"""
«Рецепт дня» для сообщества ВКонтакте и Telegram-канала.

Каждый день в SOCIAL_POST_HOUR:SOCIAL_POST_MINUTE (по REMINDER_TIMEZONE) бот придумывает рецепт, пишет под него текст
со ссылками на бота и сайт и публикует. Пока SOCIAL_AUTOPOST=false, автоматической публикации нет: владелец
вызывает /postvk, смотрит черновик и нажимает «Опубликовать». Ключи берутся только из .env, в логи и сообщения
об ошибках они не попадают.
"""
import asyncio
import logging
import random
import re
import secrets
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

import accounts
import book
import cards
import feature_posts
import plans
import config
import images
from ai import generate_recipe
from search import format_results_for_prompt, search_recipes
from shopping import extract_dish_title, extract_shopping_terms

logger = logging.getLogger(__name__)

# разделы «рецепта дня»: чередуются по дате, у каждого свои хештеги и картинка в альбоме сообщества
SECTIONS = [
    ("завтрак", "быстрый и сытный завтрак на каждый день, например каша, сырники, омлет или запеканка", "#завтрак #завтракдома #быстрыйзавтрак"),
    ("обед", "обед из доступных продуктов: суп или горячее с гарниром", "#обед #супы #обеддома"),
    ("ужин", "ужин за 30 минут, сытный, но не тяжёлый", "#ужин #ужиндома #быстрыйужин"),
    ("перекус", "лёгкий перекус или закуска, которую можно взять с собой", "#перекус #закуски #пп"),
    ("десерт", "десерт или выпечка без лишних хлопот, можно с меньшим количеством сахара", "#десерт #выпечка #сладкое"),
    ("суп", "домашний суп на каждый день из доступных продуктов", "#супы #суп #первоеблюдо"),
    ("по КБЖУ", "блюдо с понятным КБЖУ и хорошим количеством белка, не больше 500 ккал на порцию", "#пп #правильноепитание #подсчеткалорий"),
    ("подготовка к забегу", "еда перед длинной тренировкой или забегом: много сложных углеводов, легко усваивается", "#бег #подготовкакзабегу #питаниебегуна"),
    ("набор массы", "сытное блюдо для набора мышечной массы: много белка и калорий", "#наборнамассу #спортпит #белок"),
    ("сбалансированное блюдо", "сбалансированное блюдо из простых продуктов, без крайностей", "#сбалансированноепитание #здороваяеда #простыерецепты"),
    ("аюрведа", "тёплое аюрведическое блюдо со специями", "#аюрведа #специи #вегетарианское"),
]
SECTION_CUISINE = {"подготовка к забегу": "athlete_endurance", "набор массы": "mass_gain", "сбалансированное блюдо": "balanced",
                   "по КБЖУ": "balanced", "аюрведа": "ayurveda"}
SECTION_PHOTO = {"завтрак": "zavtrak", "обед": "obed", "ужин": "uzhin", "перекус": "perekus", "десерт": "desert", "суп": "supy", "по КБЖУ": "kbzhu",
                 "подготовка к забегу": "zabeg", "набор массы": "massa", "сбалансированное блюдо": "balans", "аюрведа": "ayurveda"}
BASE_TAGS = "#рецепты #рецептдня #едадома #чтоприготовить #кбжу"


def section_for(variant: int = 0, day: "date | None" = None) -> tuple[str, str, str]:
    """Раздел на сегодня: (название, подсказка для ИИ, хештеги раздела). variant сдвигает раздел («Другой рецепт»)."""
    d = day or datetime.now(ZoneInfo(config.REMINDER_TIMEZONE)).date()
    return SECTIONS[(d.toordinal() + variant) % len(SECTIONS)]


def hashtags(section_tags: str) -> str:
    """Общие хештеги и хештеги раздела. Первый тег раздела ещё и с адресом сообщества: по нажатию ВК ищет только внутри него."""
    group = normalize_group(config.VK_GROUP_ID).lower()
    own = f" {section_tags.split()[0]}@{group}" if group and not group.isdigit() else ""
    return f"{BASE_TAGS} {section_tags}{own}"


