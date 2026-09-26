"""
Веб-поиск рецептов. Основной источник — Tavily (платный API, без скрейпинга и без
рейтлимитов). Если ключ Tavily не задан или запрос не удался — резервный вариант:
DuckDuckGo (бесплатно, но на некоторых серверах упирается в рейтлимит "202 Ratelimit").
Собираем сниппеты нескольких результатов — их потом анализирует LLM.
"""
import asyncio
import logging
import re

import httpx
from duckduckgo_search import DDGS

from config import SEARCH_RESULTS_COUNT, TAVILY_API_KEY
from cuisines import cuisine_search_hint

logger = logging.getLogger(__name__)

TAVILY_URL = "https://api.tavily.com/search"

# Убираем вводные слова вроде "хочу", "хочу приготовить" перед построением поискового запроса
_LEAD_IN_RE = re.compile(
    r"^\s*(хочу приготовить|хочу сделать|хочу|сделать|приготовить)\s+", re.IGNORECASE
)


def _clean_preferred(text: str) -> str:
    return _LEAD_IN_RE.sub("", text).strip()


def _build_query(data: dict) -> str:
    parts = [cuisine_search_hint(data.get("cuisine"))]
    if data.get("macro_goal"):
        parts.append(data["macro_goal"])
    if data.get("preferred"):
        parts.append(_clean_preferred(data["preferred"]))
    if data.get("appliance_labels"):
        parts.append(f"в {', '.join(data['appliance_labels'])}")
    if data.get("time") and data["time"] != "Не важно":
        parts.append(f"быстро за {data['time']}")

    return " ".join(parts)


async def _search_tavily(query: str, count: int) -> list[dict]:
    if not TAVILY_API_KEY:
        return []
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                TAVILY_URL,
                json={
                    "api_key": TAVILY_API_KEY,
                    "query": query,
                    "search_depth": "basic",
                    "max_results": count,
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.warning(f"Ошибка поиска Tavily: {e}")
        return []

    return [
        {
            "title": r.get("title", ""),
            "body": r.get("content", ""),
            "href": r.get("url", ""),
        }
        for r in data.get("results", [])
    ]


def _search_ddg_sync(query: str, count: int) -> list[dict]:
    results = []
    try:
        with DDGS() as ddgs:
            for r in ddgs.text(query, region="ru-ru", max_results=count):
                results.append({
                    "title": r.get("title", ""),
                    "body": r.get("body", ""),
                    "href": r.get("href", ""),
                })
    except Exception as e:
        logger.warning(f"Ошибка поиска DuckDuckGo: {e}")
    return results


async def search_recipes(data: dict) -> tuple[str, list[dict]]:
    """
    Возвращает (поисковый запрос, список найденных сниппетов).
    Сначала пробует Tavily, при пустом результате — DuckDuckGo как резерв.
    """
    query = _build_query(data)

    results = await _search_tavily(query, SEARCH_RESULTS_COUNT)
    if not results:
        results = await asyncio.to_thread(_search_ddg_sync, query, SEARCH_RESULTS_COUNT)

    return query, results


def format_results_for_prompt(results: list[dict]) -> str:
    if not results:
        return "(поиск не дал результатов, используй свои знания о кухне)"
    chunks = []
    for i, r in enumerate(results, 1):
        chunks.append(f"[{i}] {r['title']}\n{r['body']}\nИсточник: {r['href']}")
    return "\n\n".join(chunks)
