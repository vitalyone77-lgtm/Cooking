"""
Тарифы: что доступно бесплатно и что — по подписке.

Настройки тарифов меняются в админке (/admin → «Тарифы и лимиты») и лежат в базе (accounts.settings);
чего там нет — берётся из .env (config.py, раздел «Подписка»). Бот и сайт — разные процессы, поэтому
перечитываем базу не чаще раза в SETTINGS_TTL секунд: изменение из админки доходит до обоих за ~15 с.

Пока PAYWALL_ENABLED выключен, у всех один план «open» — прежние лимиты, ничего не меняется.
После включения:
  anon    — сайт без входа через Telegram (минимум: попробовать);
  free    — аккаунт Telegram без подписки;
  premium — подписка, пробный период или промокод.
"""
import time

import accounts
import config

# что можно менять из админки: имя -> (кто может: owner — только владелец, all — и партнёр, тип)
EDITABLE = {
    "PAYWALL_ENABLED": ("owner", bool),
    "DAILY_GEN_CAP": ("owner", int),
    "DAILY_GEN_CAP_ANON": ("owner", int),
    "WEB_IP_DAILY_GENS": ("owner", int),
    "PARTNER_SHARE_PCT": ("owner", int),
    "LIMITS_FREE": ("all", str),
    "LIMITS_PREMIUM": ("all", str),
    "LIMITS_ANON": ("all", str),
    "PREMIUM_FEATURES": ("all", list),
    "TRIAL_DAYS": ("all", int),
    "PREMIUM_PLANS": ("all", str),
    "PROMO_CODES": ("all", str),
}
SETTINGS_TTL = 15
_cache: dict = {"ts": 0.0, "data": {}}


def _overrides() -> dict:
    if time.time() - _cache["ts"] > SETTINGS_TTL:
        try:
            _cache["data"] = accounts.settings_all()
        except Exception:  # noqa: BLE001 — база недоступна: живём на .env
            pass
        _cache["ts"] = time.time()
    return _cache["data"]


def cfg(name: str):
    """Значение настройки: из админки (если меняли), иначе из .env."""
    data = _overrides()
    if name in data:
        return data[name]
    return getattr(config, name)


def reload() -> None:
    _cache["ts"] = 0.0


def _parse_limits(spec: str) -> dict[str, tuple[int, int]]:
    out = {}
    for part in (spec or "").split(","):
        if "=" not in part:
            continue
        kind, val = part.split("=", 1)
        n, _, days = val.partition("/")
        try:
            out[kind.strip()] = (int(n), max(1, int(days or 1)))
        except ValueError:
            continue
    return out


def limits_of(plan: str) -> dict[str, tuple[int, int]]:
    spec = {"open": config.LIMITS_OPEN, "free": cfg("LIMITS_FREE"), "premium": cfg("LIMITS_PREMIUM"),
            "anon": cfg("LIMITS_ANON")}.get(plan, "")
    return _parse_limits(spec)


PLAN_TITLES = {"open": "Полный доступ", "free": "Бесплатный", "premium": "Подписка", "anon": "Без входа"}
KIND_TITLES = {"recipe": "блюд в день", "daymenu": "меню на день", "week": "планов на 3 дня", "missing": "замен продуктов"}
FEATURE_TITLES = {"memory": "Память: профиль (аллергии, «не использовать») и полка",
                  "pdf": "PDF для чтения без интернета", "cart": "Корзина ВкусВилла с ценами",
                  "noads": "Сайт без рекламы"}


def paywall() -> bool:
    return bool(cfg("PAYWALL_ENABLED"))


def is_anon(uid) -> bool:
    return str(uid).startswith("w")


def plan_of(uid) -> str:
    if not paywall():
        return "open"
    if is_anon(uid):
        return "anon"
    return "premium" if accounts.is_premium(uid) else "free"


def limit(uid, kind: str) -> tuple[int, int] | None:
    """(сколько, за сколько дней) для вида подбора или None — без ограничения (например, день плана)."""
    return limits_of(plan_of(uid)).get(kind)


def has_feature(uid, feature: str) -> bool:
    """memory / pdf / cart / noads — при включённой подписке только для неё (если отмечены в PREMIUM_FEATURES)."""
    if not paywall() or feature not in (cfg("PREMIUM_FEATURES") or []):
        return True
    return plan_of(uid) == "premium"


def can_upsell(uid) -> bool:
    """Есть ли смысл показывать «Оформить подписку» (подписка включена и её ещё нет)."""
    return paywall() and plan_of(uid) != "premium"


def offers() -> list[dict]:
    """[{key, days, rub, stars, title}] из PREMIUM_PLANS."""
    out = []
    for part in (cfg("PREMIUM_PLANS") or "").split(","):
        if "=" not in part:
            continue
        key, val = part.split("=", 1)
        bits = (val.split(":") + ["0", "0", "0"])[:3]
        try:
            days, rub, stars = (int(b) for b in bits)
        except ValueError:
            continue
        title = {30: "Месяц", 365: "Год"}.get(days, f"{days} дней")
        out.append({"key": key.strip(), "days": days, "rub": rub, "stars": stars, "title": title})
    return out


def offer(key: str) -> dict | None:
    return next((o for o in offers() if o["key"] == key), None)


def promo_codes() -> dict[str, int]:
    out = {}
    for part in (cfg("PROMO_CODES") or "").split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            try:
                out[k.strip().upper()] = max(0, int(v))
            except ValueError:
                continue
    return out


def promo_days(code: str) -> int:
    return promo_codes().get(code.strip().upper(), 0)


def maybe_start_trial(uid) -> float | None:
    """Первый упор в лимит бесплатного плана — включаем пробный полный доступ (один раз на аккаунт)."""
    days = int(cfg("TRIAL_DAYS") or 0)
    if not paywall() or is_anon(uid) or days <= 0:
        return None
    return accounts.start_trial(uid, days)


def status_text(uid) -> str:
    plan = plan_of(uid)
    if plan == "open":
        return "💎 Сейчас все функции открыты для всех."
    if plan == "premium":
        until = accounts.premium_until(uid)
        return f"💎 Подписка действует до {time.strftime('%d.%m.%Y', time.localtime(until))}."
    lim = limits_of("free")
    parts = [f"{KIND_TITLES[k]}: {n}" + (f" за {d} дн." if d > 1 else "") for k, (n, d) in lim.items() if k in KIND_TITLES]
    return "🆓 Бесплатный план — " + "; ".join(parts) + "."
