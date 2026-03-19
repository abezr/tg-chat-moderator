"""Quick test script to verify false positive cases."""
import asyncio
import json
import re
import sys
from pathlib import Path
# -*- coding: utf-8 -*-
"""Quick test script to verify false positive cases."""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Add src to path
import sys
sys.path.insert(0, '.')

from src.config import load_config
from src.llm.client import LLMClient
from src.llm.prompts import ModerationPromptBuilder


async def test_message(text, sender):
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
    
    async with client:
        messages = prompt_builder.build_messages(
            message_text=text,
            sender_name=sender
        )
        response = await client.chat(messages)
        
        # Try to parse JSON
        content = response.content.strip()
        print(f'DEBUG raw response: {content[:500]}')
        if content.startswith('```'):
            lines = content.split('\n')
            content = '\n'.join(lines[1:-1])
        try:
            result = json.loads(content)
        except:
            # Try regex
            match = re.search(r'\{.*\}', content, re.DOTALL)
            if match:
                result = json.loads(match.group())
            else:
                result = {'raw': content[:200]}
        
        print(f'Text: {text}')
        print(f'Verdict: {result.get("verdict", "unknown")}')
        print(f'Reason: {result.get("reason", "N/A")}')
        if 'raw' in result:
            print(f'Raw: {result.get("raw")}')
        print('---')
        return result


async def main():
    # Test Case 1: Political commentary about deportation
    print('=== CASE 1: Political deportation commentary ===')
    await test_message(
        'Что ж, камешек покатился. Но пока о принудительной депортации домой (кроме Польши) я не слышал.',
        'TestUser'
    )

    # Test Case 2: Safety warning about gunshots
    print('=== CASE 2: Safety warning about gunshots ===')
    await test_message(
        'И ещё если слышите выстрелы где то то сразу стоп на месте и ждите так как будут гнаться за одним и вы попадете у нас так было',
        'TestUser'
    )


if __name__ == '__main__':
    asyncio.run(main())
