#!/usr/bin/env python3
"""Debug test to see what's being sent to LLM for profanity cases."""

import asyncio
import json
import sys
import re
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from src.config import load_config
from src.llm.client import LLMClient
from src.llm.prompts import ModerationPromptBuilder


def debug_profanity_cases():
    """Debug test for profanity cases."""
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
    
    test_cases = [
        ("Personal insult 1", "Ты блядь", "ban"),
        ("Personal insult with username", "@username козел", "mute"),
    ]
    
    print("Debugging profanity handling:")
    print("=" * 70)
    
    async def run_tests():
        async with client:
            for description, message, expected_verdict in test_cases:
                try:
                    print(f"\n--- Testing: {description} ---")
                    print(f"Message: {message}")
                    print(f"Expected: {expected_verdict}")
                    
                    # Build and print the actual messages being sent
                    messages = prompt_builder.build_messages(
                        message_text=message,
                        sender_name="TestUser"
                    )
                    
                    print("\n=== System Prompt ===")
                    print(messages[0].content[:500] + "..." if len(messages[0].content) > 500 else messages[0].content)
                    
                    print("\n=== User Payload ===")
                    print(messages[1].content)
                    
                    response = await client.chat(messages)
                    content = response.content.strip()
                    
                    print("\n=== LLM Response ===")
                    print(content)
                    
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
                    
                    print(f"\nActual: {verdict['verdict']}")
                    print(f"Reason: {verdict.get('reason', 'N/A')}")
                    if 'reply' in verdict and verdict['reply']:
                        print(f"Reply: {verdict['reply']}")
                        
                    # Check if the verdict matches expected
                    if verdict['verdict'] != expected_verdict:
                        print(f"❌ FAIL: Expected '{expected_verdict}', got '{verdict['verdict']}'")
                    else:
                        print(f"✅ PASS")
                        
                except Exception as e:
                    print(f"\n❌ Error testing '{description}': {e}")
    
    asyncio.run(run_tests())
    print("\n" + "=" * 70)
    print("Debug complete!")


if __name__ == "__main__":
    import os
    if os.name == 'nt':
        import sys
        import codecs
        sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer)
        sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer)
    debug_profanity_cases()
