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
import secrets
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

import accounts
import config
import images
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


_gid_cache: dict[str, int] = {}


def normalize_group(raw) -> str:
    """«-123», «club123», «https://vk.com/club123», «https://vk.com/kbzum», «kbzum» -> «123» или «kbzum»."""
    s = re.sub(r"^https?://(?:www\.|m\.)?vk\.(?:com|ru)/", "", str(raw or "").strip()).split("?")[0].strip("/").lstrip("@-")
    m = re.fullmatch(r"(?:club|public|event)(\d+)", s)
    return m.group(1) if m else s


async def group_numeric_id(client) -> int:
    """Числовой номер сообщества. Если в настройке короткий адрес, узнаём номер у ВКонтакте (один раз)."""
    g = normalize_group(config.VK_GROUP_ID)
    if g.isdigit():
        return int(g)
    if g in _gid_cache:
        return _gid_cache[g]
    resp = await client.post(f"{config.VK_API_URL.rstrip('/')}/method/groups.getById", data={
        "group_ids": g, "access_token": config.VK_GROUP_TOKEN, "v": config.VK_API_VERSION})
    data = resp.json()
    if "error" in data:
        err = data["error"]
        raise RuntimeError(f"Не удалось узнать номер сообщества «{g}»: код {err.get('error_code')}. "
                           "Впишите номер цифрами в VK_GROUP_ID (его видно в ссылке на запись: wall-НОМЕР_...)")
    res = data.get("response")
    groups = res.get("groups") if isinstance(res, dict) else res
    if not groups:
        raise RuntimeError(f"Сообщество «{g}» не найдено: проверьте VK_GROUP_ID")
    _gid_cache[g] = int(groups[0]["id"])
    return _gid_cache[g]


_TR = dict(zip("абвгдежзийклмнопрстуфхцчшщыэюя",
               ["a", "b", "v", "g", "d", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t", "u", "f", "h",
                "c", "ch", "sh", "sch", "y", "e", "yu", "ya"]))


def slugify(title: str) -> str:
    """«Куриный суп» -> «kurinyy-sup-a3f9c1» (латиница и случайный хвост, чтобы адреса не повторялись)."""
    s = "".join(_TR.get(ch, ch) for ch in (title or "").lower().replace("ё", "е"))
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:50] or "recipe"
    return f"{s}-{secrets.token_hex(3)}"


def page_url(slug: str) -> str:
    return f"{(config.WEB_APP_URL or 'https://menu.ymayaka.ru').rstrip('/')}/r/{slug}"


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
    d = await build_post_full(variant)
    return d["title"], d["text"]


async def build_post_full(variant: int = 0) -> dict:
    """-> {"title", "body" (рецепт без ссылок, для страницы), "text" (пост)}. RuntimeError, если ИИ не ответил."""
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
    return {"title": title, "body": body, "text": text[:15000]}


def _photo_error(data: dict) -> None:
    if "error" in data:
        raise RuntimeError(f"Картинка не загрузилась: ВКонтакте отказал, код {data['error'].get('error_code')}. "
                           "Для картинок у ключа сообщества нужно право «Фотографии»")


async def vk_photo_attachment(image: bytes) -> str:
    """Загрузить картинку для записи на стену сообщества. -> «photo-НОМЕР_ID». RuntimeError без ключа в тексте."""
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            gid = await group_numeric_id(client)
            base = config.VK_API_URL.rstrip("/") + "/method/"
            common = {"access_token": config.VK_GROUP_TOKEN, "v": config.VK_API_VERSION}
            server = (await client.post(base + "photos.getWallUploadServer", data={"group_id": gid, **common})).json()
            _photo_error(server)
            up = (await client.post(server["response"]["upload_url"], files={"photo": ("dish.png", image, "image/png")})).json()
            if not up.get("photo") or up.get("photo") == "[]":
                raise RuntimeError("Картинка не загрузилась: ВКонтакте не принял файл")
            saved = (await client.post(base + "photos.saveWallPhoto", data={
                "group_id": gid, "photo": up["photo"], "server": up["server"], "hash": up["hash"], **common})).json()
            _photo_error(saved)
            ph = saved["response"][0]
            return f"photo{ph['owner_id']}_{ph['id']}"
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"Картинка не загрузилась в ВКонтакте ({type(e).__name__})") from None


