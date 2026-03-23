#!/usr/bin/env python3
"""
Test file for verifying profanity handling according to new rules:
- Profanity alone (not directed at someone) → WARN
- Profanity with personal target → BAN
- Non-sexual personal insults → MUTE
"""

import asyncio
import json
import sys
import re
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from src.config import load_config
from src.llm.client import LLMClient
from src.llm.prompts import ModerationPromptBuilder


def test_profanity_cases():
    """Test profanity handling in different contexts."""
    config = load_config("config/config.yaml")
    
    client = LLMClient(
        provider=config.llm.provider,
        api_key=config.llm.api_key.get_secret_value(),
        model=config.llm.model,
        endpoint=config.llm.endpoint,
        local_model=config.llm.local_model,
        max_tokens=config.llm.max_tokens,
        temperature=config.llm.temperature
    )
    
    prompt_builder = ModerationPromptBuilder(
        system_prompt_path=config.moderation.system_prompt_path,
        context_window=config.moderation.context_window_messages
    )
    
    # Test cases with expected verdicts
    test_cases = [
        ("General profanity 1", "Блд 😂😂😂", "ok"),
        ("General profanity 2", "Просто пиз*ец 🤯", "ok"),
        ("Personal insult 1", "Ты блядь", "ban"),
        ("Personal insult 2", "Ты пиз*ец", "ban"),
        ("General profanity in frustration", "Пизда, що за ситуація", "ok"),
        ("Personal insult without sexual content", "Ты дурак", "mute"),
    ]
    
    print("Testing profanity handling according to new rules:")
    print("=" * 70)
    
    async def run_tests():
        async with client:
            for description, message, expected_verdict in test_cases:
                try:
                    print(f"\n--- Testing: {description} ---")
                    print(f"Message: {message}")
                    print(f"Expected: {expected_verdict}")
                    
                    messages = prompt_builder.build_messages(
                        message_text=message,
                        sender_name="TestUser"
                    )
                    
                    response = await client.chat(messages)
                    content = response.content.strip()
                    
                    if content.startswith("```"):
                        content = "\n".join(content.split("\n")[1:-1])
                    
                    try:
                        verdict = json.loads(content)
                    except Exception:
                        match = re.search(r'\{.*\}', content, re.DOTALL)
                        if match:
                            verdict = json.loads(match.group())
                        else:
                            raise ValueError("Could not parse JSON")
                    
                    # Normalize to lowercase for comparison
                    actual_verdict = verdict['verdict'].lower()
                    expected_verdict = expected_verdict.lower()
                    
                    print(f"Actual: {actual_verdict}")
                    print(f"Reason: {verdict.get('reason', 'N/A')}")
                    if 'reply' in verdict and verdict['reply']:
                        print(f"Reply: {verdict['reply']}")
                        
                    # Check if the verdict matches expected
                    if actual_verdict != expected_verdict:
                        print(f"❌ FAIL: Expected '{expected_verdict}', got '{actual_verdict}'")
                    else:
                        print(f"✅ PASS")
                        
                except Exception as e:
                    print(f"\n❌ Error testing '{description}': {e}")
    
    asyncio.run(run_tests())
    print("\n" + "=" * 70)
    print("Test complete!")


if __name__ == "__main__":
    import os
    if os.name == 'nt':
        import sys
        import codecs
        sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer)
        sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer)
    test_profanity_cases()
