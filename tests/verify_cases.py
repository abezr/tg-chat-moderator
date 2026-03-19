import asyncio
import json
import sys
from pathlib import Path

# Fix encoding for Windows terminal
try:
    sys.stdout.reconfigure(encoding='utf-8')
except AttributeError:
    pass # Not supported on all Python versions/environments

# Add src to path
sys.path.append(str(Path(__file__).parent.parent))

from src.config import load_config
from src.llm.client import LLMClient
from src.llm.prompts import ModerationPromptBuilder

async def verify():
    print("🚀 Starting verification of moderation false positives...")
    
    config = load_config("config/config.yaml")
    
    # We use local LLM for verification if available, otherwise OpenRouter
    # Given the user's config, provider='both'
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
            "name": "Political Rumor/Fear",
            "text": "Да все кто в курсе и кто очевидцы, а такие есть люди которые кое что видели сопоставили, они молчат и вообще не говорят в открытую о таком, бояться что их просто за такую инфу могут грохнуть спецслужбы 🙏",
            "sender": "Берендей (@berendei_777)"
        },
        {
            "name": "Observation of Illegal Activity",
            "text": "Контрабандисты и по сей день делают бизнес с турецкими коллегами!!!😁😊👍😇 Им на всякие войны кабакку ложить, ибо существует необходимость прокормиться...😢😢🤷♂🤷♂☹️",
            "sender": "Johnni"
        },
        {
            "name": "Political Prediction/Warning",
            "text": "Нет. Ты видел села, города, через которые прошли РФия? 🤔\nТы уверен, что выживишь, а если и выживишь тебя возьмут в плен или расстреляют злые русские или чеченцы!",
            "sender": "Берендей"
        },
        {
            "name": "News Post with Footer",
            "text": "В ОАЭ горит нефтебаза в Фуджейре.\n\nРанее иранский БПЛА атаковал электростанцию.\n\nСайт \"Страна\" (https://stranaua.media/) | X/Twitter (https://x.com/stranaua?s=21) | Прислать новость/фото/видео (https://t.me/strana_news_bot) | Реклама на канале (https://t.me/@strana_adv_bot) | Помощь (https://t.me/stranaua/137068)",
            "sender": "Берендей"
        },
        {
            "name": "Sarcastic Consulting",
            "text": "Так это с ихних же слов. Так и ты запусти стрим, и говори тоже самое- мне за наличку платят деньги, и даю консультации в частном порядке, поэтому мои слова - золото!! Нормально, для лохов прокатит",
            "sender": "El"
        },
        {
            "name": "Spiritual Consulting (Free)",
            "text": "Нет, я же по факту не даю в частном порядке консультацию по ряду вопросов, допустип по геополитическим или экономическим, по духовным могу давать, но за это деньги не берут 🙏",
            "sender": "Берендей"
        },
        {
            "name": "Testing Repetition Bug (Nonsense Input)",
            "text": "вусщву Судется выража начала в 2014 году. В период з 2014-2022 нихто неконечно ничено заёбували з други мовенья.",
            "sender": "Unknown"
        },
        {
            "name": "Language Discussion",
            "text": "Я наприклад свою дитину української вчити не буду. Вона їй точно в житті не пригодиться. Вдома буду спілкуватись російською",
            "sender": "Yuriy 🇷🇴 (@Yuriy_SQA)"
        },
        {
            "name": "Food Prices (Review)",
            "text": "Дорого\\мало, але смачно\n9 Харчі, 100% Суб.Риба см.з ан.у верш.соусі 50г - 169₴\n8 Харчі, 100% Суб.Укр.борщ зі свининою 65г  - 169₴\n8 Харчі, 100% Суб.Індич.у верш.-гриб.соусі 65г  - 169₴ \n4 Харчі, 100% Суб.Гуляш зі свин.з том.соус 65г  - 169₴",
            "sender": "J"
        },
        {
            "name": "Short Opinion",
            "text": "Не наоборот все чтеко",
            "sender": "Kirill (@derristy)"
        },
        {
            "name": "Danger Warning",
            "text": "В Тегеране ад",
            "sender": "DіnFighter (@DinFighter777)"
        },
        {
            "name": "Hypothetical Advice",
            "text": "Вариант. Доехал до границы области. Находишь таксиста, который знает, как без проблем переехать в другую  область и так далее.",
            "sender": "Сергій"
        },
        {
            "name": "Meta-Discussion",
            "text": "но админ писал там надо писать 24/7 в тексте",
            "sender": "Kirill (@derristy)"
        },
        {
            "name": "Tool Recommendation",
            "text": "шлейф машину взять с аккумулятором))",
            "sender": "Kirill"
        }
    ]
    
    all_passed = True
    
    async with client:
        for case in test_cases:
            print(f"\n--- Testing Case: {case['name']} ---")
            print(f"Sender: {case['sender']}")
            # Use ascii-safe slice for preview
            preview = case['text'][:100].encode('ascii', 'ignore').decode('ascii')
            print(f"Message: {preview}...")
            
            messages = prompt_builder.build_messages(
                message_text=case['text'],
                sender_name=case['sender']
            )
            
            try:
                response = await client.chat(messages)
                print(f"Raw Output: {response.content}")
                
                # Simple parser (mimic ModerationEngine._parse_verdict)
                content = response.content.strip()
                if content.startswith("```"):
                    content = "\n".join(content.split("\n")[1:-1])
                
                try:
                    verdict = json.loads(content)
                except Exception:
                    # Try regex for JSON
                    import re
                    match = re.search(r'\{.*\}', content, re.DOTALL)
                    if match:
                        verdict = json.loads(match.group())
                    else:
                        raise ValueError("Could not parse JSON")
                
                print(f"Parsed Verdict: {verdict['verdict']}")
                print(f"Reason: {verdict.get('reason', 'N/A')}")
                
                if verdict['verdict'] == 'ok':
                    print("✅ PASSED: Verdict is 'ok'")
                else:
                    print(f"❌ FAILED: Verdict is '{verdict['verdict']}' (expected 'ok')")
                    all_passed = False
                    
            except Exception as e:
                print(f"❌ ERROR: {e}")
                all_passed = False
                
    if all_passed:
        print("\n✨ ALL VERIFICATION CASES PASSED! ✨")
    else:
        print("\n⚠️ SOME CASES FAILED. Prompt might need more adjustment.")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(verify())
