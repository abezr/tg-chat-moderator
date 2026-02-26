"""
Processed Message Cache Module

LRU cache to prevent duplicate LLM processing of the same message.
"""

from __future__ import annotations

import logging
from collections import OrderedDict

logger = logging.getLogger(__name__)


class ProcessedCache:
    """
    LRU cache keyed by (chat_id, message_id).

    Prevents duplicate LLM calls when the same message
    triggers multiple events or retries.
    """

    def __init__(self, max_size: int = 10000):
        self.max_size = max_size
        self._cache: OrderedDict[tuple[int, int], bool] = OrderedDict()

    def is_processed(self, chat_id: int, msg_id: int) -> bool:
        """Check if message was already processed."""
        key = (chat_id, msg_id)
        if key in self._cache:
            self._cache.move_to_end(key)
            return True
        return False

    def mark_processed(self, chat_id: int, msg_id: int) -> None:
        """Mark message as processed."""
        key = (chat_id, msg_id)
        self._cache[key] = True
        self._cache.move_to_end(key)

        # Evict oldest if over limit
        while len(self._cache) > self.max_size:
            self._cache.popitem(last=False)

    @property
    def size(self) -> int:
        return len(self._cache)
