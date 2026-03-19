"""
Moderation Engine Module

Core moderation pipeline with dual-path routing:
  - Newcomers → instant local LLM evaluation
  - Regulars  → batch queue → OpenRouter on interval

Pre-filter → dedup → newcomer check → route → action dispatch.
"""

from __future__ import annotations

import json
import logging
import re
import httpx
import time
from collections import defaultdict
from typing import Optional, Union

from telethon.tl.types import Channel, Chat

from src.config import ModerationConfig
from src.llm.client import LLMClient
from src.llm.prompts import ModerationPromptBuilder
from src.moderation.actions import ActionExecutor
from src.moderation.batch import BatchQueue
from src.moderation.cache import ProcessedCache
from src.moderation.newcomer import NewcomerTracker
from src.moderation.quota import QuotaManager
from src.moderation.reputation import UserReputation
from src.moderation.reports import ReportGenerator
from src.moderation.status import StatusReporter

logger = logging.getLogger(__name__)

# Call-to-action patterns (always ban) - these match imperative/encouraging forms only, not news stories
VIOLENCE_PATTERNS = [
    r"надо\s+убива",       # "надо убивать" - we should kill
    r"нужно\s+убива",      # "нужно убить" - need to kill
    r"давайте\s+убива",    # "давайте убьем" - let's kill
    r"убивать\s+мерзавц",  # "убивать мерзавцев" - kill the bastards
    r"убить\s+мерзавц",    # "убить мерзавцев" - kill the bastards
    r"подсократить\s+поголов",  # "подсократить поголовье" - reduce population
    r"убивать\s+люд",      # "убивать людей" - kill people
    r"убью\b",              # "убью" (I will kill) - direct threat
    r"убьём\b",             # "убьём" (we will kill) - group threat
    r"ножом\s+по",         # "ножом по" - knife attack
    r"нож\s+по",           # "нож по" - knife attack
]

# Quote/report indicators - if present, the message is reporting what someone else said (not a call to action)
# These patterns SKIP the violence check - they indicate the writer is REPORTING, not advocating
QUOTE_INDICATORS = [
    r"сказал\b", r"сказала\b", r"говорят\b", r"по\s+словам\b",
    r"сообщает\b", r"сообщил\b", r"сообщила\b", r"пишет\b", r"пишут\b",
    r"утверждает\b", r"заявил\b", r"заявила\b", r"заявили\b",
    r"цитирует\b", r"цитата\b", r"引用\b",  # quote in Chinese too
    r'"[^"]+"',  # "quoted text"
]


# Compile patterns at module level for efficiency
VIOLENCE_COMPILED_PATTERNS = [re.compile(pattern, re.IGNORECASE) for pattern in VIOLENCE_PATTERNS]
QUOTE_COMPILED_PATTERNS = [re.compile(pattern, re.IGNORECASE) for pattern in QUOTE_INDICATORS]


