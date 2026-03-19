#!/usr/bin/env python3
"""Test war zone situation report false positive case."""

import asyncio
import json
import re
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import load_config
from src.llm.client import LLMClient
from src.llm.prompts import ModerationPromptBuilder


async def test_war_zone_false_positive():
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
    
    # Test case: War zone situation report (reconstructed from fragments)
    # The message describes what's happening in a war zone area:
    # - "палят" (burn) - houses/areas being burned
    # - "запускают патрулировать" (deploying patrols)
    # - "Санта Клаусы" - slang for people in uniform
    # - "патрулирование в зоне 2-3 км" - patrolling in zone 2-3km
    
    message_text = "В районе палят дома, запускают патрулировать Санта Клаусы, патрулирование в зоне 2-3 км"
    
    messages = prompt_builder.build_messages(
        message_text=message_text,
        sender_name="TestUser"
    )
    
    print(f"Testing war zone situation report: '{message_text}'")
    print("=" * 60)
    print("This message describes what's happening in a war zone area.")
    print("It should be OK (war zone discussion/news), not BAN.")
    print("=" * 60)
    
    async with client:
        response = await client.chat(messages)
        content = response.content.strip()
        
        print(f"\nRaw response: {content}")
        
        try:
            if content.startswith("```"):
                content = "\n".join(content.split("\n")[1:-1])
                
            verdict = json.loads(content)
            print(f"\n=== RESULT ===")
            print(f"Verdict: {verdict['verdict']}")
            print(f"Reason: {verdict.get('reason', 'N/A')}")
            if 'reply' in verdict and verdict['reply']:
                print(f"Reply: {verdict['reply']}")
                
            if verdict['verdict'] == 'ok':
                print("\n✅ CORRECT: War zone situation report is allowed")
            else:
                print("\n❌ FALSE POSITIVE: War zone situation report incorrectly flagged")
                
        except Exception as e:
            print(f"Error parsing response: {e}")
            match = re.search(r'\{.*\}', content, re.DOTALL)
            if match:
                print(f"Extracted JSON: {match.group()}")
            raise


async def test_war_zone_alternative_phrasing():
    """Test variations of war zone messages."""
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
        {
            "name": "War zone patrol report",
            "text": "В районе патрулирование, Санта Клаусы в зоне 2-3 км"
        },
        {
            "name": "War zone burning report", 
            "text": "Там палят дома, не советую ехать в этот район"
        },
        {
            "name": "Danger warning - shots",
            "text": "Если услышите выстрелы - остановитесь и ждите"
        },
        {
            "name": "Danger warning - armed people",
            "text": "Если увидите вооруженных людей - спрячьтесь"
        }
    ]
    
    print("\n" + "=" * 60)
    print("Testing additional war zone cases")
    print("=" * 60)
    
    async with client:
        for case in test_cases:
            print(f"\n--- Test: {case['name']} ---")
            print(f"Message: {case['text']}")
            
            messages = prompt_builder.build_messages(
                message_text=case['text'],
                sender_name="TestUser"
            )
            
            response = await client.chat(messages)
            content = response.content.strip()
            
            try:
                if content.startswith("```"):
                    content = "\n".join(content.split("\n")[1:-1])
                verdict = json.loads(content)
                print(f"Verdict: {verdict['verdict']} | Reason: {verdict.get('reason', 'N/A')}")
            except Exception as e:
                print(f"Error: {e}")


if __name__ == "__main__":
    import os
    if os.name == 'nt':
        import sys
        import codecs
        sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer)
        sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer)
    
    try:
        asyncio.run(test_war_zone_false_positive())
        asyncio.run(test_war_zone_alternative_phrasing())
    except Exception as e:
        print(f"\nError: {e}")
        sys.exit(1)
