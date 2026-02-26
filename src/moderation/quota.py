"""
Quota Manager Module

Manages the OpenRouter daily request quota with a sliding interval.
Distributes remaining quota across remaining time in the day,
accounting for unplanned newcomer spending.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class QuotaManager:
    """
    Sliding-interval quota manager for OpenRouter.

    Calculates the optimal interval between batch flushes so that
    the daily limit is evenly distributed across the remaining hours.
    """

    def __init__(self, daily_limit: int = 1000, persist_path: Optional[str] = None):
        self.daily_limit = daily_limit
        self.persist_path = Path(persist_path) if persist_path else None

        # Counters — reset at midnight UTC
        self._day_start = self._current_day_start()
        self.requests_used: int = 0
        self.newcomer_requests: int = 0
        self._last_batch_time: float = 0.0

        if self.persist_path and self.persist_path.exists():
            self._load()
        
        # Immediate reset if loaded data is from a previous day
        self._maybe_reset()

    @staticmethod
    def _current_day_start() -> float:
        """Timestamp of midnight UTC today."""
        now = datetime.now(timezone.utc)
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return midnight.timestamp()

    @staticmethod
    def _seconds_until_midnight() -> float:
        """Seconds remaining until next midnight UTC."""
        now = datetime.now(timezone.utc)
        tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0)
        from datetime import timedelta
        tomorrow += timedelta(days=1)
        return (tomorrow - now).total_seconds()

    def _maybe_reset(self) -> None:
        """Reset counters if a new day has started."""
        current_day = self._current_day_start()
        if current_day > self._day_start:
            logger.info(
                f"New day detected. Resetting quota. "
                f"Yesterday: {self.requests_used}/{self.daily_limit} used "
                f"({self.newcomer_requests} newcomer)"
            )
            self._day_start = current_day
            self.requests_used = 0
            self.newcomer_requests = 0
            # We don't save immediately on reset, it will save on next record or shutdown

    @property
    def remaining_requests(self) -> int:
        """How many OpenRouter requests remain today."""
        self._maybe_reset()
        return max(0, self.daily_limit - self.requests_used)

    @property
    def interval_seconds(self) -> float:
        """
        Optimal seconds between batch flushes.

        Distributes remaining quota evenly across remaining time.
        Minimum 10s to avoid hammering.
        """
        self._maybe_reset()
        remaining = self.remaining_requests
        if remaining <= 0:
            return 3600.0  # Quota exhausted, check once per hour

        remaining_time = self._seconds_until_midnight()
        interval = remaining_time / remaining
        return max(10.0, interval)  # Floor at 10s

    def next_batch_time(self) -> float:
        """Timestamp of when the next batch should fire."""
        if self._last_batch_time == 0:
            return time.time()  # First batch: now
        return self._last_batch_time + self.interval_seconds

    def record_batch_request(self, count: int = 1) -> None:
        """Record that a batch was sent to OpenRouter."""
        self._maybe_reset()
        self.requests_used += count
        self._last_batch_time = time.time()
        logger.debug(
            f"Quota: {self.requests_used}/{self.daily_limit} used, "
            f"interval={self.interval_seconds:.1f}s"
        )
        self.save()

    def record_newcomer_request(self) -> None:
        """Record a newcomer fallback request to OpenRouter."""
        self._maybe_reset()
        self.requests_used += 1
        self.newcomer_requests += 1
        self.save()

    def can_send_now(self) -> bool:
        """Whether enough time has passed since last batch."""
        return time.time() >= self.next_batch_time()

    def save(self) -> None:
        """Persist state to disk."""
        if not self.persist_path:
            return
        try:
            self.persist_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "day_start": self._day_start,
                "requests_used": self.requests_used,
                "newcomer_requests": self.newcomer_requests,
                "last_batch_time": self._last_batch_time,
            }
            self.persist_path.write_text(json.dumps(data), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to save quota data: {e}")

    def _load(self) -> None:
        """Load persisted state."""
        try:
            data = json.loads(self.persist_path.read_text(encoding="utf-8"))
            self._day_start = data.get("day_start", self._day_start)
            self.requests_used = data.get("requests_used", 0)
            self.newcomer_requests = data.get("newcomer_requests", 0)
            self._last_batch_time = data.get("last_batch_time", 0.0)
            logger.info(f"Loaded quota state: {self.requests_used}/{self.daily_limit} used")
        except Exception as e:
            logger.warning(f"Failed to load quota data: {e}")

    def status_dict(self) -> dict:
        """Status info for the StatusReporter."""
        self._maybe_reset()
        return {
            "requests_used": self.requests_used,
            "daily_limit": self.daily_limit,
            "remaining": self.remaining_requests,
            "newcomer_requests": self.newcomer_requests,
            "interval_seconds": round(self.interval_seconds, 1),
            "next_batch_time": self.next_batch_time(),
        }
