"""
Telegram Client Module

Manages Telethon client connection and session persistence.
Adapted from llm-interviewer/src/telegram/client.py.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Union

import sqlite3
import asyncio
from telethon import TelegramClient
from telethon.tl.types import Channel, Chat, User
from telethon.errors import SessionPasswordNeededError

logger = logging.getLogger(__name__)


class TelegramSession:
    """
    Manages Telethon client connection and session persistence.

    Usage:
        async with TelegramSession(api_id, api_hash, phone) as session:
            group = await session.resolve_group("MyGroup")
    """

    def __init__(
        self,
        api_id: int,
        api_hash: str,
        phone: str,
        session_name: str = "moderator_bot",
        session_dir: Optional[Path] = None,
    ):
        self.api_id = api_id
        self.api_hash = api_hash
        self.phone = phone
        self.session_name = session_name
        self.session_dir = session_dir or Path.cwd() / "sessions"

        self.session_dir.mkdir(parents=True, exist_ok=True)
        session_path = self.session_dir / session_name

        self._client = TelegramClient(
            str(session_path),
            api_id,
            api_hash,
            system_version="4.16.30-vxCUSTOM",
        )
        self._connected = False
        self._me: Optional[User] = None

    @property
    def client(self) -> TelegramClient:
        """Get underlying Telethon client."""
        return self._client

    @property
    def is_connected(self) -> bool:
        return self._connected and self._client.is_connected()

    @property
    def me(self) -> Optional[User]:
        return self._me

    async def connect(self, code_callback=None, password_callback=None) -> bool:
        """Connect to Telegram and authenticate if needed."""
        for attempt in range(5):
            try:
                logger.info(f"Connecting to Telegram (phone={self.phone})...")
                await self._client.connect()
                
                if not await self._client.is_user_authorized():
                    logger.info("Not authorized, starting auth flow...")
                    if not self.phone:
                        raise ValueError("Phone number is required for authentication")
                    await self._client.send_code_request(self.phone)
                    if code_callback:
                        code = await code_callback()
                    else:
                        code = input("Enter the code you received: ")
                    if not code:
                        raise ValueError("Authentication code cannot be empty")
                    try:
                        await self._client.sign_in(self.phone, code)
                    except SessionPasswordNeededError:
                        logger.info("2FA required...")
                        if password_callback:
                            password = await password_callback()
                        else:
                            password = input("Enter your 2FA password: ")
                        if not password:
                            raise ValueError("2FA password cannot be empty")
                        await self._client.sign_in(password=password)
                
                self._connected = True
                self._me = await self._client.get_me()
                logger.info(f"Connected as {self._me.first_name} (@{self._me.username or 'no_user'})")
                return True

            except sqlite3.OperationalError as e:
                # SQLite may be locked if another process (or a zombie process) is accessing the session file.
                # Standard async practice for small SQLite DBs is a retry loop with exponential backoff.
                if "database is locked" in str(e).lower() and attempt < 4:
                    logger.warning(f"Database is locked, cleaning up and retrying in 2s ({attempt+1}/5)...")
                    try:
                        await self._client.disconnect()
                    except:
                        pass
                    await asyncio.sleep(2)
                    continue
                logger.error(f"Failed to connect (SQLite lock): {e}")
                raise
            except Exception as e:
                logger.error(f"Failed to connect: {e}")
                raise
        return False

    async def disconnect(self) -> None:
        """Disconnect from Telegram gracefully."""
        if self._client.is_connected():
            logger.info("Disconnecting from Telegram...")
            await self._client.disconnect()
            self._connected = False

    async def resolve_group(
        self, group_identifier: Union[str, int]
    ) -> Optional[Union[Chat, Channel]]:
        """
        Resolve group/channel by username or ID.

        Args:
            group_identifier: Group username (with or without @) or numeric ID
        """
        if not self.is_connected:
            raise RuntimeError("Not connected to Telegram")

        try:
            is_username = False
            if isinstance(group_identifier, str):
                if group_identifier.startswith("@"):
                    is_username = True
                    group_identifier = group_identifier.lstrip("@")

            # Try searching dialogs by title first
            if isinstance(group_identifier, str) and not is_username:
                try:
                    async for dialog in self._client.iter_dialogs():
                        if dialog.name == group_identifier and isinstance(
                            dialog.entity, (Chat, Channel)
                        ):
                            logger.info(
                                f"Found group by title: {dialog.name} (ID: {dialog.entity.id})"
                            )
                            return dialog.entity
                except Exception as e:
                    logger.warning(f"Failed to search dialogs by title: {e}")

            # Try direct resolution
            try:
                entity = await self._client.get_entity(group_identifier)
                if isinstance(entity, (Chat, Channel)):
                    logger.info(f"Resolved group: {entity.title} (ID: {entity.id})")
                    return entity
            except Exception as e:
                logger.debug(f"Direct resolution failed for '{group_identifier}': {e}")

            logger.warning(f"Could not resolve group/channel: {group_identifier}")
            return None

        except Exception as e:
            logger.error(f"Failed to resolve group '{group_identifier}': {e}")
            return None

    async def __aenter__(self) -> "TelegramSession":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.disconnect()
