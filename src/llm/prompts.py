"""
Prompt Builder Module

Loads the system prompt from a markdown file and builds LLM messages
with message context for moderation verdicts.
"""

from __future__ import annotations

import json
import logging
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List, Dict, Any

from src.llm.client import Message

logger = logging.getLogger(__name__)


@dataclass
class MessageContext:
    """A single message in the group conversation context."""
    sender_name: str
    sender_username: Optional[str]
    text: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "sender": self.sender_name + (f" (@{self.sender_username})" if self.sender_username else ""),
            "text": self.text,
        }


class ModerationPromptBuilder:
    """
    Builds moderation prompts for the LLM.

    Loads the system prompt from a markdown file and maintains
    a sliding window of recent group messages for context.
    """

    def __init__(
        self,
        system_prompt_path: str = "config/system_prompt.md",
        context_window: int = 15,
    ):
        self.system_prompt_path = Path(system_prompt_path)
        self.context_window = context_window
        self._system_prompt: Optional[str] = None
        self._context_buffer: deque[MessageContext] = deque(maxlen=context_window)

    def load_system_prompt(self) -> str:
        """Load system prompt from markdown file."""
        if not self.system_prompt_path.exists():
            raise FileNotFoundError(
                f"System prompt not found: {self.system_prompt_path}"
            )
        self._system_prompt = self.system_prompt_path.read_text(encoding="utf-8")
        logger.info(
            f"Loaded system prompt from {self.system_prompt_path} "
            f"({len(self._system_prompt)} chars)"
        )
        return self._system_prompt

    def reload_system_prompt(self) -> str:
        """Reload system prompt (hot-reload on config change)."""
        return self.load_system_prompt()

    @property
    def system_prompt(self) -> str:
        """Return the loaded system prompt text."""
        if not self._system_prompt:
            self.load_system_prompt()
        return self._system_prompt

    def add_context_message(
        self,
        sender_name: str,
        sender_username: Optional[str],
        text: str,
    ) -> None:
        """Add a message to the context window (sliding buffer)."""
        self._context_buffer.append(
            MessageContext(
                sender_name=sender_name,
                sender_username=sender_username,
                text=text,
            )
        )

    def build_messages(
        self,
        message_text: str,
        sender_name: str,
        sender_username: Optional[str] = None,
        sender_id: Optional[int] = None,
        warnings_count: int = 0,
    ) -> List[Message]:
        """
        Build the message list for an LLM moderation request.

        Args:
            message_text: The message to evaluate.
            sender_name: Display name of the sender.
            sender_username: @username of the sender.
            sender_id: Numeric Telegram user ID.
            warnings_count: Prior warning count for this user.

        Returns:
            List of Message objects ready for LLM.
        """
        if not self._system_prompt:
            self.load_system_prompt()

        # Build the user payload
        user_payload = {
            "message": message_text,
            "sender": {
                "name": sender_name,
                "username": sender_username or "",
                "id": sender_id or 0,
            },
            "context": [m.to_dict() for m in self._context_buffer],
            "warnings_count": warnings_count,
        }

        return [
            Message.system(self._system_prompt),
            Message.user(json.dumps(user_payload, ensure_ascii=False)),
        ]

    def clear_context(self) -> None:
        """Clear the context buffer."""
        self._context_buffer.clear()
