import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class Strike:
    timestamp: float
    rule: str
    reason: str
    message_excerpt: str

@dataclass
class UserStats:
    user_id: int
    first_seen: float
    message_count: int = 0
    strikes: List[Strike] = None

    def __post_init__(self):
        if self.strikes is None:
            self.strikes = []

class UserReputation:
    """
    Tracks user activity and reputation to determine trust levels.
    """
    def __init__(
        self,
        persist_path: str = "data/user_reputation.json",
        trusted_min_days: int = 7,
        trusted_min_messages: int = 50,
    ):
        self.persist_path = Path(persist_path)
        self.trusted_min_days = trusted_min_days
        self.trusted_min_messages = trusted_min_messages
        self.users: Dict[int, UserStats] = {}
        self.load()

    def load(self):
        if not self.persist_path.exists():
            return
        try:
            with open(self.persist_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                for uid_str, udata in data.items():
                    uid = int(uid_str)
                    strikes = [Strike(**s) for s in udata.get("strikes", [])]
                    self.users[uid] = UserStats(
                        user_id=uid,
                        first_seen=udata["first_seen"],
                        message_count=udata.get("message_count", 0),
                        strikes=strikes,
                    )
            logger.info(f"Loaded reputation data for {len(self.users)} users")
        except Exception as e:
            logger.error(f"Failed to load reputation data: {e}")

    def save(self):
        try:
            self.persist_path.parent.mkdir(parents=True, exist_ok=True)
            data = {str(uid): asdict(stats) for uid, stats in self.users.items()}
            with open(self.persist_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to save reputation data: {e}")

    def update_activity(self, user_id: int):
        """Update message count and first-seen timestamp."""
        if user_id not in self.users:
            self.users[user_id] = UserStats(user_id=user_id, first_seen=time.time())
        
        self.users[user_id].message_count += 1
        # Save periodically or on every message if volume is low
        self.save()

    def add_strike(self, user_id: int, rule: str, reason: str, message_text: str):
        """Record a violation strike for a user."""
        if user_id not in self.users:
            self.users[user_id] = UserStats(user_id=user_id, first_seen=time.time())
        
        strike = Strike(
            timestamp=time.time(),
            rule=rule,
            reason=reason,
            message_excerpt=message_text[:100] + ("..." if len(message_text) > 100 else ""),
        )
        self.users[user_id].strikes.append(strike)
        logger.info(f"Added strike to user {user_id} (Total: {len(self.users[user_id].strikes)})")
        self.save()

    def get_tier(self, user_id: int) -> str:
        """Determine the trust tier of a user."""
        if user_id not in self.users:
            return "newcomer"
        
        stats = self.users[user_id]
        days_since_first_seen = (time.time() - stats.first_seen) / 86400

        if days_since_first_seen >= self.trusted_min_days and stats.message_count >= self.trusted_min_messages:
            return "trusted"
        elif days_since_first_seen >= 1: # More than 24h
            return "regular"
        else:
            return "newcomer"

    def is_trusted(self, user_id: int) -> bool:
        return self.get_tier(user_id) == "trusted"

    def get_stats(self, user_id: int) -> Optional[UserStats]:
        return self.users.get(user_id)
