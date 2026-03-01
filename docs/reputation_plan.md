# User Trust & Reputation System — Future Implementation Plan

> **Status:** Parked — to be implemented after dry-run stabilization and prompt tuning.

## Problem

The bot banned an established group member (Yuriy) based on a false positive from the local LLM. Established users should have higher protection against auto-moderation.

## Design

### Trust Tiers

| Tier | Criteria | On violation |
|---|---|---|
| 🆕 Newcomer | < 24h in group | Instant action (ban/delete/mute) |
| 👤 Regular | 24h – 7 days | Warn first, accumulate strikes |
| ✅ Trusted | 7+ days, 50+ messages | **Never auto-ban** — log strike + forward for manual review |

### Strike System

- Each non-ok verdict adds a **strike** record: `{user_id, rule, reason, timestamp, message_excerpt}`
- Persisted in `data/user_strikes.json`
- Trusted users never get auto-banned — verdict is downgraded to a review flag:
  *"⚠️ Trusted user triggered Rule 5 — manual review needed"*
- Admins can clear strikes via a command

### Daily & Weekly Reports

Sent to `review_group` on schedule:

```
📊 Weekly Moderation Report (Feb 20–26)
───────────────────
👤 Top flagged users:
  1. @user_a — 3 strikes (Rule 5 ×2, Rule 10 ×1)
  2. @user_b — 1 strike (Rule 6 ×1)

📈 Summary:
  Messages analyzed: 1,247
  Verdicts: 1,200 ok | 35 warn | 10 delete | 2 ban
  OpenRouter quota used: 847/1000
```

### New Files

| File | Purpose |
|---|---|
| `src/moderation/reputation.py` | `UserReputation` class: trust tier calc, strike tracking |
| `src/moderation/reports.py` | `ReportGenerator`: daily/weekly digest to review group |
| `data/user_strikes.json` | Persisted strike log |

### Config Additions

```yaml
moderation:
  trusted_user_min_days: 7
  trusted_user_min_messages: 50
  report_schedule: "weekly"  # "daily", "weekly", or "both"
```

### Engine Changes

In `_apply_verdict()`, before executing ban/mute:
```python
if reputation.is_trusted(user_id) and action in ("ban", "mute"):
    # Downgrade to strike + review flag
    reputation.add_strike(user_id, rule, reason)
    await forward_review_flag(message, "trusted user — needs manual review")
    return  # Do NOT execute ban/mute
```