def section_photo(section: str | None, gid: int) -> str | None:
    """Вложение с постоянной картинкой раздела из альбома сообщества или None, если её номер не задан."""
    code = SECTION_PHOTO.get(section or "", section)
    pairs = dict(p.split("=", 1) for p in config.VK_SECTION_PHOTOS.split(",") if "=" in p)
    pid = pairs.get(code or "", "").strip()
    return f"photo-{gid}_{pid}" if pid.isdigit() else None
HISTORY_KEY = "social:history"
LAST_KEY = "social:last"
RETRY_PAUSE = 3          # секунд до повторной попытки, если ВК не взял превью ссылки


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
    section, hint, tags = section_for(variant)
    data = {"cuisine": SECTION_CUISINE.get(section, "classic"), "macro_goal": "", "preferred": "", "excluded": "", "time": "30 мин", "servings": 2,
            "portion": "normal", "appliance": ["stove"], "appliance_labels": ["Плита"],
            "variety_hint": hint, "avoid_titles": _history()}
    query, results = await search_recipes(data)
    recipe = await generate_recipe(data, query, format_results_for_prompt(results))
    recipe, _terms = extract_shopping_terms(recipe)
    if not recipe.strip() or recipe.startswith("😔"):
        raise RuntimeError("ИИ не смог составить рецепт")
    title = extract_dish_title(recipe)
    book.add(data, title, recipe, _terms)                                # рецепт дня тоже пополняет книгу
    body = plain(recipe)
    text = f"Рецепт дня ({section})\n\n{body}\n\n{links_block()}\n\n{hashtags(tags)}"
    return {"title": title, "body": body, "text": text[:15000], "section": section}


def _photo_error(data: dict) -> None:
    if "error" in data:
        raise RuntimeError(f"Картинка не загрузилась: ВКонтакте отказал, код {data['error'].get('error_code')}. "
                           "Для картинок у ключа сообщества нужно право «Фотографии»")


async def vk_photo_attachment(image: bytes) -> str:
    """Загрузить картинку для записи на стену. -> «photo-НОМЕР_ID_КЛЮЧ». RuntimeError без ключа в тексте.
    Ключ сообщества не может грузить фото на стену (photos.getWallUploadServer, код 27), для этого нужен ключ пользователя (VK_PHOTO_TOKEN).
    Без него идём через альбом сообщений (photos.getMessagesUploadServer): ВК принимает такое вложение, но на стене его может не показать."""
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            gid = await group_numeric_id(client)
            base = config.VK_API_URL.rstrip("/") + "/method/"
            common = {"access_token": config.VK_PHOTO_TOKEN or config.VK_GROUP_TOKEN, "v": config.VK_API_VERSION}
            wall = config.VK_IMAGE_MODE == "upload" or bool(config.VK_PHOTO_TOKEN)
            server = (await client.post(base + ("photos.getWallUploadServer" if wall else "photos.getMessagesUploadServer"),
                                        data={("group_id" if wall else "peer_id"): (gid if wall else 0), **common})).json()
            _photo_error(server)
            up = (await client.post(server["response"]["upload_url"], files={"photo": ("dish.png", image, "image/png")})).json()
            if not up.get("photo") or up.get("photo") == "[]":
                raise RuntimeError("Картинка не загрузилась: ВКонтакте не принял файл")
            extra = {"group_id": gid} if wall else {}
            saved = (await client.post(base + ("photos.saveWallPhoto" if wall else "photos.saveMessagesPhoto"), data={
                "photo": up["photo"], "server": up["server"], "hash": up["hash"], **extra, **common})).json()
            _photo_error(saved)
            ph = saved["response"][0]
            key = ph.get("access_key")
            return f"photo{ph['owner_id']}_{ph['id']}" + (f"_{key}" if key else "")
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"Картинка не загрузилась в ВКонтакте ({type(e).__name__})") from None


