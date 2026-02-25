"""
Telegram Gateway Module

Registers message event handlers on monitored groups and dispatches
incoming messages to the moderation engine.
"""

from __future__ import annotations

import logging
from typing import Optional, Union

from telethon import events
from telethon.tl.types import Channel, Chat

from src.telegram.client import TelegramSession
from src.moderation.engine import ModerationEngine

logger = logging.getLogger(__name__)


class Gateway:
    """
    Telegram event gateway.

    Listens for new messages in monitored groups and routes them
    through the moderation pipeline.
    """

    def __init__(
        self,
        session: TelegramSession,
        engine: ModerationEngine,
        monitored_groups: list[Union[Chat, Channel]],
    ):
        self.session = session
        self.engine = engine
        self.monitored_groups = monitored_groups
        self._group_ids: set[int] = set()

    async def start(self) -> None:
        """Register event handlers and start listening."""
        # Collect group IDs for filtering
        self._group_ids = {g.id for g in self.monitored_groups}
        group_names = [getattr(g, "title", str(g.id)) for g in self.monitored_groups]
        logger.info(f"Monitoring groups: {', '.join(group_names)}")

        client = self.session.client

        @client.on(events.NewMessage(chats=list(self._group_ids)))
        async def on_new_message(event: events.NewMessage.Event):
            """Handle every new message in monitored groups."""
            message = event.message

            # Skip messages from self
            me = self.session.me
            if me and message.sender_id == me.id:
                return

            # Skip empty messages (media-only, service messages)
            if not message.text:
                return

            try:
                await self.engine.evaluate(message, event.chat)
            except Exception as e:
                logger.error(
                    f"Moderation error for msg {message.id}: {e}", exc_info=True
                )

        logger.info("Gateway started — listening for messages.")

    async def run_until_disconnected(self) -> None:
        """Block until the Telegram client disconnects."""
        await self.session.client.run_until_disconnected()
