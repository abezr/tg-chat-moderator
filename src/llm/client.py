"""
LLM Client Module

Unified client for OpenRouter (cloud) and local OpenAI-compatible endpoints.
Supports failover: when provider="both", tries OpenRouter first and
falls back to local on rate-limit (429) or connection errors.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Literal
from enum import Enum

import httpx

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
    provider_used: str = ""
    usage: Dict[str, int] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.usage.get("total_tokens", 0)


class _Endpoint:
    """Single LLM endpoint (OpenRouter or local)."""

    def __init__(
        self,
        name: str,
        base_url: str,
        model: str,
        api_key: str = "",
        max_tokens: int = 500,
        temperature: float = 0.1,
    ):
        self.name = name
        self.base_url = base_url
        self.model = model
        self.api_key = api_key
        self.max_tokens = max_tokens
        self.temperature = temperature

        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
            headers["HTTP-Referer"] = "https://github.com/tg-chat-moderator"
            headers["X-Title"] = "tg-chat-moderator"

        self.client = httpx.AsyncClient(
            headers=headers,
            timeout=httpx.Timeout(10.0, read=60.0),
            follow_redirects=True,
        )

    async def chat(self, messages: List[Message]) -> ChatResponse:
        """Send request to this endpoint. Raises on failure."""
        payload = {
            "model": self.model,
            "messages": [m.to_dict() for m in messages],
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }

        response = await self.client.post(
            f"{self.base_url}/chat/completions",
            json=payload,
        )

        if response.status_code == 429:
            raise RateLimitError(f"{self.name}: 429 Too Many Requests")

        response.raise_for_status()
        data = response.json()

        choice = data["choices"][0]
        return ChatResponse(
            content=choice.get("message", {}).get("content", ""),
            model=data.get("model", self.model),
            finish_reason=choice.get("finish_reason", "stop"),
            provider_used=self.name,
            usage=data.get("usage", {}),
        )

    async def warm_up(self, system_prompt: str) -> bool:
        """
        Warm up the LLM by sending the system prompt with a trivial user message.

        This pre-fills the KV-cache so that real requests are faster.
        Only useful for local LLMs.
        """
        try:
            logger.info(f"Warming up {self.name} with system prompt...")
            payload = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": '{"message":"ping","sender":{"name":"system","username":"","id":0},"context":[],"warnings_count":0}'},
                ],
                "max_tokens": 20,
                "temperature": 0.0,
            }
            response = await self.client.post(
                f"{self.base_url}/chat/completions",
                json=payload,
            )
            if response.status_code == 200:
                logger.info(f"✅ {self.name} warmed up (system prompt cached)")
                return True
            else:
                logger.warning(f"Warm-up got status {response.status_code}")
                return False
        except Exception as e:
            logger.warning(f"Warm-up failed for {self.name}: {e}")
            return False

    async def close(self):
        await self.client.aclose()


class RateLimitError(Exception):
    pass


class LLMClient:
    """
    LLM client with failover support.

    provider="openrouter" → cloud only
    provider="local"      → local only
    provider="both"       → OpenRouter first, fallback to local on 429/error
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
        max_retries: int = 2,
    ):
        self.provider = provider
        self.max_retries = max_retries

        # Build endpoint list
        self._endpoints: list[_Endpoint] = []

        if provider in ("openrouter", "both"):
            self._endpoints.append(_Endpoint(
                name="openrouter",
                base_url=self.OPENROUTER_BASE,
                model=model,
                api_key=api_key,
                max_tokens=max_tokens,
                temperature=temperature,
            ))

        if provider in ("local", "both"):
            self._endpoints.append(_Endpoint(
                name="local",
                base_url=endpoint,
                model=local_model,
                max_tokens=max_tokens,
                temperature=temperature,
            ))

        if not self._endpoints:
            raise ValueError(f"Unknown provider: {provider}")

        logger.info(
            f"LLM client initialized: provider={provider}, "
            f"endpoints={[e.name for e in self._endpoints]}"
        )

    async def chat(self, messages: List[Message]) -> ChatResponse:
        """
        Send chat completion request with failover.

        Tries each endpoint in order. On rate-limit or connection error,
        falls back to the next endpoint.
        """
        last_error = None

        for ep in self._endpoints:
            for attempt in range(self.max_retries):
                try:
                    logger.debug(f"Trying {ep.name} (attempt {attempt + 1})")
                    response = await ep.chat(messages)
                    logger.info(
                        f"LLM [{ep.name}]: {len(response.content)} chars, "
                        f"{response.total_tokens} tokens"
                    )
                    return response

                except RateLimitError as e:
                    last_error = e
                    logger.warning(f"{ep.name}: rate limited, "
                                   f"{'retrying' if attempt < self.max_retries - 1 else 'failing over'}...")
                    if attempt < self.max_retries - 1:
                        await asyncio.sleep(2 ** attempt)
                    else:
                        break  # Move to next endpoint

                except (httpx.ConnectError, httpx.ConnectTimeout) as e:
                    last_error = e
                    logger.warning(f"{ep.name}: connection error ({e}), failing over...")
                    break  # Move to next endpoint immediately

                except httpx.HTTPStatusError as e:
                    last_error = e
                    if e.response.status_code >= 500:
                        logger.warning(f"{ep.name}: server error {e.response.status_code}")
                        await asyncio.sleep(2 ** attempt)
                    else:
                        raise  # 4xx (non-429) are real errors

                except Exception as e:
                    last_error = e
                    logger.error(f"{ep.name}: unexpected error: {e}")
                    await asyncio.sleep(1)

        raise RuntimeError(
            f"All LLM endpoints failed after exhausting retries: {last_error}"
        )

    async def chat_local(self, messages: List[Message]) -> ChatResponse:
        """Send request directly to local endpoint only (for newcomer fast-path)."""
        ep = self._get_endpoint("local")
        if not ep:
            raise RuntimeError("No local endpoint configured")
        return await ep.chat(messages)

    async def chat_openrouter(self, messages: List[Message]) -> ChatResponse:
        """Send request directly to OpenRouter only (for batch flush)."""
        ep = self._get_endpoint("openrouter")
        if not ep:
            raise RuntimeError("No OpenRouter endpoint configured")
        return await ep.chat(messages)

    def _get_endpoint(self, name: str) -> Optional[_Endpoint]:
        """Get endpoint by name."""
        for ep in self._endpoints:
            if ep.name == name:
                return ep
        return None

    @property
    def has_local(self) -> bool:
        return self._get_endpoint("local") is not None

    @property
    def has_openrouter(self) -> bool:
        return self._get_endpoint("openrouter") is not None

    async def warm_up_local(self, system_prompt: str) -> bool:
        """Warm up local LLM with system prompt."""
        ep = self._get_endpoint("local")
        if ep:
            return await ep.warm_up(system_prompt)
        return False

    async def close(self) -> None:
        for ep in self._endpoints:
            await ep.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()
