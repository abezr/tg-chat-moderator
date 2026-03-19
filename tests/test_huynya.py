#!/usr/bin/env python3
"""Test how "huynya" is handled in different contexts."""

import asyncio
import json
import sys
import re
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from src.config import load_config
from src.llm.client import LLMClient
from src.llm.prompts import ModerationPromptBuilder

def test_huynya_scenarios():
    """Test various scenarios with "huynya"."""
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
        ("Standalone word", "хуйня"),
        ("Military context 1", "Ця хуйня не працює в бою"),
        ("Military context 2", "Нам дали всю цю хуйню для оборони"),
        ("General conversation 1", "Чого такої хуйні написано?"),
        ("General conversation 2", "Ця хуня просто з'їла мій час"),
        ("Technical context", "Ця технічна хуйня не відповідає специфікаціям"),
        ("Question", "Що це за хуйня?"),
        ("Negative feedback", "Ця хуйня повністю дряпа"),
        ("Casual conversation", "Ні, не хочу цього брати, вся хуйня"),
    ]
    
    print("Testing 'huynya' in different contexts:")
    print("=" * 60)
    
    async def run_tests():
        async with client:
            for description, message in test_cases:
                try:
                    print(f"\n--- Testing: {description} ---")
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
                    
                    print(f"Verdict: {verdict['verdict']}")
                    print(f"Reason: {verdict.get('reason', 'N/A')}")
                    if 'reply' in verdict and verdict['reply']:
                        print(f"Reply: {verdict['reply']}")
                        
                except Exception as e:
                    print(f"\n❌ Error testing '{description}': {e}")
    
    asyncio.run(run_tests())
    print("\n" + "=" * 60)
    print("Test complete!")

if __name__ == "__main__":
    import os
    if os.name == 'nt':
        import sys
        import codecs
        sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer)
        sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer)
    test_huynya_scenarios()