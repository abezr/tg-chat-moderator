"""Test file for analyzing political discussion false positives."""

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


# Political discussion false positive cases to analyze
POLITICAL_FALSE_POSITIVES = [
    {
        "name": "Genocide Discussion - Ethnic Cleansing",
        "text": "Да людей зачищают, этот чистый геноцид, но люди еще на что то надеется 🙏",
        "sender": "PoliticalUser",
        "expected": "ok",
        "reason": "Political discussion about genocide - not a call for violence",
        "current_behavior": "ban",
        "current_reason": "Contains 'геноцид' (genocide) - triggers violence rule"
    },
    {
        "name": "Political Rhetoric - топит (metaphorical)",
        "text": "Ну да, у таких как ты партаппаратчиков все было хорошо, а потом видимо не успели хапнуть госсобственности, теперь опять топят чтобы дураки бесплатно на вас работали",
        "sender": "PoliticalUser2",
        "expected": "ok",
        "reason": "'топит' means political destruction/slandering, NOT actual burning",
        "current_behavior": "ban", 
        "current_reason": "Contains 'топят' - triggers violence rule"
    },
    {
        "name": "Slang - ствол (figurative)",
        "text": "С таким стволом как у тебя везде можно 🙏",
        "sender": "SlangUser",
        "expected": "ok",
        "reason": "'ствол' is slang for gun or figuratively 'power/influence' - not vulgar in this context",
        "current_behavior": "ban",
        "current_reason": "Contains 'ствол' - flagged as vulgar (матюк)"
    }
]


def parse_verdict(content: str) -> dict:
    """Parse the LLM response to extract the verdict JSON."""
    content = content.strip()
    
    # Handle simple "ok" text response
    if content.lower() == "ok":
        return {"verdict": "ok", "reason": "Simple ok response", "reply": ""}
    
    # Remove markdown code blocks if present
    if content.startswith("```"):
        lines = content.split("\n")
        if len(lines) > 2:
            content = "\n".join(lines[1:-1])
    
    # Try to parse as JSON
    try:
        result = json.loads(content)
        if "verdict" not in result:
            if "role" in result and len(result) == 1:
                return {"verdict": "ok", "reason": "LLM returned role instead of verdict", "reply": ""}
        return result
    except json.JSONDecodeError:
        # Try to extract JSON with regex
        match = re.search(r'\{.*\}', content, re.DOTALL)
        if match:
            try:
                result = json.loads(match.group())
                if "verdict" not in result and "role" in result and len(result) == 1:
                    return {"verdict": "ok", "reason": "LLM returned role instead of verdict", "reply": ""}
                return result
            except json.JSONDecodeError:
                pass
        if '"verdict"' in content and '"ok"' in content:
            return {"verdict": "ok", "reason": "Extracted from malformed JSON", "reply": ""}
        raise ValueError(f"Could not parse JSON from response: {content[:200]}")


async def test_political_cases():
    """Test all political discussion false positive cases."""
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
    
    results = []
    
    async with client:
        for test_case in POLITICAL_FALSE_POSITIVES:
            print(f"\n{'='*60}")
            print(f"Testing: {test_case['name']}")
            print(f"Text: {test_case['text']}")
            print(f"Expected: {test_case['expected']} (but currently returns: {test_case['current_behavior']})")
            print(f"Current reason: {test_case['current_reason']}")
            print("-" * 60)
            
            messages = prompt_builder.build_messages(
                message_text=test_case["text"],
                sender_name=test_case["sender"]
            )
            
            try:
                response = await client.chat(messages)
                verdict = parse_verdict(response.content)
                
                print(f"Actual verdict: {verdict.get('verdict', 'UNKNOWN')}")
                print(f"Reason: {verdict.get('reason', 'N/A')}")
                print(f"Full response: {response.content[:300]}")
                
                results.append({
                    "name": test_case["name"],
                    "text": test_case["text"],
                    "expected": test_case["expected"],
                    "actual": verdict.get("verdict", "ERROR"),
                    "reason": verdict.get("reason", "N/A"),
                    "passed": verdict.get("verdict") == test_case["expected"]
                })
                
            except Exception as e:
                print(f"ERROR: {e}")
                results.append({
                    "name": test_case["name"],
                    "text": test_case["text"],
                    "expected": test_case["expected"],
                    "actual": "ERROR",
                    "reason": str(e),
                    "passed": False
                })
    
    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print("="*60)
    
    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    
    print(f"Passed: {passed}/{total}")
    
    for r in results:
        status = "✓ PASS" if r["passed"] else "✗ FAIL"
        print(f"\n{status}: {r['name']}")
        print(f"  Expected: {r['expected']}, Got: {r['actual']}")
        print(f"  Analysis: {r.get('reason', 'N/A')[:100]}...")
    
    return results


if __name__ == "__main__":
    asyncio.run(test_political_cases())
