"""
Moderation Actions Module

Telegram-level actions: warn, delete, mute, forward to review.
Adapted from telegram-scraper's forward_to_manual_review pattern.
"""

from __future__ import annotations

import logging
from typing import Optional, Union

from telethon import TelegramClient
from telethon.tl.types import Channel, Chat

logger = logging.getLogger(__name__)


class ActionExecutor:
    """
    Executes moderation actions on Telegram messages.
    """

    def __init__(
        self,
        client: TelegramClient,
        review_group: Optional[Union[str, int]] = None,
    ):
        self.client = client
        self.review_group = review_group

    async def warn(self, message, reason: str, reply_text: str = "") -> bool:
        """Reply to the message with a warning."""
        try:
            text = reply_text or f"⚠️ {reason}"
            await message.reply(text)
            logger.info(
                f"WARN: user={message.sender_id} msg={message.id} reason='{reason}'"
            )
            return True
        except Exception as e:
            logger.error(f"Failed to warn: {e}")
            return False

    async def delete(self, message, reason: str, reply_text: str = "", sender_name: str = "") -> bool:
        """Delete the message and post an explanation to the chat."""
        try:
            chat_id = message.chat_id
            await message.delete()
            if reply_text:
                notification = f"🗑 **Message Removed**\n👤 User: {sender_name}\n📝 Reason: {reply_text}"
                await self.client.send_message(chat_id, notification)
            
            logger.info(
                f"DELETE: user={message.sender_id} msg={message.id} reason='{reason}'"
            )
            return True
        except Exception as e:
            logger.error(f"Failed to delete: {e}")
            return False

    async def mute(
        self,
        chat: Union[Chat, Channel],
        user_id: int,
        reason: str,
        duration_seconds: int = 3600,
        reply_text: str = "",
        sender_name: str = "",
        message=None,
    ) -> bool:
        """Restrict user in the chat (mute) and post an explanation."""
        try:
            from telethon.tl.functions.channels import EditBannedRequest
            from telethon.tl.types import ChatBannedRights
            from datetime import datetime, timedelta, timezone

            until_date = datetime.now(timezone.utc) + timedelta(seconds=duration_seconds)
            rights = ChatBannedRights(
                until_date=until_date,
                send_messages=True,
                send_media=True,
                send_stickers=True,
                send_gifs=True,
            )

            await self.client(
                EditBannedRequest(
                    channel=chat,
                    participant=user_id,
                    banned_rights=rights,
                )
            )

            if message and reply_text:
                notification = f"🔇 **User Muted**\n👤 User: {sender_name}\n⏳ Duration: {duration_seconds//60} mins\n📝 Reason: {reply_text}"
                await self.client.send_message(message.chat_id, notification)

            logger.info(
                f"MUTE: user={user_id} duration={duration_seconds}s reason='{reason}'"
            )
            return True
        except Exception as e:
            logger.error(f"Failed to mute user {user_id}: {e}")
            return False

    async def ban(
        self,
        chat: Union[Chat, Channel],
        user_id: int,
        reason: str,
        reply_text: str = "",
        sender_name: str = "",
        message=None,
    ) -> bool:
        """Permanently ban user from the chat and post an explanation."""
        try:
            from telethon.tl.functions.channels import EditBannedRequest
            from telethon.tl.types import ChatBannedRights

            # Banned until date 0 = forever for Telegram
            rights = ChatBannedRights(
                until_date=None,
                view_messages=True,
            )

            await self.client(
                EditBannedRequest(
                    channel=chat,
                    participant=user_id,
                    banned_rights=rights,
                )
            )

            if message and reply_text:
                notification = f"🚫 **User Banned**\n👤 User: {sender_name}\n📝 Reason: {reply_text}"
                await self.client.send_message(message.chat_id, notification)

            logger.info(
                f"BAN: user={user_id} reason='{reason}'"
            )
            return True
        except Exception as e:
            logger.error(f"Failed to ban user {user_id}: {e}")
            return False


    async def forward_to_review(
        self,
        message,
        chat_title: str,
        verdict: str,
        reason: str,
    ) -> bool:
        """Forward flagged message to the review group with context."""
        if not self.review_group:
            return False

        try:
            sender_name = ""
            if message.sender:
                sender_name = getattr(message.sender, "first_name", "") or ""
                username = getattr(message.sender, "username", "")
                if username:
                    sender_name += f" (@{username})"

            context_text = (
                f"🔍 **Moderation Flag**\n"
                f"📍 Group: {chat_title}\n"
                f"👤 Sender: {sender_name} (ID: {message.sender_id})\n"
                f"⚖️ Verdict: `{verdict}`\n"
                f"📝 Reason: {reason}\n"
                f"────────────────\n"
                f"{message.text}"
            )

            await self.client.send_message(self.review_group, context_text)
            logger.info(f"Forwarded msg {message.id} to review group")
            return True
        except Exception as e:
            logger.error(f"Failed to forward to review: {e}")
            return False
