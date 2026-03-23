#!/usr/bin/env python3
"""
Test file for verifying the new false positives from user feedback.
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


def test_new_false_positives():
    """Test the new false positive cases from user feedback."""
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
    
    # Test cases from user feedback (all should be OK)
    test_cases = [
        ("Pentagon budget news", "Пентагон хочет запросить у Конгресса еще более 200 млрд долл. на войну с Ираном, сообщает The Washington Post со ссылкой на источники.\n\nСейчас Минобороны обратилось за одобрением этой суммы в Белый дом. Но некоторые чиновники администрации считают, что такой запрос не имеет шансов на одобрение в Конгрессе", "ok"),
        ("Social commentary about people", "Ничего такого он не говорит. Потому что этого никто и не скрывает. В США регулярно прям серьезные книги пишутся и издаються, и одну из я сейчас читаю, и уже далеко не перву./// То о чем говорит этот парень, ещё Джек Лондон тщательно описывал в начале 20-го века. Почему это возможно - потому что люди в основной массе быдло, корым это не интересно в принципе, пока есть хоть кусочек колбаски на столе.", "ok"),
        ("Bear fire question", "Фаєр який можна примінити от ведмедя", "ok"),
    ]
    
    print("Testing new false positive cases from user feedback:")
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
                    
                    actual_verdict = verdict['verdict'].lower()
                    expected_verdict = expected_verdict.lower()
                    
                    print(f"Actual: {actual_verdict}")
                    print(f"Reason: {verdict.get('reason', 'N/A')}")
                    if 'reply' in verdict and verdict['reply']:
                        print(f"Reply: {verdict['reply']}")
                        
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
    test_new_false_positives()
