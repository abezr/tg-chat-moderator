"""Quick test for commercial marketplace link cases."""
import asyncio
import json
import sys
import io
from pathlib import Path

# Fix encoding for Windows terminal
io.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
io.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import load_config
from src.llm.client import LLMClient
from src.llm.prompts import ModerationPromptBuilder


async def test_specific_cases():
    config = load_config('config/config.yaml')
    
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
            'name': 'Equipment Recommendation with Rozetka Link',
            'text': '''Народ, берите титановые (если есть бабки) или пластиковые (не одноразовые, а нормальные пластиковые, которые для туристов). Никакого обычного металла брать не надо.

Вот с такой шёл:
https://rozetka.com.ua/humangear_022_0059/p209293285/

1. Лёгкая.
2. Крепкая.
3. Выполняет свою функцию.''',
            'sender': 'Bob'
        },
        {
            'name': 'Titanium Spoon Travel Recommendation',
            'text': 'Народ, берите титановые (если есть бабки) или пластиковые... Вот с такой шёл: https://rozetka.com.ua/humangear_022_0059/p209293285/ 1. Лёгкая. 2. Крепкая. 3. Выполняет свою функцию.',
            'sender': 'Bob'
        },
        {
            'name': 'Amazon Link for Hiking Gear',
            'text': 'Купил такой рюкзак на Amazon, отличная вещь для походов',
            'sender': 'Traveler'
        },
        {
            'name': 'Prom.ua Tool Recommendation',
            'text': 'Посоветуйте, какой инструмент купить на Prom.ua для ремонта',
            'sender': 'Helper'
        }
    ]
    
    results = []
    
    async with client:
        for case in test_cases:
            print(f"\n{'='*60}")
            print(f"Testing: {case['name']}")
            print(f"{'='*60}")
            
            messages = prompt_builder.build_messages(
                message_text=case['text'],
                sender_name=case['sender']
            )
            
            response = await client.chat(messages)
            content = response.content.strip()
            
            # Parse verdict
            if content.lower() == 'ok':
                verdict = 'ok'
                reason = 'Simple ok response'
            else:
                # Try to parse JSON
                if content.startswith('```'):
                    lines = content.split('\n')
                    content = '\n'.join(lines[1:-1])
                
                try:
                    result = json.loads(content)
                    verdict = result.get('verdict', 'unknown')
                    reason = result.get('reason', 'N/A')
                except Exception as e:
                    # Check if the response contains 'not a violation' or similar
                    content_lower = content.lower()
                    if ('не порушення' in content_lower or 'не порушен' in content_lower or 
                        'not a violation' in content_lower or 'not violation' in content_lower or
                        'нет' in content_lower or 'no' in content_lower[:10] or
                        'ok' in content_lower[:10] or 'дозволено' in content_lower):
                        verdict = 'ok'
                        reason = 'LLM indicated not a violation in text response: ' + content[:100]
                    else:
                        verdict = 'parse_error'
                        reason = content[:200]
            
            print(f"Verdict: {verdict}")
            print(f"Reason: {reason}")
            
            if verdict == 'ok':
                print('✅ PASS')
                results.append({'name': case['name'], 'passed': True})
            else:
                print('❌ FAIL')
                results.append({'name': case['name'], 'passed': False, 'verdict': verdict, 'reason': reason})
    
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    passed = sum(1 for r in results if r['passed'])
    total = len(results)
    print(f"Passed: {passed}/{total}")
    
    for r in results:
        status = '✅' if r['passed'] else '❌'
        print(f"{status} {r['name']}")


if __name__ == "__main__":
    asyncio.run(test_specific_cases())
