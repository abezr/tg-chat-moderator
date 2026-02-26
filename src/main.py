"""
Main entrypoint — bootstraps the moderator bot.

Wires together: config, Telegram, LLM, newcomer tracker,
dedup cache, quota manager, batch queue, status reporter,
moderation engine, and gateway.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from src.config import load_config, AppConfig
from src.telegram.client import TelegramSession
from src.telegram.gateway import Gateway
from src.llm.client import LLMClient
from src.llm.prompts import ModerationPromptBuilder
from src.moderation.actions import ActionExecutor
from src.moderation.batch import BatchQueue
from src.moderation.cache import ProcessedCache
from src.moderation.engine import ModerationEngine
from src.moderation.newcomer import NewcomerTracker
from src.moderation.quota import QuotaManager
from src.moderation.status import StatusReporter

logger = logging.getLogger(__name__)


def setup_logging(config: AppConfig) -> None:
    """Configure logging from config."""
    log_cfg = config.logging
    handlers = [logging.StreamHandler()]

    if log_cfg.file:
        log_path = Path(log_cfg.file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))

    logging.basicConfig(
        level=getattr(logging, log_cfg.level, logging.INFO),
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        handlers=handlers,
    )


async def _warmup_loop(
    llm_client: LLMClient,
    system_prompt: str,
    interval_minutes: int,
    stop_event: asyncio.Event,
) -> None:
    """Periodically warm up the local LLM to keep system prompt in KV-cache."""
    while not stop_event.is_set():
        await llm_client.warm_up_local(system_prompt)
        try:
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=interval_minutes * 60,
            )
        except asyncio.TimeoutError:
            pass


async def run(config_path: str | None = None) -> None:
    """Main async entry point."""
    config = load_config(config_path)
    setup_logging(config)

    logger.info("Starting tg-chat-moderator...")

    # --- Build LLM client ---
    llm_client = LLMClient(
        provider=config.llm.provider,
        api_key=config.llm.api_key.get_secret_value(),
        model=config.llm.model,
        endpoint=config.llm.endpoint,
        local_model=config.llm.local_model,
        max_tokens=config.llm.max_tokens,
        temperature=config.llm.temperature,
    )

    # --- Build prompt builder ---
    prompt_builder = ModerationPromptBuilder(
        system_prompt_path=config.moderation.system_prompt_path,
        context_window=config.moderation.context_window_messages,
    )
    prompt_builder.load_system_prompt()

    # --- Connect to Telegram ---
    session = TelegramSession(
        api_id=config.telegram.api_id,
        api_hash=config.telegram.api_hash,
        phone=config.telegram.phone,
        session_name=config.telegram.session_name,
    )
    await session.connect()

    # Resolve monitored groups
    monitored_groups = []
    for group_id in config.moderation.monitored_groups:
        group = await session.resolve_group(group_id)
        if group:
            monitored_groups.append(group)
        else:
            logger.warning(f"Could not resolve group: {group_id}")

    if not monitored_groups:
        logger.error("No monitored groups could be resolved. Exiting.")
        await session.disconnect()
        return

    # --- Resolve review group ---
    review_group_entity = None
    if config.moderation.review_group:
        review_group_entity = await session.resolve_group(
            config.moderation.review_group
        )
        if not review_group_entity:
            logger.warning(
                f"Could not resolve review_group: {config.moderation.review_group}. "
                "Review forwarding and status updates will be disabled."
            )

    # --- Build moderation components ---
    action_executor = ActionExecutor(
        client=session.client,
        review_group=review_group_entity,
    )

    newcomer_tracker = NewcomerTracker(
        window_hours=config.moderation.newcomer_window_hours,
        persist_path="data/newcomers.json",
    )

    # Pre-populate newcomer tracker with existing group members
    # so they're routed to batch queue (OpenRouter), not instant local LLM
    for group in monitored_groups:
        try:
            member_ids = []
            async for participant in session.client.iter_participants(group, limit=5000):
                member_ids.append(participant.id)
            if member_ids:
                newcomer_tracker.bulk_register(member_ids)
                logger.info(
                    f"Pre-registered {len(member_ids)} members from "
                    f"{getattr(group, 'title', group)}"
                )
        except Exception as e:
            logger.warning(f"Could not fetch participants for pre-registration: {e}")

    processed_cache = ProcessedCache(max_size=10000)

    quota_manager = QuotaManager(
        daily_limit=config.quota.daily_limit,
        persist_path="data/quota.json",
    )

    # Status reporter (uses review group)
    status_reporter = None
    if review_group_entity:
        status_reporter = StatusReporter(
            client=session.client,
            review_group=review_group_entity,
        )

    # Batch queue (will be wired to engine's flush handler)
    batch_queue = BatchQueue(
        max_batch_tokens=config.moderation.batch_max_tokens,
    )

    # --- Build engine ---
    engine = ModerationEngine(
        config=config.moderation,
        llm_client=llm_client,
        prompt_builder=prompt_builder,
        action_executor=action_executor,
        newcomer_tracker=newcomer_tracker,
        processed_cache=processed_cache,
        quota_manager=quota_manager,
        batch_queue=batch_queue,
        status_reporter=status_reporter,
    )

    # Wire batch flush callback
    batch_queue._on_flush = engine.handle_batch_flush

    # Wire tick callback for periodic status updates
    if status_reporter:
        async def _on_tick():
            await status_reporter.update(
                quota_manager.status_dict(),
                batch_queue.size,
            )
        batch_queue._on_tick = _on_tick

    # --- Gateway ---
    gateway = Gateway(
        session=session,
        engine=engine,
        monitored_groups=monitored_groups,
    )

    # --- Start everything ---
    stop_event = asyncio.Event()

    try:
        await gateway.start()

        # Start background tasks
        tasks: list[asyncio.Task] = []

        # Warm-up loop for local LLM
        if llm_client.has_local:
            tasks.append(asyncio.create_task(
                _warmup_loop(
                    llm_client,
                    prompt_builder.system_prompt,
                    config.quota.warmup_interval_minutes,
                    stop_event,
                )
            ))

        # Batch flush loop
        if llm_client.has_openrouter:
            tasks.append(asyncio.create_task(
                batch_queue.run_loop(
                    get_interval=lambda: quota_manager.interval_seconds,
                    stop_event=stop_event,
                )
            ))

        # Initial status update (also finds existing pinned message)
        if status_reporter:
            await status_reporter.update(
                quota_manager.status_dict(),
                batch_queue.size,
            )

        logger.info("Moderator bot is running. Press Ctrl+C to stop.")
        await gateway.run_until_disconnected()

    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        stop_event.set()
        newcomer_tracker.save()
        quota_manager.save()
        await llm_client.close()
        await session.disconnect()
        logger.info("Moderator bot stopped.")


def main(config_path: str | None = None) -> None:
    """Synchronous wrapper for run()."""
    asyncio.run(run(config_path))
