"""
Test file for verifying TRUE POSITIVE cases are handled correctly.

These tests verify that messages containing slurs, including letter replacements,
are correctly identified as violations by the moderation engine.

This addresses the FALSE NEGATIVE case:
- Message: "Бот который чекает сообщения в групе πидарас"
- Expected: This should be flagged (not ok)
- Previous verdict: ok (incorrect)
"""

import asyncio
import json
import sys
import re
from pathlib import Path

# Add src to path
sys.path.append(str(Path(__file__).parent.parent))

from src.config import load_config
from src.llm.client import LLMClient
from src.llm.prompts import ModerationPromptBuilder


# Test cases that should be flagged (true positives)
TRUE_POSITIVE_CASES = [
    {
        "name": "Slur with Greek Letter Replacement",
        "text": "Бот который чекает сообщения в групе πидарас",
        "sender": "TestUser",
        "description": "Message contains slur with letter replacement (Greek π for Cyrillic п) - should be flagged"
    },
    {
        "name": "Slur with Letter Replacement - Variation 1",
        "text": "πидарас - проверка на матюки",
        "sender": "TestUser",
        "description": "Direct use of slur with Greek letter replacement - should be flagged"
    },
    {
        "name": "Slur with Letter Replacement - Variation 2",
        "text": "Підарас - ще один тест",
        "sender": "TestUser",
        "description": "Slur with i instead of и - should be flagged"
    },
    {
        "name": "External Group Mention - UFM",
        "text": "на UFM рекомендовали к покупке Болторіз YATO 350мм (YT-1844)",
        "sender": "ProductRecommender",
        "description": "Mentioning external group UFM and recommending products - should be flagged"
    },
    {
        "name": "External Group + Slur Combined",
        "text": "UFM πидараси",
        "sender": "Violator",
        "description": "Contains both external group mention and slur - should be flagged"
    },
    {
        "name": "Violent Content - Encouraging Killing",
        "text": "надо убивать мерзавцев",
        "sender": "ViolentUser",
        "description": "Must be flagged - encouraging killing"
    },
    {
        "name": "Violent Content - Kill The Bastards",
        "text": "убить мерзавцев",
        "sender": "ViolentUser",
        "description": "Must be flagged - encouraging killing"
    },
    {
        "name": "Violent Content - Reduce The Population",
        "text": "поголовье уродов подсократить",
        "sender": "ViolentUser", 
        "description": "Must be flagged - encouraging genocide"
    },
    {
        "name": "Violent Content - Full Message",
        "text": "Пусть не морочит голову... надо убивать мерзавцев... поголовье уродов подсократить",
        "sender": "ViolentUser",
        "description": "Must be flagged - combined violent content"
    },
    {
        "name": "Violent Content - Knife Attack",
        "text": "ножом по артерии",
        "sender": "ViolentUser",
        "description": "Must be flagged - violent weapon threat"
    },
    {
        "name": "Personal Insult - Vulgar Idiot (долбоёба)",
        "text": "Жду ответа, не надо изображать из себя долбоёба",
        "sender": "InsultUser",
        "expected_verdict": "mute",
        "description": "Message contains vulgar personal insult 'долбоёба' - should be flagged with mute action"
    },
]


async def run_tests():
    """Run all true positive test cases."""
    print("🚀 Running TRUE POSITIVE verification tests...")
    
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
    
    passed_count = 0
    failed_cases = []
    
    async with client:
        for case in TRUE_POSITIVE_CASES:
            print(f"\n--- Testing Case: {case['name']} ---")
            print(f"Sender: {case['sender']}")
            preview = case['text'][:80].encode('ascii', 'ignore').decode('ascii')
            print(f"Message: {preview}...")
            
            messages = prompt_builder.build_messages(
                message_text=case['text'],
                sender_name=case['sender']
            )
            
            try:
                response = await client.chat(messages)
                content = response.content.strip()
                
                # Parse JSON response
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
                
                print(f"Parsed Verdict: {verdict['verdict']}")
                print(f"Reason: {verdict.get('reason', 'N/A')}")
                
                if verdict['verdict'] != 'ok':
                    print("✅ PASSED: Message correctly flagged as violation")
                    passed_count += 1
                else:
                    print("❌ FAILED: Message should have been flagged but got 'ok'")
                    failed_cases.append(case['name'])
                    
            except Exception as e:
                print(f"❌ ERROR: {e}")
                failed_cases.append(case['name'])
    
    return passed_count, failed_cases


def run_sync_test():
    """Run tests synchronously."""
    passed, failed = asyncio.run(run_tests())
    
    print("\n" + "=" * 60)
    print(f"📊 Results: {passed}/{len(TRUE_POSITIVE_CASES)} tests passed")
    
    if failed:
        print("\n❌ Failed cases:")
        for name in failed:
            print(f"   - {name}")
        return 1
    else:
        print("\n✨ All true positive tests passed!")
        return 0


if __name__ == "__main__":
    sys.exit(run_sync_test())
