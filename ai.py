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
import profile_ctx
from prompts import SYSTEM_PROMPT, build_user_prompt

logger = logging.getLogger(__name__)


class _Truncated(RuntimeError):
    """Провайдер ответил, но обрезал текст лимитом токенов. Несёт сам текст, чтобы _call_any
    мог вернуть его как крайний случай, если ВСЕ провайдеры обрежут (лучше неполный ответ,
    чем полный отказ пользователю)."""
    def __init__(self, provider: str, content: str):
        super().__init__(f"Провайдер {provider} вернул обрезанный ответ")
        self.content = content

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


async def _call_provider(provider: str, messages: list[dict], max_tokens: int) -> str:
    cfg = _provider_config(provider)
    if not cfg:
        raise RuntimeError(f"Провайдер {provider} не настроен (нет API-ключа)")

    payload = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": 0.6,
        "max_tokens": max_tokens,
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
        if choice.get("finish_reason") == "length":
            # Ответ обрезан лимитом токенов — для меню/списка покупок маркеры (БАЗОВЫЕ_ПРОДУКТЫ,
            # ИСПОЛЬЗОВАНО_БАЗОВЫЕ и т.п.) обычно идут в конце текста и могут быть потеряны целиком.
            # Раньше это тихо считалось успехом. Пробуем следующего провайдера — если и там обрежет,
            # вызывающий код хотя бы получит (неполный) текст от последнего провайдера, а не тишину.
            logger.warning(f"Провайдер {provider} ({cfg['model']}) обрезал ответ (finish_reason=length)")
            raise _Truncated(provider, content)
        return content


async def call_llm_with_fallback(messages: list[dict], max_tokens: int = 3000) -> str:
    """
    Общая точка входа для любого запроса к LLM (одиночный рецепт, меню на день и т.п.).
    Пробует провайдеров по очереди (LLM_PROVIDER первым, дальше LLM_FALLBACK_ORDER) и
    поднимает RuntimeError, только если ВСЕ провалились — вызывающий код сам решает,
    что показать пользователю или на какой альтернативный путь переключиться.
    """
    block = profile_ctx.prompt_block()
    if block:
        # исключения запроса — в конец запроса пользователя: системный промпт не меняется и остаётся в кэше DeepSeek
        messages = messages[:-1] + [{**messages[-1], "content": messages[-1]["content"] + block}]
    text = await _call_any(messages, max_tokens)
    bad = profile_ctx.hits(text)
    if bad:
        # нашёлся исключённый продукт — одна повторная попытка с явным указанием, что не так
        logger.warning("В ответе исключённые продукты %s — перегенерирую", bad)
        retry = messages + [{"role": "assistant", "content": text},
                            {"role": "user", "content": "В ответе есть продукты, которые просили исключить: " + ", ".join(bad)
                             + ". Перепиши ответ полностью в том же формате без них (и без всего, что их содержит)."}]
        try:
            text = await _call_any(retry, max_tokens)
            bad = profile_ctx.hits(text)   # перепроверяем ОТВЕТ ПОСЛЕ повтора, а не старый список
        except RuntimeError:
            pass   # провайдеры недоступны — bad остаётся прежним, предупредим о нём ниже
        if bad:
            # Решение от 2026-11-12: раньше это предупреждение добавлял только generate_recipe (один рецепт).
            # Меню на день, план на 3 дня и замена недостающего продукта (day_menu.py/week_menu.py/missing.py)
            # тоже зовут call_llm_with_fallback, но сами не перепроверяли результат — если повтор не помогал,
            # аллерген/исключённый продукт молча уходил пользователю без единого предупреждения. Проверка и
            # предупреждение теперь здесь, в общей точке входа — одно место на всех.
            text = ("⚠️ Проверьте состав: возможно, здесь есть " + ", ".join(bad)
                    + ", а вы просили это исключить.\n\n" + text)
    return text


async def _call_any(messages: list[dict], max_tokens: int) -> str:
    providers = [config.LLM_PROVIDER] + [p for p in LLM_FALLBACK_ORDER if p != config.LLM_PROVIDER]
    errors = []

    for provider in providers:
        try:
            return await _call_provider(provider, messages, max_tokens)
        except _Truncated as e:
            # Обрезал лимит длины: у резервных моделей лимит тот же и русский текст в них «длиннее», поэтому
            # не идём к ним, а один раз повторяем у того же провайдера с запасом в полтора раза. Не помогло —
            # отдаём его же (неполный) ответ: это лучше, чем отказ или ещё минута ожидания.
            logger.warning(f"Провайдер {provider}: {e}, повторяю с большим лимитом")
            try:
                return await _call_provider(provider, messages, int(max_tokens * 1.5))
            except _Truncated as e2:
                return e2.content
            except Exception:
                return e.content
        except Exception as e:
            logger.warning(f"Провайдер {provider} не сработал: {e}")
            errors.append(f"{provider}: {e}")
            continue

    raise RuntimeError("; ".join(errors))


async def generate_recipe(data: dict, search_query: str, search_results_text: str) -> str:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_prompt(data, search_query, search_results_text)},
    ]
    try:
        return await call_llm_with_fallback(messages)   # предупреждение про исключённые продукты — внутри неё
    except RuntimeError as e:
        logger.error(f"Все LLM-провайдеры не сработали: {e}")
        return (
            "😔 Сейчас не получается составить рецепт: сервис ИИ не отвечает. Попробуйте ещё раз через несколько минут."
        )
