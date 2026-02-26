"""
Configuration Module

Loads configuration from YAML file with environment variable override.
Adapted from llm-interviewer/src/config.py.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class TelegramConfig(BaseModel):
    """Telegram API configuration."""
    api_id: int = Field(..., description="Telegram API ID from my.telegram.org")
    api_hash: str = Field(..., description="Telegram API hash")
    phone: str = Field(..., description="Phone number for userbot session")
    session_name: str = Field(default="moderator_bot", description="Session file name")


class ModerationConfig(BaseModel):
    """Moderation behavior configuration."""
    monitored_groups: list[str] = Field(
        default_factory=list,
        description="Group usernames or IDs to monitor",
    )
    review_group: Optional[str] = Field(
        default=None,
        description="Group/channel to forward flagged messages for human review",
    )
    dry_run: bool = Field(
        default=False,
        description="Dry run mode: only forward to review group, no actions in main chat",
    )
    hard_ban_keywords: list[str] = Field(
        default_factory=list,
        description="Keywords that trigger instant action (no LLM call)",
    )
    hard_ban_regex: list[str] = Field(
        default_factory=list,
        description="Regex patterns that trigger instant action",
    )
    user_cooldown_seconds: int = Field(
        default=60, ge=0, le=3600,
        description="Cooldown between moderation actions on same user",
    )
    context_window_messages: int = Field(
        default=15, ge=0, le=100,
        description="Recent messages to include as LLM context",
    )
    system_prompt_path: str = Field(
        default="config/system_prompt.md",
        description="Path to the system prompt markdown file",
    )
    mute_duration_seconds: int = Field(
        default=3600, ge=60, le=31536000,
        description="Duration for 'mute' moderation action",
    )
    newcomer_window_hours: int = Field(
        default=24, ge=1, le=720,
        description="Hours to consider a user a newcomer after first message",
    )
    batch_max_tokens: int = Field(
        default=3000, ge=500, le=30000,
        description="Max estimated tokens before auto-flushing batch queue",
    )


class QuotaConfig(BaseModel):
    """OpenRouter quota management."""
    daily_limit: int = Field(
        default=1000, ge=1,
        description="Max OpenRouter requests per day",
    )
    warmup_interval_minutes: int = Field(
        default=30, ge=5, le=1440,
        description="Re-warm local LLM every N minutes",
    )

class LLMConfig(BaseModel):
    """LLM provider configuration."""
    provider: Literal["openrouter", "local", "both"] = Field(
        default="openrouter", description="LLM provider (openrouter, local, or both for failover)"
    )
    api_key: SecretStr = Field(
        default=SecretStr(""), description="API key (OpenRouter)"
    )
    model: str = Field(
        default="google/gemini-2.0-flash-001", description="Model name"
    )
    endpoint: str = Field(
        default="http://127.0.0.1:1234/v1",
        description="API endpoint (used when provider=local)",
    )
    local_model: str = Field(
        default="gemma-3-4b",
        description="Model name for local provider",
    )
    max_tokens: int = Field(default=500, ge=50, le=4000)
    temperature: float = Field(default=0.1, ge=0.0, le=2.0)


class LoggingConfig(BaseModel):
    """Logging configuration."""
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(default="INFO")
    file: Optional[str] = Field(default="logs/moderator.log")


class AppConfig(BaseSettings):
    """
    Main application configuration.

    Usage:
        config = AppConfig.from_yaml("config/config.yaml")
    """
    model_config = SettingsConfigDict(
        env_prefix="MODERATOR_",
        env_nested_delimiter="__",
        case_sensitive=False,
    )

    telegram: TelegramConfig
    moderation: ModerationConfig = Field(default_factory=ModerationConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    quota: QuotaConfig = Field(default_factory=QuotaConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "AppConfig":
        """Load configuration from YAML file."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls(**data)

    @model_validator(mode="after")
    def check_placeholders(self) -> "AppConfig":
        """Check if credentials are still using placeholder values."""
        placeholders = {
            "your_api_hash_here",
            "+380XXXXXXXXX",
            "your_openrouter_key",
        }
        if self.telegram.api_id == 12345678:
            raise ValueError("Telegram api_id is still using the placeholder: 12345678")
        if self.telegram.api_hash in placeholders:
            raise ValueError(
                f"Telegram api_hash is still using the placeholder: {self.telegram.api_hash}"
            )
        if self.telegram.phone and "X" in self.telegram.phone:
            raise ValueError(
                f"Telegram phone is still using the placeholder: {self.telegram.phone}"
            )
        if (
            self.llm.provider == "openrouter"
            and self.llm.api_key.get_secret_value() in placeholders
        ):
            raise ValueError("LLM API key is still using placeholder value")
        return self


def load_config(path: Optional[str | Path] = None) -> AppConfig:
    """
    Load configuration from file or environment.

    Tries: explicit path → config/config.yaml → config.yaml → env vars.
    """
    if path:
        return AppConfig.from_yaml(path)

    default_paths = [
        Path("config/config.yaml"),
        Path("config.yaml"),
    ]
    for p in default_paths:
        if p.exists():
            return AppConfig.from_yaml(p)

    # Fall back to environment
    return AppConfig(
        telegram=TelegramConfig(
            api_id=int(os.environ["MODERATOR_TELEGRAM__API_ID"]),
            api_hash=os.environ["MODERATOR_TELEGRAM__API_HASH"],
            phone=os.environ["MODERATOR_TELEGRAM__PHONE"],
        ),
    )
