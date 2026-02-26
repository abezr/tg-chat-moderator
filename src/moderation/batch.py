"""
Batch Queue Module

Accumulates messages from regular (non-newcomer) users and flushes
them to OpenRouter as a single batched request. Triggers on either
the quota interval timer or when accumulated tokens hit the limit.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Optional, Union, Callable, Awaitable

from telethon.tl.types import Channel, Chat

logger = logging.getLogger(__name__)


@dataclass
class QueuedMessage:
    """A message waiting in the batch queue."""
    payload: dict          # JSON payload for LLM
    message: object        # Telethon Message
    chat: Union[Chat, Channel]
    sender_name: str
    user_id: int
    enqueued_at: float = field(default_factory=time.time)

    @property
    def estimated_tokens(self) -> int:
        """Rough token estimate (~4 chars per token)."""
        text = self.payload.get("message", "")
        return max(1, len(text) // 4)


class BatchQueue:
    """
    Accumulates messages and flushes them to OpenRouter as batches.

    Flush triggers:
      1. Quota interval timer fires
      2. Accumulated tokens >= max_batch_tokens
    """

    def __init__(
        self,
        max_batch_tokens: int = 3000,
        on_flush: Optional[Callable[["BatchQueue"], Awaitable[None]]] = None,
        on_tick: Optional[Callable[[], Awaitable[None]]] = None,
    ):
        self.max_batch_tokens = max_batch_tokens
        self._queue: list[QueuedMessage] = []
        self._lock = asyncio.Lock()
        self._flush_event = asyncio.Event()
        self._on_flush = on_flush
        self._on_tick = on_tick

    async def add(
        self,
        payload: dict,
        message,
        chat: Union[Chat, Channel],
        sender_name: str,
        user_id: int,
    ) -> None:
        """Add a message to the batch queue."""
        async with self._lock:
            item = QueuedMessage(
                payload=payload,
                message=message,
                chat=chat,
                sender_name=sender_name,
                user_id=user_id,
            )
            self._queue.append(item)
            total_tokens = self.estimated_tokens

            logger.debug(
                f"Batch queue: +1 msg (total={len(self._queue)}, "
                f"~{total_tokens} tokens)"
            )

            # Trigger flush if token limit reached
            if total_tokens >= self.max_batch_tokens:
                logger.info(
                    f"Batch token limit reached ({total_tokens} >= "
                    f"{self.max_batch_tokens}), triggering flush"
                )
                self._flush_event.set()

    async def drain(self) -> list[QueuedMessage]:
        """Remove and return all queued messages."""
        async with self._lock:
            items = list(self._queue)
            self._queue.clear()
            return items

    @property
    def estimated_tokens(self) -> int:
        """Total estimated tokens in queue."""
        return sum(m.estimated_tokens for m in self._queue)

    @property
    def size(self) -> int:
        return len(self._queue)

    @property
    def is_empty(self) -> bool:
        return len(self._queue) == 0

    def trigger_flush(self) -> None:
        """Manually trigger a flush (e.g. from quota timer)."""
        self._flush_event.set()

    async def run_loop(
        self,
        get_interval: Callable[[], float],
        stop_event: Optional[asyncio.Event] = None,
    ) -> None:
        """
        Background loop that flushes the queue on interval or token limit.

        Args:
            get_interval: Callable that returns seconds until next flush.
            stop_event: Set this to stop the loop.
        """
        logger.info("Batch queue flush loop started")
        while True:
            if stop_event and stop_event.is_set():
                break

            # Wait for either: interval timer OR token-limit trigger
            interval = get_interval()
            try:
                await asyncio.wait_for(
                    self._flush_event.wait(),
                    timeout=max(1.0, interval),
                )
            except asyncio.TimeoutError:
                pass  # Timer fired

            self._flush_event.clear()

            # Tick callback — update status even when queue is empty
            if self._on_tick:
                try:
                    await self._on_tick()
                except Exception as e:
                    logger.debug(f"Tick callback error: {e}")

            if self.is_empty:
                continue

            if self._on_flush:
                try:
                    await self._on_flush(self)
                except Exception as e:
                    logger.error(f"Batch flush error: {e}", exc_info=True)

    @staticmethod
    def build_batch_prompt(items: list[QueuedMessage]) -> str:
        """
        Build a single user message containing multiple messages for batch evaluation.

        Returns JSON array of message payloads.
        """
        payloads = []
        for i, item in enumerate(items):
            payloads.append({
                "index": i,
                "message_id": item.message.id if hasattr(item.message, 'id') else 0,
                **item.payload,
            })
        return json.dumps(payloads, ensure_ascii=False)

    @staticmethod
    def parse_batch_verdicts(raw: str, expected_count: int) -> list[dict]:
        """
        Parse an array of verdicts from the LLM batch response.

        Handles: JSON array, markdown fences, individual objects.
        """
        cleaned = raw.strip()

        # Strip markdown fences
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            cleaned = "\n".join(lines).strip()

        # Try parsing as JSON array
        try:
            result = json.loads(cleaned)
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

        # Try extracting a JSON array
        match = re.search(r'\[.*\]', cleaned, re.DOTALL)
        if match:
            try:
                result = json.loads(match.group())
                if isinstance(result, list):
                    return result
            except json.JSONDecodeError:
                pass

        # Try extracting individual JSON objects
        objects = re.findall(r'\{[^{}]+\}', cleaned)
        if objects:
            verdicts = []
            for obj_str in objects:
                try:
                    verdicts.append(json.loads(obj_str))
                except json.JSONDecodeError:
                    continue
            if verdicts:
                return verdicts

        logger.warning(f"Failed to parse batch verdicts, returning all 'ok'. Raw response: {raw}")
        return [
            {"verdict": "ok", "reason": "unparseable batch response", "reply": ""}
            for _ in range(expected_count)
        ]
