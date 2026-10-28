"""
Фото и голос: продукты с фото (DeepSeek с картинками) и текст из голосового (Whisper на Groq).

Фото только РАСПОЗНАЁТ продукты — рецепт потом собирается обычным путём (ai.generate_recipe), поэтому
аллергии из профиля, проверка ответа на запрещённые продукты и список покупок работают как всегда.
"""
import base64
import json
import logging
import re

import httpx

import config

logger = logging.getLogger(__name__)

STT_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
PHOTO_PROMPT = (
    "На фото холодильник, полка или продукты. Перечисли ЕДУ, которую видно, по-русски, коротко и обобщённо "
    "(«молоко», «яйца», «помидоры», «куриное филе», «гречка»), без марок и упаковки. Не выдумывай то, чего не видно; "
    "если не уверен — не пиши. Напитки и соусы можно. Ответь JSON: {\"items\": [\"...\"]}. "
    "Если еды на фото нет — {\"items\": []}."
)


class VisionError(RuntimeError):
    pass


def _clean(items) -> list[str]:
    out, seen = [], set()
    for x in items if isinstance(items, list) else []:
        name = re.sub(r"\s+", " ", str(x)).strip(" .,;-").lower()[:40]
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out[:30]


async def photo_items(image: bytes, mime: str = "image/jpeg") -> list[str]:
    """Список продуктов на фото. VisionError — если модель недоступна или ответ не разобрать."""
    if not config.DEEPSEEK_API_KEY:
        raise VisionError("нет ключа DeepSeek")
    payload = {
        "model": config.VISION_MODEL,
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64," + base64.b64encode(image).decode()}},
            {"type": "text", "text": PHOTO_PROMPT}]}],
        "max_tokens": 400,
        "temperature": 0.2,
        "thinking": {"type": "disabled"},
        "response_format": {"type": "json_object"},
    }
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(config.DEEPSEEK_URL, json=payload,
                                     headers={"Authorization": f"Bearer {config.DEEPSEEK_API_KEY}"})
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"] or ""
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
        logger.warning("Фото не распознано: %s", e)
        raise VisionError(str(e)) from e
    m = re.search(r"\{.*\}", content, re.S)
    try:
        return _clean(json.loads(m.group(0)).get("items") if m else [])
    except ValueError as e:
        raise VisionError("ответ не JSON") from e


async def transcribe(audio: bytes, filename: str = "voice.ogg") -> str:
    """Текст голосового сообщения ("" — если ничего не разобрал). VisionError — сервис недоступен."""
    if not config.GROQ_API_KEY:
        raise VisionError("нет ключа Groq")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(STT_URL, headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
                                     data={"model": config.STT_MODEL, "language": "ru", "response_format": "json"},
                                     files={"file": (filename, audio, "audio/ogg")})
            resp.raise_for_status()
            return (resp.json().get("text") or "").strip()
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("Голос не распознан: %s", e)
        raise VisionError(str(e)) from e
