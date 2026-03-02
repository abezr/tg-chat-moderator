import logging
import time
from collections import Counter
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from src.moderation.reputation import UserReputation

logger = logging.getLogger(__name__)

class ReportGenerator:
    """
    Generates moderation reports (daily/weekly) for the review group.
    """
    def __init__(self, reputation: UserReputation):
        self.reputation = reputation
        self.stats = {
            "total_messages": 0,
            "verdicts": Counter(),
            "start_time": time.time(),
        }

    def record_verdict(self, verdict: str):
        """Record a single verdict for the report statistics."""
        self.stats["total_messages"] += 1
        self.stats["verdicts"][verdict] += 1

    def generate_report(self, daily: bool = True) -> str:
        """Generate a formatted report string."""
        report_type = "Daily" if daily else "Weekly"
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Aggregate stats
        total = self.stats["total_messages"]
        verdicts = self.stats["verdicts"]
        
        # Find top flagged users
        flagged_users = []
        for uid, user_stats in self.reputation.users.items():
            if user_stats.strikes:
                recent_strikes = [s for s in user_stats.strikes if time.time() - s.timestamp < (86400 if daily else 604800)]
                if recent_strikes:
                    flagged_users.append((uid, len(recent_strikes), recent_strikes))
        
        flagged_users.sort(key=lambda x: x[1], reverse=True)
        top_users_str = ""
        for i, (uid, count, strikes) in enumerate(flagged_users[:5]):
            rule_summary = Counter(s.rule for s in strikes)
            rules_str = ", ".join(f"{rule} x{c}" for rule, c in rule_summary.items())
            top_users_str += f"  {i+1}. ID: {uid} — {count} strikes ({rules_str})\n"

        if not top_users_str:
            top_users_str = " (None)\n"

        # Build report
        report = (
            f"📊 {report_type} Moderation Report ({now})\n"
            f"───────────────────\n"
            f"📈 Summary:\n"
            f"  Messages analyzed: {total}\n"
            f"  Verdicts: " + " | ".join(f"{v}: {c}" for v, c in verdicts.items()) + "\n\n"
            f"👤 Top flagged users:\n"
            f"{top_users_str}"
        )

        return report

    def reset_stats(self):
        """Reset counters for the next reporting period."""
        self.stats = {
            "total_messages": 0,
            "verdicts": Counter(),
            "start_time": time.time(),
        }
