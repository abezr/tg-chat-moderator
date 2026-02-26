# tg-chat-moderator

LLM-powered Telegram group moderator with smart quota management, dual-path moderation, and intent tracking.

## Features

- **Dual-path moderation** — newcomers are evaluated instantly via local LLM; regular members' messages are batched for OpenRouter
- **Smart quota management** — 1000 daily OpenRouter requests distributed via sliding interval, dynamically adjusting for newcomer spending
- **Dry-run mode** — observe verdicts without any actions; only forwards flagged messages to review group with `[DRY RUN]` tag
- **Intent tracking** — LLM analyzes the true intent of messages (hidden ads, paid services disguised as casual posts)
- **Rule quoting** — bot quotes the exact violated rule in its public response
- **Pre-filter** — keyword/regex blocklist for instant action (no LLM call)
- **Local LLM warm-up** — system prompt is pre-cached in local LLM KV-cache on startup and periodically refreshed
- **Dedup cache** — LRU cache prevents double-processing of the same message
- **Live status** — self-updating status message in review group with quota, batch timing, and last ban info
- **Dual LLM support** — OpenRouter (cloud) + any local OpenAI-compatible endpoint (LM Studio, Ollama)
- **Human review** — flagged messages forwarded to review group with context
- **Escalation** — warn → delete → mute → permanent ban

## Quick Start

```bash
# 1. Install
uv venv
source .venv/Scripts/activate   # Windows (Git Bash)
# source .venv/bin/activate     # Linux/Mac
uv pip install -e .

# 2. Configure
cp config/config.example.yaml config/config.yaml
# Edit config/config.yaml — add Telegram API credentials, LLM API key

# 3. Customize rules
# Edit config/system_prompt.md

# 4. Validate
uv run moderator check-config

# 5. Run
uv run moderator run
```

## CLI Commands

| Command | Description |
|---|---|
| `uv run moderator run` | Start the bot |
| `uv run moderator check-config` | Validate configuration |
| `uv run moderator test-prompt "message"` | Test LLM verdict locally |

## Architecture

```
Message → Gateway → Pre-Filter → Dedup Cache → Newcomer Check
                                                  │
                              ┌───────────────────┴──────────────┐
                              ▼                                  ▼
                     Local LLM (instant)              Batch Queue → OpenRouter
                              │                                  │
                              └────────────┬─────────────────────┘
                                           ▼
                                    Parse Verdict → Action Executor
                                                     ├─ warn (reply)
                                                     ├─ delete + explanation
                                                     ├─ mute + explanation
                                                     ├─ ban + explanation
                                                     └─ forward to review
                                                          └─ update status msg
```

## Configuration

### `config/config.yaml`

```yaml
telegram:
  api_id: 12345678
  api_hash: "your_hash"
  phone: "+380XXXXXXXXX"

moderation:
  monitored_groups: ["@YourGroup"]
  review_group: "YourReviewGroup"
  dry_run: true                   # only forward to review, no actions
  hard_ban_keywords: ["spam phrase"]
  user_cooldown_seconds: 60
  mute_duration_seconds: 3600
  newcomer_window_hours: 24       # instant local LLM for new users
  batch_max_tokens: 3000          # flush batch when tokens accumulate

quota:
  daily_limit: 1000               # OpenRouter requests/day
  warmup_interval_minutes: 30     # re-warm local LLM cache

llm:
  provider: both                  # "openrouter", "local", or "both"
  api_key: "sk-or-..."
  model: "google/gemini-2.0-flash-001"
  endpoint: "http://127.0.0.1:1234/v1"
  local_model: "gemma-3-4b"
  max_tokens: 500
  temperature: 0.1
```

### `config/system_prompt.md`

Defines the moderator persona, community rules, verdict format, and intent tracking examples. Edit this file to customize moderation behavior — no code changes needed.

## Requirements

- Python 3.10+
- Telegram API credentials from [my.telegram.org](https://my.telegram.org)
- LLM API key (OpenRouter) and/or local LLM server
