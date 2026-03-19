"""
Quick test to check vulgar term regression
"""

import asyncio
import json
import sys
import io
from pathlib import Path

# Fix Windows console encoding
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import load_config
from src.llm.client import LLMClient
from src.llm.prompts import ModerationPromptBuilder


async def test_message(message_text: str, sender: str = "TestUser"):
    """Test a single message."""
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
    )
    prompt_builder.load_system_prompt()
    
    messages = prompt_builder.build_messages(
        message_text=message_text,
        sender_name=sender,
    )
    
    async with client:
        response = await client.chat(messages)
        content = response.content.strip()
        
        print(f"Message: {message_text}")
        
        # Parse JSON
        try:
            verdict = json.loads(content)
            print(f"Verdict: {verdict['verdict']}")
            print(f"Reason: {verdict.get('reason', 'N/A')}")
            return verdict
        except (json.JSONDecodeError, KeyError):
            # Try to extract JSON
            import re
            match = re.search(r'\{.*\}', content, re.DOTALL)
            if match:
                try:
                    verdict = json.loads(match.group())
                    print(f"Verdict: {verdict['verdict']}")
                    print(f"Reason: {verdict.get('reason', 'N/A')}")
                    return verdict
                except:
                    pass
            print(f"Raw response: {content[:500]}")
            return None


async def main():
    # Test cases to understand the pattern
    test_cases = [
        # Test 1: Standalone vulgar - should be warn/ban
        ("πидараси", "Expected: warn/ban"),
        
        # Test 2: Bot mention + vulgar - REGRESSION
        ("Бот который чекает сообщения в групе πидарас", "Expected: ban (REGRESSION)"),
        
        # Test 3: Simple vulgar
        ("пидарас", "Expected: warn/ban"),
        
        # Test 4: Bot insult - should be OK per line 149
        ("бот тупий", "Expected: ok (insult to bot)"),
        
        # Test 5: підарас (different letter)
        ("підарас", "Expected: warn/ban"),
        
        # Test 6: πидарас (singular form)
        ("πидарас", "Expected: warn/ban"),
        
        # Test 7: Pure insult to bot with vulgar
        ("бот підарас", "Expected: ok? (insult to bot)"),
    ]
    
    for text, expected in test_cases:
        print("=" * 60)
        print(f"{text}")
        print(f"Expected: {expected}")
        print("-" * 60)
        await test_message(text)
        print()


if __name__ == "__main__":
    asyncio.run(main())
