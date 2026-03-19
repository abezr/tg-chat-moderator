"""Test script for "бомба" figure of speech false positive."""

import asyncio
import json
import sys
import os
from pathlib import Path

# Fix encoding for Windows
if sys.platform == 'win32':
    os.environ['PYTHONIOENCODING'] = 'utf-8'
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# Add src to path
sys.path.append(str(Path(__file__).parent.parent))

from src.config import load_config
from src.llm.client import LLMClient
from src.llm.prompts import ModerationPromptBuilder


async def test_bomba_figure_of_speech():
    """Test that 'бомба по отзывам' (meaning 'awesome according to reviews') is correctly identified as ok."""
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
    
    # Test message: "By the way, better to make alcohol tincture on leaves, bomb according to reviews"
    # "бомба по отзывам" means "awesome/great according to reviews" - a figure of speech
    test_message = "Кстати, лучше делать спиртовую настойку на листьях, бомба по отзывам."
    
    print(f"Testing message: {test_message}")
    print("=" * 60)
    
    async with client:
        messages = prompt_builder.build_messages(
            message_text=test_message,
            sender_name="TestUser"
        )
        
        # Print the prompt being sent
        print("\n=== SYSTEM PROMPT (first 500 chars) ===")
        print(messages[0].content[:500])
        print("\n=== USER MESSAGE ===")
        print(messages[1].content)
        
        response = await client.chat(messages)
        
        print("\n=== LLM RESPONSE ===")
        print(response.content)
        
        # Try to parse JSON
        try:
            # Find JSON in response
            content = response.content
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]
            
            verdict = json.loads(content.strip())
            print("\n=== PARSED VERDICT ===")
            print(f"Verdict: {verdict.get('verdict')}")
            print(f"Reason: {verdict.get('reason', 'N/A')}")
            
            if verdict.get('verdict') == 'ban':
                print("\n❌ FALSE POSITIVE CONFIRMED: Message about alcohol tincture was incorrectly flagged as violence!")
                print("The word 'бомба' here means 'awesome/great' (figure of speech), not an actual bomb/weapon")
            else:
                print("\n✅ OK: Message correctly identified as not a violation")
                
        except Exception as e:
            print(f"\nError parsing response: {e}")


if __name__ == "__main__":
    asyncio.run(test_bomba_figure_of_speech())
