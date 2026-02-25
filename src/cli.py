"""
CLI Module — Typer-based command-line interface.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

app = typer.Typer(
    name="moderator",
    help="tg-chat-moderator — Generic LLM-powered Telegram group moderator",
)


@app.command()
def run(
    config: Optional[str] = typer.Option(
        None, "--config", "-c", help="Path to config YAML file"
    ),
) -> None:
    """Start the moderator bot."""
    from src.main import main as run_main

    run_main(config)


@app.command("check-config")
def check_config(
    config: Optional[str] = typer.Option(
        None, "--config", "-c", help="Path to config YAML file"
    ),
) -> None:
    """Validate configuration file without starting the bot."""
    from src.config import load_config

    try:
        cfg = load_config(config)
        typer.echo("✅ Configuration is valid.")
        typer.echo(f"   Telegram API ID: {cfg.telegram.api_id}")
        typer.echo(f"   LLM provider:    {cfg.llm.provider}")
        typer.echo(f"   LLM model:       {cfg.llm.model}")
        typer.echo(f"   Monitored groups: {cfg.moderation.monitored_groups}")
        typer.echo(f"   System prompt:    {cfg.moderation.system_prompt_path}")
    except Exception as e:
        typer.echo(f"❌ Config error: {e}", err=True)
        raise typer.Exit(1)


@app.command("test-prompt")
def test_prompt(
    message: str = typer.Argument(help="Test message to evaluate"),
    config: Optional[str] = typer.Option(
        None, "--config", "-c", help="Path to config YAML file"
    ),
) -> None:
    """Test the moderation prompt by sending a message to the LLM (no Telegram needed)."""
    import asyncio
    from src.config import load_config
    from src.llm.client import LLMClient
    from src.llm.prompts import ModerationPromptBuilder

    async def _test():
        cfg = load_config(config)

        llm = LLMClient(
            provider=cfg.llm.provider,
            api_key=cfg.llm.api_key.get_secret_value(),
            model=cfg.llm.model,
            endpoint=cfg.llm.endpoint,
            local_model=cfg.llm.local_model,
            max_tokens=cfg.llm.max_tokens,
            temperature=cfg.llm.temperature,
        )

        builder = ModerationPromptBuilder(
            system_prompt_path=cfg.moderation.system_prompt_path,
        )
        builder.load_system_prompt()

        messages = builder.build_messages(
            message_text=message,
            sender_name="TestUser",
            sender_username="test_user",
            sender_id=0,
        )

        typer.echo(f"📤 Sending to LLM ({cfg.llm.provider} / {cfg.llm.model})...")
        response = await llm.chat(messages)

        typer.echo(f"\n📥 Raw response:\n{response.content}")
        typer.echo(f"\n📊 Tokens: {response.total_tokens}")

        # Try parsing
        from src.moderation.engine import ModerationEngine
        verdict = ModerationEngine._parse_verdict(response.content)
        typer.echo(f"\n⚖️ Parsed verdict:\n{json.dumps(verdict, indent=2, ensure_ascii=False)}")

        await llm.close()

    asyncio.run(_test())


if __name__ == "__main__":
    app()
