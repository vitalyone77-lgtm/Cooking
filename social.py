"""
«Рецепт дня» для сообщества ВКонтакте и Telegram-канала.

Каждый день в SOCIAL_POST_HOUR:SOCIAL_POST_MINUTE (по REMINDER_TIMEZONE) бот придумывает рецепт, пишет под него текст
со ссылками на бота и сайт и публикует. Пока SOCIAL_AUTOPOST=false, автоматической публикации нет: владелец
вызывает /postvk, смотрит черновик и нажимает «Опубликовать». Ключи берутся только из .env, в логи и сообщения
об ошибках они не попадают.
"""
import logging
import random
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

import accounts
import config
from ai import generate_recipe
from search import format_results_for_prompt, search_recipes
from shopping import extract_dish_title, extract_shopping_terms

logger = logging.getLogger(__name__)

# тема дня недели: понедельник = 0
WEEKDAY_HINTS = [
    "быстрый и сытный завтрак",
    "ужин за 30 минут",
    "суп",
    "блюдо из курицы",
    "лёгкий ужин на пятницу",
    "что-то вкусное на выходные, например запеканка или выпечка",
    "семейный обед из доступных продуктов",
]
HASHTAGS = "#рецепты #рецептдня #едадома #чтоприготовить #кбжу #пп #меню #ужин"
HISTORY_KEY = "social:history"
LAST_KEY = "social:last"


def configured_vk() -> bool:
    return bool(config.VK_GROUP_ID and config.VK_GROUP_TOKEN)


def configured_tg() -> bool:
    return bool(config.TG_CHANNEL_ID)


def today() -> str:
    return datetime.now(ZoneInfo(config.REMINDER_TIMEZONE)).date().isoformat()


def plain(text: str) -> str:
    """Рецепт приходит с разметкой Telegram (*жирный*, _курсив_) — ВКонтакте её не показывает."""
    text = re.sub(r"[*_`]", "", text or "")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def links_block() -> str:
    site = (config.WEB_APP_URL or "https://menu.ymayaka.ru").rstrip("/")
    return (f"Подобрать рецепт под себя, посчитать КБЖУ и собрать список покупок:\n"
            f"Бот в Telegram: https://t.me/{config.BOT_USERNAME}?start=ref_vk\n"
            f"Сайт: {site}/?utm_source=vk")


def _history() -> list[str]:
    return (accounts.sess_get(HISTORY_KEY) or {}).get("titles", [])


def _remember(title: str) -> None:
    titles = ([title] + [t for t in _history() if t != title])[:40]
    accounts.sess_set(HISTORY_KEY, {"titles": titles})
    accounts.sess_mark_forever(HISTORY_KEY)


async def build_post(variant: int = 0) -> tuple[str, str]:
    """-> (название блюда, готовый текст поста). RuntimeError, если ИИ не ответил."""
    weekday = datetime.now(ZoneInfo(config.REMINDER_TIMEZONE)).weekday()
    hint = WEEKDAY_HINTS[(weekday + variant) % len(WEEKDAY_HINTS)]
    data = {"cuisine": "classic", "macro_goal": "", "preferred": "", "excluded": "", "time": "30 мин", "servings": 2,
            "portion": "normal", "appliance": ["stove"], "appliance_labels": ["Плита"],
            "variety_hint": hint, "avoid_titles": _history()}
    query, results = await search_recipes(data)
    recipe = await generate_recipe(data, query, format_results_for_prompt(results))
    recipe, _terms = extract_shopping_terms(recipe)
    if not recipe.strip() or recipe.startswith("😔"):
        raise RuntimeError("ИИ не смог составить рецепт")
    title = extract_dish_title(recipe)
    body = plain(recipe)
    text = f"Рецепт дня\n\n{body}\n\n{links_block()}\n\n{HASHTAGS}"
    return title, text[:15000]


async def post_vk(text: str) -> int:
    """Запись на стену сообщества от имени сообщества. -> номер записи. RuntimeError — без ключа в тексте."""
    if not configured_vk():
        raise RuntimeError("ВКонтакте не настроен: нет VK_GROUP_ID или VK_GROUP_TOKEN")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{config.VK_API_URL.rstrip('/')}/method/wall.post", data={
                "owner_id": -abs(int(config.VK_GROUP_ID)), "from_group": 1, "message": text,
                "access_token": config.VK_GROUP_TOKEN, "v": config.VK_API_VERSION})
            data = resp.json()
    except (httpx.HTTPError, ValueError) as e:
        raise RuntimeError(f"ВКонтакте не ответил ({type(e).__name__})") from None
    if "error" in data:
        err = data["error"]
        raise RuntimeError(f"ВКонтакте отказал: код {err.get('error_code')}, {err.get('error_msg')}")
    return int(data["response"]["post_id"])


async def post_tg(bot, text: str) -> int:
    msg = await bot.send_message(config.TG_CHANNEL_ID, text[:4000], parse_mode=None, disable_web_page_preview=False)
    return msg.message_id


async def publish(bot, title: str, text: str) -> dict:
    """Публикует везде, где настроено. -> {"vk": id|None, "tg": id|None, "errors": [...]}."""
    out = {"vk": None, "tg": None, "errors": []}
    if configured_vk():
        try:
            out["vk"] = await post_vk(text)
        except RuntimeError as e:
            out["errors"].append(str(e))
    if configured_tg():
        try:
            out["tg"] = await post_tg(bot, text)
        except Exception as e:  # noqa: BLE001
            out["errors"].append(f"Telegram-канал: {type(e).__name__}")
    if out["vk"] or out["tg"]:
        _remember(title)
        accounts.sess_set(LAST_KEY, {"day": today()})
        accounts.sess_mark_forever(LAST_KEY)
    return out


async def _tell_admins(bot, text: str) -> None:
    for admin in config.ADMIN_TG_IDS:
        try:
            await bot.send_message(admin, text, parse_mode=None)
        except Exception:  # noqa: BLE001
            pass


async def daily_job(bot) -> None:
    """По расписанию. Работает только при SOCIAL_AUTOPOST=true и настроенном ВК или канале; раз в день."""
    if not config.SOCIAL_AUTOPOST:
        return
    if not (configured_vk() or configured_tg()):
        logger.warning("Рецепт дня: автопубликация включена, но ВКонтакте и канал не настроены")
        return
    if (accounts.sess_get(LAST_KEY) or {}).get("day") == today():
        return
    try:
        title, text = await build_post()
        res = await publish(bot, title, text)
    except Exception as e:  # noqa: BLE001
        logger.exception("Рецепт дня не опубликован")
        await _tell_admins(bot, f"⚠️ Рецепт дня не опубликован: {e}")
        return
    if res["errors"]:
        await _tell_admins(bot, "⚠️ Рецепт дня: " + "; ".join(res["errors"]))
    else:
        logger.info("Рецепт дня опубликован: %s", title)
