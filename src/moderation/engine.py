"""
Moderation Engine Module

Core moderation pipeline:
  1. Pre-filter (keyword/regex blocklist — instant, no LLM)
  2. LLM analysis (sends message + context, parses JSON verdict)
  3. Action dispatch (warn/delete/mute/forward)
  4. User cooldown (rate-limits actions per user)

Keyword matching adapted from telegram-scraper.check_keyword_match.
Cooldown pattern adapted from telegram-scraper.can_send_reply.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections import defaultdict
from typing import Optional, Union

from telethon.tl.types import Channel, Chat

from src.config import ModerationConfig
from src.llm.client import LLMClient
from src.llm.prompts import ModerationPromptBuilder
from src.moderation.actions import ActionExecutor

logger = logging.getLogger(__name__)


class PreFilter:
    """
    Fast pre-filter: keyword and regex blocklist.
    Messages matching here are actioned instantly without an LLM call.
    """

    def __init__(
        self,
        keywords: list[str] | None = None,
        regex_patterns: list[str] | None = None,
    ):
        self.keywords = [k.lower() for k in (keywords or [])]
        self.compiled_regex = []
        for pattern in (regex_patterns or []):
            try:
                self.compiled_regex.append(re.compile(pattern, re.IGNORECASE))
            except re.error as e:
                logger.warning(f"Invalid regex pattern '{pattern}': {e}")

    def check(self, text: str) -> Optional[str]:
        """
        Check if message matches any pre-filter rule.

        Returns:
            Matched keyword/pattern string, or None if clean.
        """
        text_lower = text.lower()

        for keyword in self.keywords:
            if keyword in text_lower:
                return f"keyword:{keyword}"

        for pattern in self.compiled_regex:
            if pattern.search(text):
                return f"regex:{pattern.pattern}"

        return None


class ModerationEngine:
    """
    Core moderation pipeline.

    Pre-filter → LLM analysis → verdict parsing → action dispatch.
    """

    def __init__(
        self,
        config: ModerationConfig,
        llm_client: LLMClient,
        prompt_builder: ModerationPromptBuilder,
        action_executor: ActionExecutor,
    ):
        self.config = config
        self.llm = llm_client
        self.prompts = prompt_builder
        self.actions = action_executor

        # Pre-filter
        self.pre_filter = PreFilter(
            keywords=config.hard_ban_keywords,
            regex_patterns=config.hard_ban_regex,
        )

        # Per-user cooldown tracking
        self._user_last_action: dict[int, float] = defaultdict(float)

        # Per-user warning counter (in-memory; reset on restart)
        self._user_warnings: dict[int, int] = defaultdict(int)

    def _is_on_cooldown(self, user_id: int) -> bool:
        """Check if user is on moderation cooldown."""
        if self.config.user_cooldown_seconds <= 0:
            return False
        elapsed = time.time() - self._user_last_action[user_id]
        return elapsed < self.config.user_cooldown_seconds

    def _record_action(self, user_id: int) -> None:
        """Record that a moderation action was taken on a user."""
        self._user_last_action[user_id] = time.time()

    async def evaluate(
        self,
        message,
        chat: Union[Chat, Channel],
    ) -> None:
        """
        Evaluate a message through the moderation pipeline.

        Args:
            message: Telethon Message object.
            chat: The chat/channel the message was sent in.
        """
        user_id = message.sender_id
        text = message.text or ""
        chat_title = getattr(chat, "title", str(chat.id))

        # 0. Extract sender info
        sender_name = ""
        sender_username = None
        if message.sender:
            sender_name = getattr(message.sender, "first_name", "") or ""
            last = getattr(message.sender, "last_name", "")
            if last:
                sender_name += f" {last}"
            sender_username = getattr(message.sender, "username", None)

        # Always add to context window (even if not moderated)
        self.prompts.add_context_message(
            sender_name=sender_name or "Unknown",
            sender_username=sender_username,
            text=text,
        )

        # 1. Cooldown check
        if self._is_on_cooldown(user_id):
            logger.debug(f"User {user_id} on cooldown, skipping moderation")
            return

        # 2. Pre-filter (instant, no LLM)
        pre_match = self.pre_filter.check(text)
        if pre_match:
            logger.info(f"Pre-filter hit: {pre_match} | user={user_id} msg={message.id}")
            self._user_warnings[user_id] += 1
            self._record_action(user_id)

            await self.actions.delete(
                message,
                reason=f"Pre-filter: {pre_match}",
                reply_text="🚫 This message was removed by auto-moderator.",
                sender_name=sender_name or "Unknown",
            )
            await self.actions.forward_to_review(
                message,
                chat_title=chat_title,
                verdict="delete (pre-filter)",
                reason=pre_match,
            )
            return

        # 3. LLM analysis
        try:
            warnings_count = self._user_warnings.get(user_id, 0)
            messages = self.prompts.build_messages(
                message_text=text,
                sender_name=sender_name or "Unknown",
                sender_username=sender_username,
                sender_id=user_id,
                warnings_count=warnings_count,
            )

            response = await self.llm.chat(messages)
            verdict = self._parse_verdict(response.content)

        except Exception as e:
            logger.error(f"LLM analysis failed for msg {message.id}: {e}")
            # On LLM failure, do nothing (fail-open)
            return

        # 4. Act on verdict
        if verdict["verdict"] == "ok":
            logger.debug(f"OK: msg={message.id} user={user_id}")
            return

        self._record_action(user_id)
        action = verdict["verdict"]
        reason = verdict.get("reason", "")
        reply_text = verdict.get("reply", "")

        if action == "warn":
            self._user_warnings[user_id] += 1
            await self.actions.warn(message, reason=reason, reply_text=reply_text)

        elif action == "delete":
            self._user_warnings[user_id] += 1
            await self.actions.delete(
                message,
                reason=reason,
                reply_text=reply_text,
                sender_name=sender_name or "Unknown",
            )

        elif action == "mute":
            self._user_warnings[user_id] += 1
            await self.actions.mute(
                chat=chat,
                user_id=user_id,
                reason=reason,
                duration_seconds=self.config.mute_duration_seconds,
                message=message,
                reply_text=reply_text,
                sender_name=sender_name or "Unknown",
            )

        elif action == "ban":
            self._user_warnings[user_id] += 1
            await self.actions.ban(
                chat=chat,
                user_id=user_id,
                reason=reason,
                message=message,
                reply_text=reply_text,
                sender_name=sender_name or "Unknown",
            )

        # Forward non-ok verdicts to review
        if self.actions.review_group:
            await self.actions.forward_to_review(
                message,
                chat_title=chat_title,
                verdict=action,
                reason=reason,
            )

    @staticmethod
    def _parse_verdict(raw: str) -> dict:
        """
        Parse the LLM's JSON verdict response.

        Handles common LLM quirks: markdown fences, extra text.
        """
        # Strip markdown code fences
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            # Remove first and last lines (fences)
            lines = cleaned.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            cleaned = "\n".join(lines).strip()

        # Try to find JSON object in the response
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        # Try to extract JSON from surrounding text
        match = re.search(r'\{[^}]+\}', cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

        logger.warning(f"Failed to parse LLM verdict, treating as 'ok': {raw[:200]}")
        return {"verdict": "ok", "reason": "unparseable LLM response", "reply": ""}