async def build_image(title: str, text: str) -> bytes | None:
    """Картинка блюда через Leonardo (None — нет ключа или не получилось)."""
    return await images.make_dish_image(title, text)


async def function_photo() -> str | None:
    """Картинка «Возможности помощника» из альбома сообщества для постов о функциях."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            return section_photo("funkcii", await group_numeric_id(client))
    except (httpx.HTTPError, ValueError) as e:
        raise RuntimeError(f"ВКонтакте не ответил ({type(e).__name__})") from None


async def post_vk(text: str, attachment: str | None = None) -> int:
    """Запись на стену сообщества от имени сообщества. -> номер записи. RuntimeError — без ключа в тексте.
    Если ВК не принял вложение-ссылку (код 100, link_photo_sizing_rule), запись всё равно выходит: без вложения, со ссылкой в тексте."""
    if not configured_vk():
        raise RuntimeError("ВКонтакте не настроен: нет VK_GROUP_ID или VK_GROUP_TOKEN")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            gid = await group_numeric_id(client)
            payload = {"owner_id": -gid, "from_group": 1, "message": text,
                       "access_token": config.VK_GROUP_TOKEN, "v": config.VK_API_VERSION}
            if attachment:
                payload["attachments"] = attachment
            url = f"{config.VK_API_URL.rstrip('/')}/method/wall.post"
            data = (await client.post(url, data=payload)).json()
            err = data.get("error") or {}
            if err.get("error_code") == 100 and "link_photo" in str(err.get("error_msg", "")) and attachment:
                payload.pop("attachments")
                if attachment.startswith("http") and attachment not in text:
                    payload["message"] = f"{text}\n\nРецепт с картинкой: {attachment}"
                await asyncio.sleep(RETRY_PAUSE)
                data = (await client.post(url, data=payload)).json()
    except (httpx.HTTPError, ValueError) as e:
        raise RuntimeError(f"ВКонтакте не ответил ({type(e).__name__})") from None
    if "error" in data:
        err = data["error"]
        raise RuntimeError(f"ВКонтакте отказал: код {err.get('error_code')}, {err.get('error_msg')}")
    return int(data["response"]["post_id"])


def tg_text(text: str) -> str:
    """Тот же пост для Telegram: свои метки в ссылках и без хештега для поиска внутри сообщества ВК."""
    text = text.replace("start=ref_vk", "start=ref_tg").replace("utm_source=vk", "utm_source=tg")
    return re.sub(r"\s*#[^\s#@]+@\S+", "", text)


async def post_tg(bot, text: str, image: bytes | None = None) -> int:
    text = tg_text(text)
    if image:
        from aiogram.types import BufferedInputFile
        await bot.send_photo(config.TG_CHANNEL_ID, BufferedInputFile(image, "dish.png"))
    msg = await bot.send_message(config.TG_CHANNEL_ID, text[:4000], parse_mode=None, disable_web_page_preview=False)
    return msg.message_id


async def publish(bot, title: str, text: str, image: bytes | None = None, body: str | None = None, section: str | None = None) -> dict:
    """Публикует везде, где настроено (с картинкой, если она есть). -> {"vk": id|None, "tg": id|None, "errors": [...]}.
    В ВК картинка идёт настоящей фотографией (см. vk_photo_attachment), а если не загрузилась, то ссылкой на страницу рецепта."""
    out = {"vk": None, "tg": None, "errors": []}
    if configured_vk():
        attachment = None
        if section and config.VK_SECTION_PHOTOS:
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    attachment = section_photo(section, await group_numeric_id(client))
            except (httpx.HTTPError, ValueError, RuntimeError):
                logger.warning("ВК: не удалось узнать номер сообщества для картинки раздела")
        if not image and config.VK_IMAGE_MODE != "upload":
            image = cards.make_card(title, body or text)      # запись всегда с картинкой: своя карточка, если другой нет
        if image and not attachment:
            try:
                attachment = await vk_photo_attachment(image)
            except RuntimeError as e:
                logger.warning("ВК: фото не загрузилось, пробую ссылку: %s", e)
                if config.VK_IMAGE_MODE == "upload":
                    out["errors"].append(f"{e}. Запись опубликована без картинки")
            if not attachment and config.VK_IMAGE_MODE != "upload":
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


# ---------- посты о функциях: раз в неделю по расписанию или вручную через /postfunc ----------
FEATURE_DONE_KEY = "soc:func"
FEATURE_WEEK_KEY = "soc:func:week"


def feature_done() -> dict:
    """{пост: номер записи} уже опубликованных."""
    return accounts.sess_get(FEATURE_DONE_KEY) or {}


def feature_open(key: str) -> bool:
    """Пост про новую функцию не выпускаем, пока она не открыта всем."""
    feat = feature_posts.BY_KEY[key][4]
    return not feat or plans.rollout().get(feat) == "all"


def feature_next() -> str | None:
    """Следующий пост: сначала ещё не выходившие по порядку, потом тот, что выходил давнее всего."""
    done = feature_done()
    keys = [p[0] for p in feature_posts.POSTS if feature_open(p[0])]
    fresh = [k for k in keys if k not in done]
    if fresh:
        return fresh[0]
    return min(keys, key=lambda k: done[k]) if keys else None


def feature_text(key: str) -> str:
    _, _, text, tags, _ = feature_posts.BY_KEY[key]
    return f"{text}\n\n{links_block()}\n\n{hashtags(tags)}"


async def publish_feature(bot, key: str) -> dict:
    """Пост о функции: в ВК с картинкой из альбома, в Telegram со своей карточкой. -> {"vk", "tg", "errors"}; сбой в одном месте не мешает другому."""
    out = {"vk": None, "tg": None, "errors": []}
    if configured_vk():
        try:
            out["vk"] = await post_vk(feature_text(key), await function_photo())
        except RuntimeError as e:
            out["errors"].append(str(e))
    if configured_tg():
        try:
            out["tg"] = await post_tg(bot, feature_text(key), cards.feature_card(feature_posts.BY_KEY[key][1]))
        except Exception as e:  # noqa: BLE001
            out["errors"].append(f"Telegram: {type(e).__name__}")
    if out["vk"] or out["tg"]:
        done = feature_done()
        done[key] = time.time()
        accounts.sess_set(FEATURE_DONE_KEY, done)
        accounts.sess_mark_forever(FEATURE_DONE_KEY)
    return out


async def weekly_feature_job(bot) -> None:
    """По расписанию раз в неделю. Работает только при SOCIAL_AUTOPOST=true; за неделю один пост, даже если сервер перезапустили."""
    if not config.SOCIAL_AUTOPOST or not (configured_vk() or configured_tg()):
        return
    week = "-".join(str(x) for x in datetime.now(ZoneInfo(config.REMINDER_TIMEZONE)).isocalendar()[:2])
    if (accounts.sess_get(FEATURE_WEEK_KEY) or {}).get("week") == week:
        return
    key = feature_next()
    if not key:
        return
    res = await publish_feature(bot, key)
    if res["errors"]:
        await _tell_admins(bot, "⚠️ Пост о функции: " + "; ".join(res["errors"]))
    if res["vk"] or res["tg"]:
        accounts.sess_set(FEATURE_WEEK_KEY, {"week": week})
        accounts.sess_mark_forever(FEATURE_WEEK_KEY)
        logger.info("Пост о функции опубликован: %s", key)


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
        res = await publish(bot, title, text, image, d["body"], d["section"])
    except Exception as e:  # noqa: BLE001
        logger.exception("Рецепт дня не опубликован")
        await _tell_admins(bot, f"⚠️ Рецепт дня не опубликован: {e}")
        return
    if res["errors"]:
        await _tell_admins(bot, "⚠️ Рецепт дня: " + "; ".join(res["errors"]))
    else:
        logger.info("Рецепт дня опубликован: %s", title)
