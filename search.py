"""
Веб-поиск рецептов через DuckDuckGo (бесплатно, без API-ключа).
Собираем сниппеты нескольких результатов — их потом анализирует LLM.
"""
import asyncio
import logging
from duckduckgo_search import DDGS

from config import SEARCH_RESULTS_COUNT

logger = logging.getLogger(__name__)


def _build_query(data: dict) -> str:
    cuisine_map = {
        "ayurveda": "аюрведический рецепт",
        "classic": "классический рецепт",
    }
    cuisine = cuisine_map.get(data.get("cuisine"), "рецепт")

    parts = [cuisine]
    if data.get("preferred"):
        parts.append(f"из {data['preferred']}")
    if data.get("appliance_labels"):
        parts.append(f"в {', '.join(data['appliance_labels'])}")
    if data.get("time") and data["time"] != "Не важно":
        parts.append(f"быстро за {data['time']}")

    return " ".join(parts)


def _search_sync(query: str, count: int) -> list[dict]:
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
    Выполняется в отдельном потоке, т.к. duckduckgo_search — синхронная библиотека.
    """
    query = _build_query(data)
    results = await asyncio.to_thread(_search_sync, query, SEARCH_RESULTS_COUNT)
    return query, results


def format_results_for_prompt(results: list[dict]) -> str:
    if not results:
        return "(поиск не дал результатов, используй свои знания о кухне)"
    chunks = []
    for i, r in enumerate(results, 1):
        chunks.append(f"[{i}] {r['title']}\n{r['body']}\nИсточник: {r['href']}")
    return "\n\n".join(chunks)
