# tg-chat-moderator

Generic LLM-powered Telegram group chat moderator bot.

## Features

- **System-prompt-driven** — edit `config/system_prompt.md` to change moderation behavior, no code changes needed
- **Pre-filter** — keyword/regex blocklist for instant moderation (no LLM call)
- **LLM analysis** — sends messages to an LLM for nuanced moderation verdicts (ok/warn/delete/mute)
- **Dual LLM support** — OpenRouter (cloud) or any local OpenAI-compatible endpoint (LM Studio, Ollama, etc.)
- **Human review** — forward flagged messages to a review group with context
- **User cooldown** — rate-limits moderation actions per user
- **Escalation ladder** — tracks per-user warnings for progressive enforcement

## Quick Start

```bash
# 1. Install
pip install -e .

# 2. Configure
cp config/config.example.yaml config/config.yaml
# Edit config/config.yaml with your Telegram API credentials and LLM API key

# 3. Customize moderation rules
# Edit config/system_prompt.md

# 4. Validate config
moderator check-config

# 5. Run
moderator run
```

## CLI Commands

| Command | Description |
|---|---|
| `moderator run` | Start the moderator bot |
| `moderator check-config` | Validate configuration |
| `moderator test-prompt "test message"` | Test LLM verdict without connecting to Telegram |

## Architecture

```
Telegram Group → Gateway (Telethon) → Pre-Filter → LLM Analysis → Action Executor
                                                                    ├─ warn (reply)
                                                                    ├─ delete
                                                                    ├─ mute
                                                                    └─ forward to review
```

## Configuration

- `config/config.yaml` — API credentials, LLM settings, monitored groups
- `config/system_prompt.md` — Moderator persona, rules, verdict format

## Requirements

- Python 3.10+
- Telegram API credentials from [my.telegram.org](https://my.telegram.org)
- LLM API key (OpenRouter) or local LLM server
