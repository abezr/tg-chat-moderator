"""Test for insect and laser camera detector link case."""
import asyncio
import json
import sys
import io
from pathlib import Path

# Fix encoding for Windows terminal
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import load_config
from src.llm.client import LLMClient
from src.llm.prompts import ModerationPromptBuilder


async def test_insect_camera_detector():
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
            'name': 'Insect and Laser Camera Detector Link',
            'text': 'https://example.com/insect-laser-camera-detector',
            'sender': 'TestUser'
        },
        {
            'name': 'Insect and Laser Camera Detector with Text',
            'text': 'Перевірте цей детектор комах та лазерних камер: https://example.com/insect-laser-camera-detector',
            'sender': 'TestUser'
        },
        {
            'name': 'Insect and Laser Camera Detector for Privacy',
            'text': 'Детектор комах та лазерних камер для захисту приватності: https://example.com/insect-laser-camera-detector',
            'sender': 'TestUser'
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
                        'not a violation' in content_lower or 'ok' in content_lower):
                        verdict = 'ok'
                        reason = f"LLM response indicates OK: {content[:100]}..."
                    else:
                        verdict = 'violation'
                        reason = f"JSON parse failed: {e} - Response: {content[:100]}..."
            
            results.append({
                'name': case['name'],
                'text': case['text'],
                'verdict': verdict,
                'reason': reason
            })
            
            print(f"Verdict: {verdict}")
            try:
                print(f"Reason: {reason}")
            except UnicodeEncodeError:
                print(f"Reason: {reason.encode('utf-8', 'replace').decode('utf-8')}")
            try:
                print(f"Raw response: {content}")
            except UnicodeEncodeError:
                print(f"Raw response: {content.encode('utf-8', 'replace').decode('utf-8')}")
    
    # Write detailed results
    output_file = 'insect_camera_detector_test_results.json'
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    
    print(f"\n{'='*60}")
    print(f"Test results saved to: {output_file}")
    
    # Summary
    print(f"\n{'='*60}")
    print("Test Summary")
    print(f"{'='*60}")
    for result in results:
        status = '✅' if result['verdict'] == 'ok' else '❌'
        try:
            print(f"{status} {result['name']}: {result['verdict']}")
        except UnicodeEncodeError:
            print(f"{status} {result['name'].encode('utf-8', 'replace').decode('utf-8')}: {result['verdict']}")


if __name__ == "__main__":
    asyncio.run(test_insect_camera_detector())
