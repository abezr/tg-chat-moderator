"""
Newcomer Tracker Module

Tracks when users are first seen in monitored groups.
Users seen within `newcomer_window_hours` are classified as newcomers
and get instant local LLM evaluation instead of batched OpenRouter.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class NewcomerTracker:
    """
    Tracks first-seen timestamps per user.

    A "newcomer" is any user whose first message was seen
    within the last `window_hours`.
    """

    def __init__(
        self,
        window_hours: int = 24,
        persist_path: Optional[str] = None,
    ):
        self.window_seconds = window_hours * 3600
        self.persist_path = Path(persist_path) if persist_path else None

        # {user_id: first_seen_timestamp}
        self._users: dict[int, float] = {}

        if self.persist_path and self.persist_path.exists():
            self._load()

    def register_user(self, user_id: int) -> None:
        """Record user as seen. No-op if already known."""
        if user_id not in self._users:
            self._users[user_id] = time.time()
            logger.debug(f"New user registered: {user_id}")

    def is_newcomer(self, user_id: int) -> bool:
        """Check if user is a newcomer (first seen < window ago)."""
        first_seen = self._users.get(user_id)
        if first_seen is None:
            return True  # Never seen = newcomer
        return (time.time() - first_seen) < self.window_seconds

    def bulk_register(self, user_ids: list[int]) -> None:
        """Pre-populate known users (e.g. from get_participants on startup)."""
        now = time.time()
        # Mark them as "seen long ago" so they're NOT newcomers
        old_ts = now - self.window_seconds - 1
        added = 0
        updated = 0
        for uid in user_ids:
            if uid not in self._users:
                self._users[uid] = old_ts
                added += 1
            else:
                # If they were already known but as newcomers, make them old
                if (now - self._users[uid]) < self.window_seconds:
                    self._users[uid] = old_ts
                    updated += 1
        
        if added or updated:
            logger.info(f"Bulk-registered: {added} new, {updated} updated to non-newcomers")

    def save(self) -> None:
        """Persist state to disk."""
        if not self.persist_path:
            return
        self.persist_path.parent.mkdir(parents=True, exist_ok=True)
        data = {str(k): v for k, v in self._users.items()}
        self.persist_path.write_text(json.dumps(data), encoding="utf-8")

    def _load(self) -> None:
        """Load persisted state."""
        try:
            data = json.loads(self.persist_path.read_text(encoding="utf-8"))
            self._users = {int(k): v for k, v in data.items()}
            logger.info(f"Loaded {len(self._users)} users from {self.persist_path}")
        except Exception as e:
            logger.warning(f"Failed to load newcomer data: {e}")

    @property
    def known_user_count(self) -> int:
        return len(self._users)
