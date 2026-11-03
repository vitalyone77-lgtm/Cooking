"""
Картинки блюд для «рецепта дня» через Leonardo.ai (оплаченный ключ владельца, LEONARDO_API_KEY в .env).

Схема как в проекте storymetaverse: запрос на создание, потом опрос готовности, потом скачивание. Сначала ИИ
коротко описывает по-английски, как выглядит готовое блюдо, затем это описание уходит в Leonardo.
Любой сбой возвращает None: пост выходит без картинки, ничего не ломается. Ключ в логи не попадает.
"""
import asyncio
import logging
import re

import httpx

import config
import profile_ctx
from ai import call_llm_with_fallback

logger = logging.getLogger(__name__)

LEONARDO_URL = "https://cloud.leonardo.ai/api/rest/v1/generations"
POLL_ATTEMPTS = 24
POLL_DELAY = 3.0
STYLE = ("professional food photography, appetizing, natural window light, rustic wooden table, shallow depth of field, "
         "the finished dish served on a plate, realistic, no text, no watermark, no people, no hands, no logo")


async def _sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


def configured() -> bool:
    return bool(config.LEONARDO_API_KEY)


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


async def make_dish_image(title: str, recipe_text: str) -> bytes | None:
    """Картинка блюда (байты PNG/JPEG) или None, если ключа нет или Leonardo не справился."""
    if not configured():
        return None
    prompt = f"{await dish_scene(title, recipe_text)}, {STYLE}"
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
        logger.warning("Картинка блюда не получилась: %s", type(e).__name__)
        return None
