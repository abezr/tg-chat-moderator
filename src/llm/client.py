"""
LLM Client Module

Unified client for OpenRouter (cloud) and local OpenAI-compatible endpoints.
Adapted from llm-interviewer/src/llm/openrouter_client.py — simplified for
non-streaming JSON verdict responses.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from enum import Enum

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False

logger = logging.getLogger(__name__)


class MessageRole(Enum):
    """Chat message roles."""
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


@dataclass
class Message:
    """Chat message."""
    role: MessageRole
    content: str

    def to_dict(self) -> Dict[str, str]:
        return {"role": self.role.value, "content": self.content}

    @classmethod
    def system(cls, content: str) -> "Message":
        return cls(MessageRole.SYSTEM, content)

    @classmethod
    def user(cls, content: str) -> "Message":
        return cls(MessageRole.USER, content)

    @classmethod
    def assistant(cls, content: str) -> "Message":
        return cls(MessageRole.ASSISTANT, content)


@dataclass
class ChatResponse:
    """LLM chat response."""
    content: str
    model: str
    finish_reason: str
    usage: Dict[str, int] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.usage.get("total_tokens", 0)


class LLMClient:
    """
    Unified LLM client supporting OpenRouter and local endpoints.

    Both use the same OpenAI-compatible /v1/chat/completions format.
    """

    OPENROUTER_BASE = "https://openrouter.ai/api/v1"

    def __init__(
        self,
        provider: str = "openrouter",
        api_key: str = "",
        model: str = "google/gemini-2.0-flash-001",
        endpoint: str = "http://127.0.0.1:1234/v1",
        local_model: str = "gemma-3-4b",
        max_tokens: int = 500,
        temperature: float = 0.1,
        max_retries: int = 3,
    ):
        self.provider = provider
        self.api_key = api_key
        self.model = model if provider == "openrouter" else local_model
        self.endpoint = endpoint
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.max_retries = max_retries

        self._base_url = (
            self.OPENROUTER_BASE if provider == "openrouter" else endpoint
        )
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if not self._client:
            headers = {}
            if self.provider == "openrouter" and self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
                headers["HTTP-Referer"] = "https://github.com/tg-chat-moderator"
                headers["X-Title"] = "tg-chat-moderator"
            self._client = httpx.AsyncClient(
                headers=headers,
                timeout=httpx.Timeout(10.0, read=30.0),
                follow_redirects=True,
            )
        return self._client

    async def chat(
        self,
        messages: List[Message],
        model: Optional[str] = None,
    ) -> ChatResponse:
        """
        Send chat completion request.

        Returns:
            ChatResponse with the LLM's verdict.
        """
        model = model or self.model

        payload = {
            "model": model,
            "messages": [m.to_dict() for m in messages],
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }

        logger.debug(f"LLM request: {model}, {len(messages)} messages")

        last_error = None
        for attempt in range(self.max_retries):
            try:
                client = await self._get_client()
                response = await client.post(
                    f"{self._base_url}/chat/completions",
                    json=payload,
                )

                if response.status_code == 429:
                    wait_time = 2 ** attempt
                    logger.warning(f"Rate limited, waiting {wait_time}s...")
                    await asyncio.sleep(wait_time)
                    continue

                response.raise_for_status()
                data = response.json()

                choice = data["choices"][0]
                content = choice.get("message", {}).get("content", "")
                finish_reason = choice.get("finish_reason", "stop")
                usage = data.get("usage", {})

                logger.debug(
                    f"LLM response: {len(content or '')} chars, "
                    f"{usage.get('total_tokens', 0)} tokens"
                )

                return ChatResponse(
                    content=content,
                    model=data.get("model", model),
                    finish_reason=finish_reason,
                    usage=usage,
                )

            except httpx.HTTPStatusError as e:
                last_error = e
                logger.error(f"HTTP error: {e.response.status_code}")
                if e.response.status_code >= 500:
                    await asyncio.sleep(2 ** attempt)
                    continue
                else:
                    raise

            except Exception as e:
                last_error = e
                logger.error(f"Request error: {e}")
                await asyncio.sleep(2 ** attempt)

        raise RuntimeError(f"LLM request failed after {self.max_retries} retries: {last_error}")

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()
