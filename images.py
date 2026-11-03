"""
Картинки для «рецепта дня». Чем делать, выбирает IMAGE_PROVIDER в .env:
- card (по умолчанию): фирменная карточка рецепта (cards.py): название, время, порции, КБЖУ; бесплатно, без сети;
- free: бесплатный pollinations.ai, как в проекте autobot (с логотипом сервиса, лимит одна картинка в 45 секунд, еда
  получается «странной»); если не вышло, берётся карточка;
- leonardo: платный Leonardo.ai (LEONARDO_API_KEY, схема как в storymetaverse); если не вышло, берётся карточка;
- off: картинки отключены.
Сначала ИИ коротко описывает по-английски, как выглядит готовое блюдо, затем описание уходит в выбранный сервис.
Любой сбой возвращает None: пост выходит без картинки, ничего не ломается. Ключи в логи не попадают.
"""
import asyncio
import logging
import random
import re
import urllib.parse

import httpx

import cards
import config
import profile_ctx
from ai import call_llm_with_fallback

logger = logging.getLogger(__name__)

POLLINATIONS_URL = "https://image.pollinations.ai/prompt/"
NEGATIVE = "people, hands, text, watermark, logo, cartoon, illustration, drawing, blurry"
LEONARDO_URL = "https://cloud.leonardo.ai/api/rest/v1/generations"
POLL_ATTEMPTS = 24
POLL_DELAY = 3.0
STYLE = ("professional food photography, appetizing, natural window light, rustic wooden table, shallow depth of field, "
         "the finished dish served on a plate, realistic, no text, no watermark, no people, no hands, no logo")


async def _sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


def configured() -> bool:
    """Картинки включены (IMAGE_PROVIDER не off). Бесплатный вариант ключа не требует."""
    return config.IMAGE_PROVIDER != "off"


def _ingredients(recipe_text: str, limit: int = 8) -> str:
    lines, take = [], False
    for ln in (recipe_text or "").splitlines():
        s = ln.strip()
        if re.match(r"^\*?продукты", s, re.I):
            take = True
            continue
        if take:
            if not s.startswith("-"):
                if lines:
                    break
                continue
            lines.append(re.sub(r"\s*[—-]\s*[\d,.]+.*$", "", s.lstrip("- ")).strip())
    return ", ".join(x for x in lines[:limit] if x)


async def dish_scene(title: str, recipe_text: str) -> str:
    """Одна английская фраза: как выглядит готовое блюдо. При сбое ИИ берём простое описание по названию."""
    profile_ctx.current.set(None)       # исключения и профиль человека к описанию картинки отношения не имеют
    profile_ctx.base.set("")
    prompt = (f"Название блюда: {title}\nОсновные продукты: {_ingredients(recipe_text) or 'не указаны'}\n\n"
              "Напиши ОДНО предложение на английском (до 35 слов): как выглядит готовое блюдо на тарелке, "
              "цвета, текстуры, подача. Только само предложение.")
    try:
        raw = await call_llm_with_fallback([
            {"role": "system", "content": "You write short English prompts for food photography."},
            {"role": "user", "content": prompt}], max_tokens=150)
        scene = re.sub(r"\s+", " ", raw.strip().strip("\"'"))[:300]
    except Exception:  # noqa: BLE001
        scene = ""
    return scene or f"{title}, served on a plate"


async def _leonardo(prompt: str) -> bytes | None:
    if not config.LEONARDO_API_KEY:
        return None
    headers = {"Authorization": f"Bearer {config.LEONARDO_API_KEY}", "Content-Type": "application/json"}
    payload = {"height": config.RECIPE_IMAGE_SIZE, "width": config.RECIPE_IMAGE_SIZE,
               "modelId": config.LEONARDO_MODEL_ID, "num_images": 1, "prompt": prompt[:1500]}
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(LEONARDO_URL, json=payload, headers=headers)
            resp.raise_for_status()
            created = resp.json()
            gid = (created.get("sdGenerationJob") or {}).get("generationId") or created.get("generationId")
            if not gid:
                return None
            url = None
            for _ in range(POLL_ATTEMPTS):
                await _sleep(POLL_DELAY)
                st = await client.get(f"{LEONARDO_URL}/{gid}", headers=headers)
                st.raise_for_status()
                gen = st.json().get("generations_by_pk") or {}
                if gen.get("status") == "COMPLETE":
                    imgs = gen.get("generated_images") or []
                    url = imgs[0].get("url") if imgs else None
                    break
                if gen.get("status") == "FAILED":
                    break
            if not url:
                return None
            img = await client.get(url)
            img.raise_for_status()
            return img.content or None
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as e:
        logger.warning("Leonardo: картинка не получилась (%s)", type(e).__name__)
        return None


async def _free(prompt: str) -> bytes | None:
    """Бесплатный pollinations.ai: обычный запрос, в ответ картинка. Лимит без ключа: одна в 45 секунд с адреса."""
    url = (f"{POLLINATIONS_URL}{urllib.parse.quote(prompt[:900])}?width={config.RECIPE_IMAGE_SIZE}&height={config.RECIPE_IMAGE_SIZE}"
           f"&nologo=true&model={config.FREE_IMAGE_MODEL}&seed={random.randint(1, 99999)}&negative={urllib.parse.quote(NEGATIVE)}")
    headers = {"Authorization": f"Bearer {config.POLLINATIONS_KEY}"} if config.POLLINATIONS_KEY else {}
    try:
        async with httpx.AsyncClient(timeout=90, follow_redirects=True) as client:
            for attempt in range(2):
                resp = await client.get(url, headers=headers)
                if resp.status_code == 402:                       # лимит: «Retry after 42s»
                    m = re.search(r"Retry after (\d+)s", resp.text or "")
                    if attempt == 0 and m and int(m.group(1)) <= 50:
                        await _sleep(int(m.group(1)) + 1)
                        continue
                    return None
                if resp.status_code != 200 or "image" not in resp.headers.get("content-type", ""):
                    return None
                return resp.content or None
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("Бесплатная картинка не получилась: %s", type(e).__name__)
    return None


async def make_dish_image(title: str, recipe_text: str) -> bytes | None:
    """Картинка к рецепту (байты) или None. card: фирменная карточка; free: pollinations.ai, leonardo: платный; если они
    не справились, берётся карточка (она не зависит от сети)."""
    if not configured():
        return None
    order = {"leonardo": ["leonardo", "card"], "free": ["free", "card"]}.get(config.IMAGE_PROVIDER, ["card"])
    prompt = None
    for provider in order:
        if provider == "card":
            try:
                return cards.make_card(title, recipe_text)
            except Exception as e:  # noqa: BLE001
                logger.warning("Карточка рецепта не нарисовалась: %s", type(e).__name__)
                return None
        if prompt is None:
            prompt = f"{await dish_scene(title, recipe_text)}, {STYLE}"
        img = await (_leonardo(prompt) if provider == "leonardo" else _free(prompt))
        if img:
            return img
    return None