async def build_image(title: str, text: str) -> bytes | None:
    """Картинка блюда через Leonardo (None — нет ключа или не получилось)."""
    return await images.make_dish_image(title, text)


async def post_vk(text: str, attachment: str | None = None) -> int:
    """Запись на стену сообщества от имени сообщества. -> номер записи. RuntimeError — без ключа в тексте."""
    if not configured_vk():
        raise RuntimeError("ВКонтакте не настроен: нет VK_GROUP_ID или VK_GROUP_TOKEN")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            gid = await group_numeric_id(client)
            payload = {"owner_id": -gid, "from_group": 1, "message": text,
                       "access_token": config.VK_GROUP_TOKEN, "v": config.VK_API_VERSION}
            if attachment:
                payload["attachments"] = attachment
            resp = await client.post(f"{config.VK_API_URL.rstrip('/')}/method/wall.post", data=payload)
            data = resp.json()
    except (httpx.HTTPError, ValueError) as e:
        raise RuntimeError(f"ВКонтакте не ответил ({type(e).__name__})") from None
    if "error" in data:
        err = data["error"]
        raise RuntimeError(f"ВКонтакте отказал: код {err.get('error_code')}, {err.get('error_msg')}")
    return int(data["response"]["post_id"])


async def post_tg(bot, text: str, image: bytes | None = None) -> int:
    if image:
        from aiogram.types import BufferedInputFile
        await bot.send_photo(config.TG_CHANNEL_ID, BufferedInputFile(image, "dish.png"))
    msg = await bot.send_message(config.TG_CHANNEL_ID, text[:4000], parse_mode=None, disable_web_page_preview=False)
    return msg.message_id


async def publish(bot, title: str, text: str, image: bytes | None = None, body: str | None = None) -> dict:
    """Публикует везде, где настроено (с картинкой, если она есть). -> {"vk": id|None, "tg": id|None, "errors": [...]}.
    В ВК картинка идёт ссылкой на страницу рецепта (VK_IMAGE_MODE=link): ключ сообщества фото не загружает (код 27)."""
    out = {"vk": None, "tg": None, "errors": []}
    if configured_vk():
        attachment = None
        if image and config.VK_IMAGE_MODE == "upload":
            try:
                attachment = await vk_photo_attachment(image)
            except RuntimeError as e:
                out["errors"].append(f"{e}. Запись опубликована без картинки")
        elif image:
            try:
                slug = slugify(title)
                accounts.pub_recipe_add(slug, title, body or text, image)
                attachment = page_url(slug)
            except Exception:  # noqa: BLE001
                logger.exception("Страница рецепта не создана")
                out["errors"].append("Страница рецепта не создана. Запись опубликована без картинки")
        try:
            out["vk"] = await post_vk(text, attachment)
        except RuntimeError as e:
            out["errors"].append(str(e))
    if configured_tg():
        try:
            out["tg"] = await post_tg(bot, text, image)
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
        d = await build_post_full()
        title, text = d["title"], d["text"]
        image = await build_image(title, d["body"]) if (config.SOCIAL_IMAGES and images.configured()) else None
        res = await publish(bot, title, text, image, d["body"])
    except Exception as e:  # noqa: BLE001
        logger.exception("Рецепт дня не опубликован")
        await _tell_admins(bot, f"⚠️ Рецепт дня не опубликован: {e}")
        return
    if res["errors"]:
        await _tell_admins(bot, "⚠️ Рецепт дня: " + "; ".join(res["errors"]))
    else:
        logger.info("Рецепт дня опубликован: %s", title)