def _check_violence_keyword_filter(text: str) -> dict | None:
    """Pre-filter for violent call-to-action content - runs BEFORE LLM.
    
    Uses regex patterns to distinguish between:
    - CALL TO ACTION (ban): "надо убивать мерзавцев" - encouraging/imperative forms
    - NEWS STORY (allow): "он убил преступника" - past tense, news reporting
    - REPORTING (allow): "Путин сказал, что надо убивать..." - quoting what someone said
    - QUOTED VIOLENCE (allow): 'Он заявил: "надо убивать"' - violence inside quotes
    
    Returns:
    - dict with verdict "ok" and reason "quoted_speech" or "reported_speech" when content is allowed
    - dict with verdict "ban" when violence call-to-action detected
    - None when no violence patterns found at all
    """
    # Find all violence pattern matches with their positions
    violence_matches = []
    for pattern in VIOLENCE_COMPILED_PATTERNS:
        match = pattern.search(text)
        if match:
            violence_matches.append((match.start(), match.end(), match.group()))
    
    if not violence_matches:
        # No violence patterns found - allow (no filter needed)
        return None
    
    # Check if violence is inside quotation marks - if so, it's a quote, not a call to action
    # Find all quoted sections (text between "...")
    quote_pattern = re.compile(r'"([^"]+)"', re.IGNORECASE)
    quoted_sections = []
    for match in quote_pattern.finditer(text):
        quoted_sections.append((match.start(), match.end(), match.group(1)))
    
    # Check if any violence match is inside a quoted section
    for v_start, v_end, v_group in violence_matches:
        is_inside_quote = False
        for q_start, q_end, q_content in quoted_sections:
            # Check if the violence match is within the quote boundaries
            if q_start < v_start and v_end < q_end:
                is_inside_quote = True
                break
        if is_inside_quote:
            # Violence is inside quotes - this is a quote/report, not a call to action
            # Return "ok" verdict to SKIP LLM evaluation entirely
            logger.info(f"Violence inside quotes detected - allowing (skip LLM): {text[:50]}...")
            return {"verdict": "ok", "reason": "quoted_speech", "reply": ""}
    
    # Find all quote indicator matches with their positions (for non-quoted violence)
    quote_matches = []
    for pattern in QUOTE_COMPILED_PATTERNS:
        for match in pattern.finditer(text):
            quote_matches.append(match.start())
    
    # If there's a quote indicator BEFORE the first violence match, it's reporting - allow
    if quote_matches:
        min_violence_pos = min(vm[0] for vm in violence_matches)
        min_quote_pos = min(quote_matches)
        if min_quote_pos < min_violence_pos:
            # Quote indicator appears before violence - this is a story/report
            # Return "ok" verdict to SKIP LLM evaluation entirely
            logger.info(f"Quote indicator before violence - allowing (skip LLM): {text[:50]}...")
            return {"verdict": "ok", "reason": "reported_speech", "reply": ""}
    
    # No quote indicator before violence, or no quote indicator at all - it's a call to action - ban
    return {
        "verdict": "ban",
        "reason": f"VIOLENCE_PATTERN_DETECTED: {text[:50]}...",
        "reply": "Ваше повідомлення містить заклики до насильства. Це неприйнятно."
    }


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
        """Check if message matches any pre-filter rule."""
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
    Core moderation pipeline with dual-path routing.

    Newcomers → instant local LLM
    Regulars  → batch queue → OpenRouter
    """

    def __init__(
        self,
        config: ModerationConfig,
        llm_client: LLMClient,
        prompt_builder: ModerationPromptBuilder,
        action_executor: ActionExecutor,
        newcomer_tracker: NewcomerTracker,
        reputation: UserReputation,
        report_generator: ReportGenerator,
        processed_cache: ProcessedCache,
        quota_manager: QuotaManager,
        batch_queue: BatchQueue,
        status_reporter: Optional[StatusReporter] = None,
        admin_ids: Optional[set[int]] = None,
    ):
        self.config = config
        self.llm = llm_client
        self.prompts = prompt_builder
        self.actions = action_executor
        self.newcomer = newcomer_tracker
        self.reputation = reputation
        self.reports = report_generator
        self.cache = processed_cache
        self.quota = quota_manager
        self.batch = batch_queue
        self.status = status_reporter
        self.admin_ids = admin_ids or set()

        # Pre-filter
        self.pre_filter = PreFilter(
            keywords=config.hard_ban_keywords,
            regex_patterns=config.hard_ban_regex,
        )

        # Dry run mode
        self.dry_run = config.dry_run
        if self.dry_run:
            logger.info("🔇 DRY RUN MODE — no actions will be taken, only forwarding to review")

        # Per-user cooldown tracking
        self._user_last_action: dict[int, float] = defaultdict(float)
        # Per-user warning counter (in-memory; reset on restart)
        self._user_warnings: dict[int, int] = defaultdict(int)

    def _is_on_cooldown(self, user_id: int) -> bool:
        if self.config.user_cooldown_seconds <= 0:
            return False
        elapsed = time.time() - self._user_last_action[user_id]
        return elapsed < self.config.user_cooldown_seconds

    def _record_action(self, user_id: int) -> None:
        self._user_last_action[user_id] = time.time()

    async def evaluate(
        self,
        message,
        chat: Union[Chat, Channel],
    ) -> None:
        """
        Evaluate a message through the dual-path moderation pipeline.
        """
        user_id = message.sender_id
        text = message.text or ""
        chat_id = getattr(chat, "id", getattr(message, "chat_id", 0))
        chat_title = getattr(chat, "title", str(chat_id))

        # Skip service messages and anonymous channel posts
        if user_id is None:
            return

        # Record activity for reputation tracking
        self.reputation.update_activity(user_id)

        # Skip admin users (unless we are in a test group, where we WANT to test the bot)
        is_test_group = "test" in str(chat_title).lower() or abs(int(chat_id)) == 5139770999 or abs(int(chat_id)) == 1005139770999
        if user_id in self.admin_ids and not is_test_group:
            logger.info(f"Skipping admin user: {user_id} in {chat_title}")
            return

        # 0. Extract sender info
        sender_name = ""
        sender_username = None
        if message.sender:
            sender_name = getattr(message.sender, "first_name", "") or ""
            last = getattr(message.sender, "last_name", "")
            if last:
                sender_name += f" {last}"
            sender_username = getattr(message.sender, "username", None)

        # Always add to context window
        self.prompts.add_context_message(
            sender_name=sender_name or "Unknown",
            sender_username=sender_username,
            text=text,
        )

        # 1. Dedup check
        if self.cache.is_processed(chat_id, message.id):
            return
        self.cache.mark_processed(chat_id, message.id)

        # 2. Register user for newcomer tracking
        self.newcomer.register_user(user_id)

        # 3. Cooldown check
        if self._is_on_cooldown(user_id):
            logger.info(f"User {user_id} on cooldown, skipping")
            return

        # 4. Pre-filter (instant, no LLM)
        pre_match = self.pre_filter.check(text)
        if pre_match:
            logger.info(f"Pre-filter hit: {pre_match} | user={user_id}")
            self._record_action(user_id)

            if self.dry_run:
                logger.info(f"🔇 DRY RUN: would delete msg={message.id} (pre-filter: {pre_match})")
            else:
                self._user_warnings[user_id] += 1
                await self.actions.delete(
                    message,
                    reason=f"Pre-filter: {pre_match}",
                    reply_text="🚫 This message was removed by auto-moderator.",
                    sender_name=sender_name or "Unknown",
                )

            await self.actions.forward_to_review(
                message,
                chat_title=chat_title,
                verdict="delete (pre-filter)" + (" [DRY RUN]" if self.dry_run else ""),
                reason=pre_match,
            )
            return

        # 5. Build LLM payload
        warnings_count = self._user_warnings.get(user_id, 0)
        messages = self.prompts.build_messages(
            message_text=text,
            sender_name=sender_name or "Unknown",
            sender_username=sender_username,
            sender_id=user_id,
            warnings_count=warnings_count,
        )

        # 6. Route: newcomer → instant local | regular → batch
        is_test_group_route = "test" in str(chat_title).lower() or abs(int(chat_id)) == 5139770999 or abs(int(chat_id)) == 1005139770999
        if (self.newcomer.is_newcomer(user_id) or is_test_group_route) and self.llm.has_local:
            if is_test_group_route:
                logger.info(f"🧪 Test group message {user_id} — instant local LLM evaluation")
            else:
                logger.info(f"🆕 Newcomer {user_id} — instant local LLM evaluation")
            await self._evaluate_instant(
                messages, message, chat, chat_title,
                sender_name, sender_username, user_id, provider="local",
            )
        elif self.llm.has_openrouter:
            # Add to batch queue
            payload = {
                "message": text,
                "sender": {
                    "name": sender_name or "Unknown",
                    "username": sender_username or "",
                    "id": user_id,
                },
                "context": [],  # Context handled via system prompt
                "warnings_count": warnings_count,
            }
            await self.batch.add(
                payload=payload,
                message=message,
                chat=chat,
                sender_name=sender_name or "Unknown",
                sender_username=sender_username,
                user_id=user_id,
            )
            logger.debug(f"📦 Regular user {user_id} — queued for batch")
        else:
            # Fallback: direct evaluation with whatever is available
            await self._evaluate_instant(
                messages, message, chat, chat_title,
                sender_name, sender_username, user_id, provider="any",
            )

    async def _evaluate_instant(
        self,
        messages,
        message,
        chat,
        chat_title: str,
        sender_name: str,
        sender_username: Optional[str],
        user_id: int,
        provider: str = "any",
    ) -> None:
        """Evaluate a single message instantly via LLM."""
        # Pre-filter check for violent keywords (runs BEFORE LLM)
        violence_filter_result = _check_violence_keyword_filter(message.text or "")
        
        # Check if pre-filter returned an explicit "ok" verdict (quote/report detected)
        # In this case, skip LLM evaluation entirely and return immediately
        if violence_filter_result and violence_filter_result.get("verdict") == "ok":
            reason = violence_filter_result.get("reason", "")
            logger.info(f"Pre-filter allowed (skipping LLM): {reason} - {message.text[:50]}...")
            # Still apply the verdict to handle logging and test group forwarding
            await self._apply_verdict(
                violence_filter_result, message, chat, chat_title, sender_name, user_id
            )
            return
        
        # If pre-filter returned a ban verdict, apply it and skip LLM
        if violence_filter_result and violence_filter_result.get("verdict") == "ban":
            logger.info(f"Violence keyword pre-filter hit: {violence_filter_result['reason']}")
            await self._apply_verdict(
                violence_filter_result, message, chat, chat_title, sender_name, user_id
            )
            return

        try:
            try:
                # First attempt with full context
                if provider == "local":
                    response = await self.llm.chat_local(messages)
                elif provider == "openrouter":
                    response = await self.llm.chat_openrouter(messages)
                    self.quota.record_newcomer_request()
                else:
                    response = await self.llm.chat(messages)
            except httpx.HTTPStatusError as e:
                # If local LLM fails with 400 (context overflow/channel error), retry without context
                if e.response.status_code == 400:
                    logger.warning(f"LLM 400 error (likely context overflow), retrying without message context for msg {message.id}...")
                    # Re-build messages without context
                    trimmed_messages = self.prompts.build_messages(
                        message_text=message.text or "",
                        sender_name=sender_name,
                        sender_username=sender_username,
                        sender_id=user_id,
                        warnings_count=self._user_warnings.get(user_id, 0),
                        include_context=False
                    )
                    if provider == "local":
                        response = await self.llm.chat_local(trimmed_messages)
                    elif provider == "openrouter":
                        response = await self.llm.chat_openrouter(trimmed_messages)
                    else:
                        response = await self.llm.chat(trimmed_messages)
                else:
                    raise

            verdict = self._parse_verdict(response.content)
            await self._apply_verdict(
                verdict, message, chat, chat_title, sender_name, user_id
            )
        except Exception as e:
            # Log full exception details including traceback
            logger.exception(f"LLM analysis failed for msg {message.id}: {type(e).__name__}: {e}")
            
            # Fallback: if local LLM failed and OpenRouter is available, try OpenRouter
            if provider == "local" and self.llm.has_openrouter:
                logger.info(f"Falling back to OpenRouter for msg {message.id} after local LLM failure")
                try:
                    # Re-build messages without context for the fallback
                    fallback_messages = self.prompts.build_messages(
                        message_text=message.text or "",
                        sender_name=sender_name,
                        sender_username=sender_username,
                        sender_id=user_id,
                        warnings_count=self._user_warnings.get(user_id, 0),
                        include_context=False
                    )
                    response = await self.llm.chat_openrouter(fallback_messages)
                    self.quota.record_newcomer_request()
                    verdict = self._parse_verdict(response.content)
                    await self._apply_verdict(
                        verdict, message, chat, chat_title, sender_name, user_id
                    )
                    logger.info(f"OpenRouter fallback succeeded for msg {message.id}")
                    return
                except Exception as fallback_error:
                    logger.exception(f"OpenRouter fallback also failed for msg {message.id}: {type(fallback_error).__name__}: {fallback_error}")
            
            return  # Fail-open

    async def handle_batch_flush(self, batch: BatchQueue) -> None:
        """
        Called when the batch queue is flushed.
        Sends accumulated messages to OpenRouter and processes verdicts.

        OPTIMIZATION: Check pre-filter FIRST and skip LLM for known quote cases.
        """
        items = await batch.drain()
        if not items:
            return

        logger.info(f"Flushing batch: {len(items)} messages")

        # Pre-filter check FIRST - separate items into needs-llm and skip-llm
        items_needing_llm = []
        items_skipped = []

        for item in items:
            # Check pre-filter BEFORE LLM
            violence_filter_result = _check_violence_keyword_filter(item.message.text or "")
            if violence_filter_result:
                # Pre-filter has a verdict - use it and skip LLM
                logger.info(f"Pre-filter skip LLM (batch): {violence_filter_result.get('reason', 'unknown')} - {item.message.text[:50]}...")
                items_skipped.append((item, violence_filter_result))
            else:
                # No pre-filter match - needs LLM evaluation
                items_needing_llm.append(item)

        # Process items that need LLM (if any)
        verdicts = {}
        if items_needing_llm:
            # Build batch prompt only for items needing LLM
            batch_prompt_text = BatchQueue.build_batch_prompt(items_needing_llm)

            # Build messages with batch instruction
            system_prompt = self.prompts.system_prompt
            batch_instruction = (
                "\n\n---"
                "BATCH MODE: You will receive an array of messages. "
                "Return a JSON ARRAY of verdicts, one per message, "
                "in the same order. Each verdict has the same format: "
                '{"verdict": "ok"|"warn"|"delete"|"mute"|"ban", '
                '"reason": "...", "reply": "...", "index": N}"'
            )

            from src.llm.client import Message as LLMMessage
            messages = [
                LLMMessage.system(system_prompt + batch_instruction),
                LLMMessage.user(batch_prompt_text),
            ]

            try:
                response = await self.llm.chat_openrouter(messages)
                self.quota.record_batch_request()
                llm_verdicts = BatchQueue.parse_batch_verdicts(
                    response.content, len(items_needing_llm)
                )
                # Map verdicts back to original indices
                for idx, item in enumerate(items_needing_llm):
                    verdicts[item.message.id] = llm_verdicts[idx] if idx < len(llm_verdicts) else {
                        "verdict": "ok", "reason": "missing verdict", "reply": ""
                    }
            except Exception as e:
                logger.error(f"Batch LLM call failed: {e}")
                # For failed LLM, use ok verdict
                for item in items_needing_llm:
                    verdicts[item.message.id] = {"verdict": "ok", "reason": "llm_failed", "reply": ""}

        # Apply verdicts to all items (both skipped and LLM-processed)
        for item in items:
            # Check if this item was pre-filtered
            prefilter_result = next((vf for i, vf in items_skipped if i.message.id == item.message.id), None)
            if prefilter_result:
                verdict = prefilter_result
            else:
                verdict = verdicts.get(item.message.id, {"verdict": "ok", "reason": "missing", "reply": ""})

            logger.info(f"Batch verdict for msg {item.message.id}: {verdict}")
            chat_title = getattr(item.chat, "title", str(getattr(item.chat, "id", 0)))
            await self._apply_verdict(
                verdict, item.message, item.chat,
                chat_title, item.sender_name, item.user_id,
            )

        # Update status
        if self.status:
            self.status.record_batch()
            await self.status.update(
                self.quota.status_dict(),
                self.batch.size,
            )

    async def _apply_verdict(
        self,
        verdict: dict,
        message,
        chat,
        chat_title: str,
        sender_name: str,
        user_id: int,
    ) -> None:
        """Apply a parsed verdict to a message."""
        action = verdict["verdict"]
        reason = verdict.get("reason", "")
        reply_text = verdict.get("reply", "")
        rule = verdict.get("rule", "general")

        # Record for reports
        self.reports.record_verdict(action)

        chat_id = getattr(chat, "id", getattr(message, "chat_id", 0))
        is_test_group = "test" in str(chat_title).lower() or abs(int(chat_id)) == 5139770999 or abs(int(chat_id)) == 1005139770999

        if action == "ok":
            logger.debug(f"OK: msg={message.id} user={user_id}")
            
            # Forward "ok" verdicts from test groups to see the reasoning
            if is_test_group and self.actions.review_group:
                await self.actions.forward_to_review(
                    message, chat_title=chat_title,
                    verdict="ok [TEST GROUP]", reason=reason,
                )
            return

        # --- DRY RUN: only forward to review, no actions ---
        if self.dry_run:
            logger.info(
                f"🔇 DRY RUN: would {action} user={user_id} msg={message.id} "
                f"reason='{reason[:100]}'"
            )
            if self.actions.review_group:
                await self.actions.forward_to_review(
                    message, chat_title=chat_title,
                    verdict=f"{action} [DRY RUN]", reason=reason,
                )
            if self.status:
                await self.status.update(
                    self.quota.status_dict(),
                    self.batch.size,
                )
            return

        # --- LIVE MODE: take real actions ---
        self._record_action(user_id)

        # Check if trusted user - if so, don't auto-ban/mute, just log strike
        if self.reputation.is_trusted(user_id) and action in ("ban", "mute", "delete"):
            logger.info(f"⚠️ Trusted user {user_id} triggered {action} — downgrading to strike.")
            self.reputation.add_strike(user_id, rule, reason, message.text or "")
            
            if self.actions.review_group:
                await self.actions.forward_to_review(
                    message,
                    chat_title=chat_title,
                    verdict=f"STRIKE ({action} bypassed)",
                    reason=f"Trusted user violation of {rule}: {reason}",
                )
            return

        if action == "warn":
            self._user_warnings[user_id] += 1
            await self.actions.warn(message, reason=reason, reply_text=reply_text)

        elif action == "delete":
            self._user_warnings[user_id] += 1
            await self.actions.delete(
                message, reason=reason, reply_text=reply_text,
                sender_name=sender_name or "Unknown",
            )

        elif action == "mute":
            self._user_warnings[user_id] += 1
            await self.actions.mute(
                chat=chat, user_id=user_id, reason=reason,
                duration_seconds=self.config.mute_duration_seconds,
                message=message, reply_text=reply_text,
                sender_name=sender_name or "Unknown",
            )

        elif action == "ban":
            self._user_warnings[user_id] += 1
            await self.actions.ban(
                chat=chat, user_id=user_id, reason=reason,
                message=message, reply_text=reply_text,
                sender_name=sender_name or "Unknown",
            )
            if self.status:
                self.status.record_ban()

        # Forward non-ok verdicts to review
        if self.actions.review_group:
            await self.actions.forward_to_review(
                message, chat_title=chat_title,
                verdict=action, reason=reason,
            )

        # Update status after actions
        if self.status:
            await self.status.update(
                self.quota.status_dict(),
                self.batch.size,
            )

    @staticmethod
    def _parse_verdict(raw: str) -> dict:
        """Parse the LLM's JSON verdict response."""
        cleaned = raw.strip()
        
        # Handle simple "ok" text response (treat as valid ok verdict)
        if cleaned.lower() == "ok":
            return {"verdict": "ok", "reason": "Simple ok response", "reply": ""}
        
        # Strip markdown code blocks
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = [line for line in lines if not line.strip().startswith("```")]
            cleaned = "\n".join(lines).strip()

        # Try parsing as JSON
        try:
            result = json.loads(cleaned)
            
            # Handle case where JSON is valid but missing 'verdict' field
            if "verdict" not in result:
                # If it only has 'role' field, the LLM is confused - treat as ok
                if "role" in result and len(result) == 1:
                    return {"verdict": "ok", "reason": "LLM returned role instead of verdict", "reply": ""}
                # If it has some other structure but no verdict, treat as ok
                return {"verdict": "ok", "reason": "Missing verdict field in response", "reply": ""}
            
            return result
        except json.JSONDecodeError:
            pass

        # Try extracting a single JSON object
        match = re.search(r'\{.*\}', cleaned, re.DOTALL)
        if match:
            try:
                result = json.loads(match.group())
                if "verdict" not in result:
                    if "role" in result and len(result) == 1:
                        return {"verdict": "ok", "reason": "LLM returned role instead of verdict", "reply": ""}
                    return {"verdict": "ok", "reason": "Missing verdict field in extracted JSON", "reply": ""}
                return result
            except json.JSONDecodeError:
                pass

        # Check if the response is a plain string (no JSON structure)
        if not match and cleaned and not cleaned.startswith("{"):
            logger.warning(f"LLM returned plain string instead of JSON: {cleaned}")
            return {"verdict": "ok", "reason": f"llm_plain_string: {cleaned[:100]}", "reply": ""}

        logger.warning(f"Failed to parse LLM verdict, treating as 'ok'. Raw response: {raw}")
        return {"verdict": "ok", "reason": "unparseable LLM response", "reply": ""}
