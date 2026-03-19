#!/usr/bin/env python3
"""Test military context "хуйня" case specifically."""

import asyncio
import json
import sys
import re
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from src.config import load_config
from src.llm.client import LLMClient
from src.llm.prompts import ModerationPromptBuilder

async def test_military_huynya():
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
    
    messages = prompt_builder.build_messages(
        message_text="Нам дали всю цю хуйню для оборони",
        sender_name="TestUser"
    )
    
    print("Testing military context message: 'Нам дали всю цю хуйню для оборони'")
    print("=" * 50)
    
    async with client:
        response = await client.chat(messages)
        content = response.content.strip()
        
        print(f"Raw response: {content}")
        
        try:
            if content.startswith("```"):
                content = "\n".join(content.split("\n")[1:-1])
                
            verdict = json.loads(content)
            print(f"Parsed Verdict: {verdict['verdict']}")
            print(f"Reason: {verdict.get('reason', 'N/A')}")
            if 'reply' in verdict and verdict['reply']:
                print(f"Reply: {verdict['reply']}")
                
        except Exception as e:
            print(f"Error parsing response: {e}")
            match = re.search(r'\{.*\}', content, re.DOTALL)
            if match:
                print(f"Extracted JSON: {match.group()}")
            raise

if __name__ == "__main__":
    import os
    if os.name == 'nt':
        import sys
        import codecs
        sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer)
        sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer)
    
    try:
        asyncio.run(test_military_huynya())
    except Exception as e:
        print(f"\nError: {e}")
        sys.exit(1)