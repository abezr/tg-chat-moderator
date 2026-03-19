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
        "text": "Да людей зачищают, этот чистый геноцид, но люди еще на что то надеется",
        "sender": "PoliticalUser",
        "expected": "ok",
        "reason": "Political discussion about genocide - not a call for violence",
        "current_behavior": "ban",
        "current_reason": "Contains genocide keyword"
    },
    {
        "name": "Political Rhetoric - топит (metaphorical)",
        "text": "Ну да, у таких как ты партаппаратчиков все было хорошо, а потом видимо не успели хапнуть госсобственности, теперь опять топят чтобы дураки бесплатно на вас работали",
        "sender": "PoliticalUser2",
        "expected": "ok",
        "reason": "топит means political destruction, NOT actual burning",
        "current_behavior": "ban", 
        "current_reason": "Contains топят - triggers violence"
    },
    {
        "name": "Slang - ствол (figurative)",
        "text": "С таким стволом как у тебя везде можно",
        "sender": "SlangUser",
        "expected": "ok",
        "reason": "ствол is slang for gun or figuratively power/influence",
        "current_behavior": "ban",
        "current_reason": "Contains ствол - vulgar"
    }
]


def parse_verdict(content: str) -> dict:
    """Parse the LLM response to extract the verdict JSON."""
    content = content.strip()
    
    if content.lower() == "ok":
        return {"verdict": "ok", "reason": "Simple ok response", "reply": ""}
    
    if content.startswith("```"):
        lines = content.split("\n")
        if len(lines) > 2:
            content = "\n".join(lines[1:-1])
    
    try:
        result = json.loads(content)
        if "verdict" not in result:
            if "role" in result and len(result) == 1:
                return {"verdict": "ok", "reason": "LLM returned role instead of verdict", "reply": ""}
        return result
    except json.JSONDecodeError:
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
            messages = prompt_builder.build_messages(
                message_text=test_case["text"],
                sender_name=test_case["sender"]
            )
            
            try:
                response = await client.chat(messages)
                verdict = parse_verdict(response.content)
                
                results.append({
                    "name": test_case["name"],
                    "text": test_case["text"],
                    "expected": test_case["expected"],
                    "actual": verdict.get("verdict", "ERROR"),
                    "reason": verdict.get("reason", "N/A"),
                    "raw_response": response.content[:500],
                    "passed": verdict.get("verdict") == test_case["expected"]
                })
                
            except Exception as e:
                results.append({
                    "name": test_case["name"],
                    "text": test_case["text"],
                    "expected": test_case["expected"],
                    "actual": "ERROR",
                    "reason": str(e),
                    "passed": False
                })
    
    # Write results to file
    with open("tests/political_test_results.txt", "w", encoding="utf-8") as f:
        f.write("=" * 60 + "\n")
        f.write("POLITICAL FALSE POSITIVE TEST RESULTS\n")
        f.write("=" * 60 + "\n\n")
        
        passed = sum(1 for r in results if r["passed"])
        total = len(results)
        
        f.write(f"Passed: {passed}/{total}\n\n")
        
        for r in results:
            status = "PASS" if r["passed"] else "FAIL"
            f.write(f"\n{'='*60}\n")
            f.write(f"Test: {r['name']}\n")
            f.write(f"Text: {r['text'][:50]}...\n")
            f.write(f"Expected: {r['expected']}, Got: {r['actual']}\n")
            f.write(f"Reason: {r['reason']}\n")
            f.write(f"Status: {status}\n")
            if "raw_response" in r:
                f.write(f"Raw: {r['raw_response'][:200]}\n")
    
    return results


if __name__ == "__main__":
    asyncio.run(test_political_cases())
