"""
Main entrypoint — bootstraps the moderator bot.
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
from src.moderation.engine import ModerationEngine

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


async def run(config_path: str | None = None) -> None:
    """Main async entry point."""
    config = load_config(config_path)
    setup_logging(config)

    logger.info("Starting tg-chat-moderator...")

    # Build components
    llm_client = LLMClient(
        provider=config.llm.provider,
        api_key=config.llm.api_key.get_secret_value(),
        model=config.llm.model,
        endpoint=config.llm.endpoint,
        local_model=config.llm.local_model,
        max_tokens=config.llm.max_tokens,
        temperature=config.llm.temperature,
    )

    prompt_builder = ModerationPromptBuilder(
        system_prompt_path=config.moderation.system_prompt_path,
        context_window=config.moderation.context_window_messages,
    )
    prompt_builder.load_system_prompt()

    # Connect to Telegram
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

    # Action executor
    action_executor = ActionExecutor(
        client=session.client,
        review_group=config.moderation.review_group,
    )

    # Moderation engine
    engine = ModerationEngine(
        config=config.moderation,
        llm_client=llm_client,
        prompt_builder=prompt_builder,
        action_executor=action_executor,
    )

    # Gateway
    gateway = Gateway(
        session=session,
        engine=engine,
        monitored_groups=monitored_groups,
    )

    try:
        await gateway.start()
        logger.info("Moderator bot is running. Press Ctrl+C to stop.")
        await gateway.run_until_disconnected()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        await llm_client.close()
        await session.disconnect()
        logger.info("Moderator bot stopped.")


def main(config_path: str | None = None) -> None:
    """Synchronous wrapper for run()."""
    asyncio.run(run(config_path))
