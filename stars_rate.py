"""
Цена в звёздах считается от цены в рублях: сколько звёзд нужно, чтобы при выводе получилась та же сумма.
Курс доллара берём у ЦБ раз в день. Цену меняем, только если она разошлась больше чем на DRIFT, чтобы не дёргать покупателей.
Цена звезды при выводе (config.STAR_USD) задана настройкой: точное значение Telegram отдаёт только через внутренний API клиентов, боту он недоступен.
Нет курса: цена остаётся прежней. Нули не трогаем: 0 звёзд значит, что этот тариф звёздами не продаётся.
"""
import logging
import math
import re
import time

import httpx

import accounts
import config
import plans

logger = logging.getLogger(__name__)

DRIFT = 0.05
STEP = 5
RATE_KEY = "stars:rate"
SOURCES = ("https://www.cbr-xml-daily.ru/daily_json.js", "https://www.cbr.ru/scripts/XML_daily.asp")
STALE_DAYS = 3


def stars_for(rub: int, usd: float) -> int:
    """Звёзды за цену в рублях, вверх до кратного STEP."""
    return int(math.ceil(rub / (usd * config.STAR_USD) / STEP) * STEP) if rub > 0 and usd > 0 else 0


def reprice(spec: str, usd: float) -> str:
    """Строка PREMIUM_PLANS с пересчитанными звёздами."""
    out = []
    for part in (spec or "").split(","):
        m = re.fullmatch(r"\s*([^=\s]+)=(\d+):(\d+):(\d+)\s*", part)
        if not m:
            if part.strip():
                out.append(part.strip())
            continue
        key, days, rub, stars = m.group(1), m.group(2), int(m.group(3)), int(m.group(4))
        out.append(f"{key}={days}:{rub}:{stars_for(rub, usd) if stars else 0}")
    return ",".join(out)


def drifted(spec: str, new: str) -> bool:
    old_n = {o.split("=")[0]: int(o.split(":")[-1]) for o in spec.split(",") if "=" in o and o.count(":") == 2 and o.split(":")[-1].isdigit()}
    new_n = {o.split("=")[0]: int(o.split(":")[-1]) for o in new.split(",") if "=" in o and o.count(":") == 2 and o.split(":")[-1].isdigit()}
    return any(k in old_n and old_n[k] and abs(v - old_n[k]) / old_n[k] > DRIFT for k, v in new_n.items())


def last_rate() -> dict | None:
    """{usd, ts}: последний полученный курс."""
    return accounts.sess_get(RATE_KEY)


def auto_on() -> bool:
    return bool(plans.cfg("STARS_AUTO"))


def on_admin_save(spec: str) -> str:
    """Владелец поменял цены в рублях: звёзды сразу подстраиваются по последнему известному курсу. Курса ещё не было: оставляем как ввели."""
    r = last_rate()
    return reprice(spec, r["usd"]) if auto_on() and r else spec


def _parse(url: str, resp: httpx.Response) -> float | None:
    if url.endswith(".js"):
        return float(resp.json()["Valute"]["USD"]["Value"])
    m = re.search(r"<CharCode>USD</CharCode>.*?<Value>([\d,]+)</Value>", resp.content.decode("windows-1251", "replace"), re.S)
    return float(m.group(1).replace(",", ".")) if m else None


async def fetch_usd() -> float | None:
    """Курс доллара ЦБ: зеркало в JSON, затем сайт ЦБ. Нелепое значение считаем ошибкой, цены от него зависят."""
    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True) as client:
        for url in SOURCES:
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                usd = _parse(url, resp)
                if usd and 20 <= usd <= 500:
                    return usd
                logger.warning("Курс вне разумных границ: %s", usd)
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
                logger.warning("Курс не получен: %r", e)
    return None


async def _notify(bot, text: str) -> None:
    for admin in config.ADMIN_TG_IDS:
        try:
            await bot.send_message(admin, text, parse_mode=None)
        except Exception:  # noqa: BLE001
            pass


async def update(bot=None) -> dict:
    """Получить курс и при заметном расхождении обновить цены. -> {usd, changed, plans}."""
    usd = await fetch_usd()
    if not usd:
        r = last_rate()
        if bot and auto_on() and r and time.time() - r["ts"] > STALE_DAYS * 86400:
            await _notify(bot, f"⚠️ Курс для цены в звёздах не обновлялся больше {STALE_DAYS} дней. Цены остаются прежними, бот продолжает их показывать.")
        return {"usd": None, "changed": False, "plans": plans.cfg("PREMIUM_PLANS")}
    accounts.sess_set(RATE_KEY, {"usd": usd, "ts": time.time()})
    accounts.sess_mark_forever(RATE_KEY)
    spec = plans.cfg("PREMIUM_PLANS")
    if not auto_on():
        return {"usd": usd, "changed": False, "plans": spec}
    new = reprice(spec, usd)
    if new == spec or not drifted(spec, new):
        return {"usd": usd, "changed": False, "plans": spec}
    accounts.settings_set({"PREMIUM_PLANS": new}, "курс")
    plans.reload()
    if bot:
        prices = ", ".join(f"{o['title'].lower()} {o['stars']} ⭐" for o in plans.offers() if o["stars"])
        await _notify(bot, f"⭐ Цена в звёздах обновлена по курсу ЦБ {usd:.2f} ₽ за доллар: {prices}. Выключить: админка → «Тарифы и лимиты» → «Цену в звёздах считать по курсу».")
    return {"usd": usd, "changed": True, "plans": new}


async def job(bot) -> None:
    try:
        await update(bot)
    except Exception:  # noqa: BLE001 — цены остаются прежними, завтра попробуем снова
        logger.exception("курс для звёзд")
