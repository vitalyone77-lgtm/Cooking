"""
Тарифы: что доступно бесплатно и что — по подписке. Всё настраивается в .env (см. config.py, раздел «Подписка»).

Пока PAYWALL_ENABLED=false, у всех один план «open» — прежние лимиты, ничего не меняется.
После включения:
  anon    — сайт без входа через Telegram (минимум: попробовать);
  free    — аккаунт Telegram без подписки;
  premium — подписка, пробный период или промокод.
"""
import time

import accounts
import config


def _parse_limits(spec: str) -> dict[str, tuple[int, int]]:
    out = {}
    for part in spec.split(","):
        if "=" not in part:
            continue
        kind, val = part.split("=", 1)
        n, _, days = val.partition("/")
        try:
            out[kind.strip()] = (int(n), max(1, int(days or 1)))
        except ValueError:
            continue
    return out


LIMITS = {
    "open": _parse_limits(config.LIMITS_OPEN),
    "free": _parse_limits(config.LIMITS_FREE),
    "premium": _parse_limits(config.LIMITS_PREMIUM),
    "anon": _parse_limits(config.LIMITS_ANON),
}

PLAN_TITLES = {"open": "Полный доступ", "free": "Бесплатный", "premium": "Подписка", "anon": "Без входа"}
KIND_TITLES = {"recipe": "блюд в день", "daymenu": "меню на день", "week": "планов на 3 дня", "missing": "замен продуктов"}


def is_anon(uid) -> bool:
    return str(uid).startswith("w")


def plan_of(uid) -> str:
    if not config.PAYWALL_ENABLED:
        return "open"
    if is_anon(uid):
        return "anon"
    return "premium" if accounts.is_premium(uid) else "free"


def limit(uid, kind: str) -> tuple[int, int] | None:
    """(сколько, за сколько дней) для вида подбора или None — без ограничения (например, день плана)."""
    return LIMITS.get(plan_of(uid), {}).get(kind)


def has_feature(uid, feature: str) -> bool:
    """pdf / cart / noads — при включённой подписке только для неё."""
    if not config.PAYWALL_ENABLED or feature not in config.PREMIUM_FEATURES:
        return True
    return plan_of(uid) == "premium"


def can_upsell(uid) -> bool:
    """Есть ли смысл показывать «Оформить подписку» (подписка включена и её ещё нет)."""
    return config.PAYWALL_ENABLED and plan_of(uid) != "premium"


def offers() -> list[dict]:
    """[{key, days, rub, stars, title}] из PREMIUM_PLANS."""
    out = []
    for part in config.PREMIUM_PLANS.split(","):
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


def promo_days(code: str) -> int:
    for part in config.PROMO_CODES.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip().upper() == code.strip().upper():
                try:
                    return max(0, int(v))
                except ValueError:
                    return 0
    return 0


def maybe_start_trial(uid) -> float | None:
    """Первый упор в лимит бесплатного плана — включаем пробный полный доступ (один раз на аккаунт)."""
    if not config.PAYWALL_ENABLED or is_anon(uid) or config.TRIAL_DAYS <= 0:
        return None
    return accounts.start_trial(uid, config.TRIAL_DAYS)


def status_text(uid) -> str:
    plan = plan_of(uid)
    if plan == "open":
        return "💎 Сейчас все функции открыты для всех."
    if plan == "premium":
        until = accounts.premium_until(uid)
        return f"💎 Подписка действует до {time.strftime('%d.%m.%Y', time.localtime(until))}."
    lim = LIMITS["free"]
    parts = [f"{KIND_TITLES[k]}: {n}" + (f" за {d} дн." if d > 1 else "") for k, (n, d) in lim.items() if k in KIND_TITLES]
    return "🆓 Бесплатный план — " + "; ".join(parts) + "."
