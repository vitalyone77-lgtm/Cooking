"""
Обёртка над LLM-провайдерами. Groq и OpenRouter оба используют
OpenAI-совместимый формат /chat/completions, поэтому логика общая.
"""
import logging
import httpx

import config
from prompts import SYSTEM_PROMPT, build_user_prompt

logger = logging.getLogger(__name__)


async def _call_llm(messages: list[dict]) -> str:
    if config.LLM_PROVIDER == "openrouter":
        url = config.OPENROUTER_URL
        headers = {
            "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://t.me",
            "X-Title": "kitchen-bot",
        }
        model = config.OPENROUTER_MODEL
    else:  # groq по умолчанию
        url = config.GROQ_URL
        headers = {
            "Authorization": f"Bearer {config.GROQ_API_KEY}",
            "Content-Type": "application/json",
        }
        model = config.GROQ_MODEL

    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.6,
        "max_tokens": 2000,
    }

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]


async def generate_recipe(data: dict, search_query: str, search_results_text: str) -> str:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_prompt(data, search_query, search_results_text)},
    ]
    try:
        return await _call_llm(messages)
    except Exception as e:
        logger.exception("Ошибка вызова LLM")
        return (
            "😔 Не получилось получить рецепт от ИИ (проблема с API-ключом или сервисом).\n"
            f"Техническая причина: {e}\n\n"
            "Проверь ключи в файле .env и попробуй ещё раз."
        )
