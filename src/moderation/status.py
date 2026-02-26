"""
Status Reporter Module

Maintains a live status message in the review group showing
quota usage, batch timing, and last moderation action.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Optional

from telethon import TelegramClient
from telethon.errors import MessageNotModifiedError

logger = logging.getLogger(__name__)

# Plain-text marker for searching (no markdown — Telegram strips ** from stored text)
STATUS_MARKER_SEARCH = "📊 Moderator Status"
# Display version with markdown formatting
STATUS_MARKER_DISPLAY = "📊 **Moderator Status**"
# Minimum seconds between status updates (5 minutes)
STATUS_UPDATE_INTERVAL = 300


class StatusReporter:
    """
    Manages a self-updating status message in the review group.

    On startup, searches recent messages for an existing status message
    (by marker text) and reuses it. Otherwise sends a new one.
    Always edits in-place to avoid message spam.
    """

    def __init__(
        self,
        client: TelegramClient,
        review_group,
    ):
        self.client = client
        self.review_group = review_group
        self._message_id: Optional[int] = None
        self._last_ban_time: Optional[float] = None
        self._last_batch_time: Optional[float] = None
        self._last_update_time: float = 0.0
        self._initialized = False
        self._force_update = False

    async def initialize(self) -> None:
        """
        Search recent messages in the review group for an existing
        status message so we can edit it instead of sending a new one.
        """
        if self._initialized:
            return

        try:
            me = await self.client.get_me()
            async for msg in self.client.iter_messages(
                self.review_group, limit=50
            ):
                # Use raw_text to avoid markdown parsing issues;
                # also check msg.message as fallback
                text = msg.raw_text or msg.message or ""
                if STATUS_MARKER_SEARCH in text and msg.sender_id == me.id:
                    self._message_id = msg.id
                    logger.info(
                        f"Found existing status message (id={msg.id}), "
                        "will edit it in-place"
                    )
                    break
        except Exception as e:
            logger.warning(f"Could not search for existing status message: {e}")

        self._initialized = True

    def record_ban(self) -> None:
        self._last_ban_time = time.time()
        self._force_update = True

    def record_batch(self) -> None:
        self._last_batch_time = time.time()
        self._force_update = True

    def _format_time(self, ts: Optional[float]) -> str:
        if ts is None:
            return "—"
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M UTC")

    def build_status_text(
        self,
        quota_info: dict,
        batch_queue_size: int,
    ) -> str:
        """Build the formatted status message."""
        next_batch_ts = quota_info.get("next_batch_time", 0)
        interval = quota_info.get("interval_seconds", 0)

        return (
            f"{STATUS_MARKER_DISPLAY}\n"
            "─────────────────\n"
            f"🕐 Last batch check: {self._format_time(self._last_batch_time)}\n"
            f"⏳ Next planned check: {self._format_time(next_batch_ts)}\n"
            f"📈 Interval: {interval:.0f}s\n"
            f"🔨 Last user banned: {self._format_time(self._last_ban_time)}\n"
            f"📦 OpenRouter quota: {quota_info.get('remaining', '?')}"
            f"/{quota_info.get('daily_limit', '?')} remaining "
            f"({quota_info.get('newcomer_requests', 0)} newcomer requests)\n"
            f"📬 Batch queue: {batch_queue_size} messages pending"
        )

    async def update(
        self,
        quota_info: dict,
        batch_queue_size: int,
    ) -> None:
        """Update the status message in the review group."""
        if not self.review_group:
            return

        # Find existing status message on first call
        if not self._initialized:
            await self.initialize()

        # Throttle updates: skip if last update was recent
        # (unless forced by a ban/batch event)
        now = time.time()
        if not self._force_update and self._last_update_time > 0:
            elapsed = now - self._last_update_time
            if elapsed < STATUS_UPDATE_INTERVAL:
                return

        self._force_update = False
        text = self.build_status_text(quota_info, batch_queue_size)

        # Try editing existing message
        if self._message_id:
            try:
                await self.client.edit_message(
                    self.review_group,
                    self._message_id,
                    text,
                )
                self._last_update_time = now
                return
            except MessageNotModifiedError:
                # Content hasn't changed — that's fine, skip
                self._last_update_time = now
                return
            except Exception as e:
                logger.warning(f"Could not edit status message (id={self._message_id}): {e}")
                self._message_id = None

        # Send new message only if we don't have one to edit
        try:
            msg = await self.client.send_message(self.review_group, text)
            self._message_id = msg.id
            self._last_update_time = now
            logger.info(f"Status message sent (id={msg.id})")
        except Exception as e:
            logger.error(f"Failed to send status message: {e}")
