"""
Обёртка над LLM-провайдерами. DeepSeek, Groq и OpenRouter все используют
OpenAI-совместимый формат /chat/completions, поэтому логика общая.

Если основной провайдер (config.LLM_PROVIDER) не ответил или вернул пустой текст —
автоматически пробуем следующего в LLM_FALLBACK_ORDER. Это нужно, чтобы сбой/пустой
ответ одной модели не приводил к отказу бота там, где рецепт на самом деле обычный
(курица+рис и т.п.) — раньше при пустом ответе Groq пользователь просто получал
"не получилось составить рецепт", хотя вторая попытка почти всегда срабатывает.
"""
import logging
import httpx

import config
from prompts import SYSTEM_PROMPT, build_user_prompt

logger = logging.getLogger(__name__)

# Порядок провайдеров для автоматического фолбэка. LLM_PROVIDER из .env всегда идёт первым,
# остальные — как резерв (используются только те, у кого задан API-ключ).
LLM_FALLBACK_ORDER = ["deepseek", "groq", "openrouter"]


def _provider_config(provider: str) -> dict | None:
    if provider == "deepseek":
        if not config.DEEPSEEK_API_KEY:
            return None
        return {
            "url": config.DEEPSEEK_URL,
            "model": config.DEEPSEEK_MODEL,
            "headers": {
                "Authorization": f"Bearer {config.DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            },
        }
    if provider == "groq":
        if not config.GROQ_API_KEY:
            return None
        return {
            "url": config.GROQ_URL,
            "model": config.GROQ_MODEL,
            "headers": {
                "Authorization": f"Bearer {config.GROQ_API_KEY}",
                "Content-Type": "application/json",
            },
        }
    if provider == "openrouter":
        if not config.OPENROUTER_API_KEY:
            return None
        return {
            "url": config.OPENROUTER_URL,
            "model": config.OPENROUTER_MODEL,
            "headers": {
                "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://t.me",
                "X-Title": "kitchen-bot",
            },
        }
    return None


async def _call_provider(provider: str, messages: list[dict]) -> str:
    cfg = _provider_config(provider)
    if not cfg:
        raise RuntimeError(f"Провайдер {provider} не настроен (нет API-ключа)")

    payload = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": 0.6,
        "max_tokens": 3000,
    }
    if provider == "deepseek":
        # У текущего поколения DeepSeek (V4.x) thinking по умолчанию ВКЛЮЧЁН — без этого
        # флага модель тратит время/токены на скрытые рассуждения, которые нам не нужны
        # для генерации рецепта.
        payload["thinking"] = {"type": "disabled"}

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(cfg["url"], headers=cfg["headers"], json=payload)
        resp.raise_for_status()
        data = resp.json()
        choice = data["choices"][0]
        content = (choice.get("message", {}) or {}).get("content") or ""
        content = content.strip()
        if not content:
            # Модель ответила 200 OK, но без текста (например, весь бюджет токенов ушёл
            # на скрытые рассуждения reasoning-моделей). Логируем сырой ответ для отладки
            # и считаем это сбоем провайдера, чтобы сработал фолбэк на следующего.
            logger.warning(
                f"Провайдер {provider} ({cfg['model']}) вернул пустой content. "
                f"finish_reason={choice.get('finish_reason')!r}, сырой ответ: {data}"
            )
            raise RuntimeError(f"Провайдер {provider} вернул пустой ответ")
        return content


async def generate_recipe(data: dict, search_query: str, search_results_text: str) -> str:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_prompt(data, search_query, search_results_text)},
    ]

    providers = [config.LLM_PROVIDER] + [p for p in LLM_FALLBACK_ORDER if p != config.LLM_PROVIDER]
    errors = []

    for provider in providers:
        try:
            return await _call_provider(provider, messages)
        except Exception as e:
            logger.warning(f"Провайдер {provider} не сработал: {e}")
            errors.append(f"{provider}: {e}")
            continue

    logger.error(f"Все LLM-провайдеры не сработали: {errors}")
    return (
        "😔 Не получилось получить рецепт от ИИ (проблема с API-ключами или сервисами).\n"
        f"Технические причины: {'; '.join(errors)}\n\n"
        "Проверь ключи в файле .env и попробуй ещё раз."
    )
