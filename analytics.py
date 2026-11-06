"""
Что люди делают и где отваливаются: воронка, функции, выборы в анкете и слова запросов. Данные для решений, какие функции улучшать.
Слова запросов хранятся без привязки к человеку (usage.word_stats). Смотреть: админка, раздел «Пользователи», или /stats в боте.
"""
import time

import accounts
import usage

CHOICES = {"cuisine": "Тип питания", "time": "Время", "servings": "Порции", "appliance": "Техника"}
WORDS = {"pref": "Что хотят приготовить", "exc": "Что исключают", "diary": "Чего нет в справочнике дневника", "book": "Что ищут в книге"}


def _uids(sql: str, args=()) -> set[str]:
    return {r[0] for r in usage.rows(sql, args)}


def funnel(days: int = 30) -> list[dict]:
    """Шаги воронки за период: сколько людей дошло до каждого. Считаем только тех, кто появился за этот период."""
    since = time.time() - days * 86400
    new = _uids("SELECT uid FROM seen WHERE first >= ?", (since,))
    gens = usage.rows("SELECT uid, ts FROM gens WHERE ts >= ? AND kind != 'reminder'", (since,))
    made = {u for u, _ in gens}
    days_by: dict[str, set] = {}
    for u, ts in gens:
        days_by.setdefault(u, set()).add(time.strftime("%Y-%m-%d", time.localtime(ts)))
    back = {u for u, d in days_by.items() if len(d) >= 2}
    ev = lambda *names: _uids(f"SELECT uid FROM events WHERE ts >= ? AND name IN ({','.join('?' * len(names))})", (since, *names))
    limit = _uids("SELECT uid FROM events WHERE ts >= ? AND name LIKE 'limit_%'", (since,))
    gate = _uids("SELECT uid FROM events WHERE ts >= ? AND name LIKE 'gate_%'", (since,))
    paid = {r[0] for r in accounts._q("SELECT uid FROM payments WHERE ts >= ? AND currency != 'RUBTEST'", (since,), all_=True)}
    steps = [("Новые люди", new), ("Сделали подбор", made), ("Вернулись в другой день", back), ("Вошли на сайте", ev("site_link", "site_link_vk")), ("Упёрлись в лимит", limit),
             ("Нажали закрытую функцию", gate), ("Начали пробный период", ev("trial_start")), ("Открыли подписку", ev("sub_view", "pay_start")), ("Оплатили", paid)]
    return [{"step": t, "users": len(s)} for t, s in steps]


def features(days: int = 30) -> list[dict]:
    """Функции по числу людей: что востребовано, а что можно упростить или убрать."""
    since = time.time() - days * 86400
    rows = usage.rows("SELECT name, COUNT(*), COUNT(DISTINCT uid) FROM events WHERE ts >= ? AND name NOT LIKE 'c\\_%' ESCAPE '\\' GROUP BY name ORDER BY 3 DESC, 2 DESC", (since,))
    gens = usage.rows("SELECT 'подбор: ' || kind, COUNT(*), COUNT(DISTINCT uid) FROM gens WHERE ts >= ? AND kind != 'reminder' GROUP BY kind", (since,))
    out = [{"name": r[0], "times": r[1], "users": r[2]} for r in list(rows) + list(gens)]
    return sorted(out, key=lambda x: (-x["users"], -x["times"]))


def choices(days: int = 30) -> dict[str, list]:
    since = time.time() - days * 86400
    out = {}
    for key, title in CHOICES.items():
        rows = usage.rows("SELECT name, COUNT(*) FROM events WHERE ts >= ? AND name LIKE ? GROUP BY name ORDER BY 2 DESC LIMIT 10", (since, f"c_{key}:%"))
        out[title] = [[r[0].split(":", 1)[1], r[1]] for r in rows]
    return out


def report(days: int = 30) -> dict:
    return {"days": days, "funnel": funnel(days), "features": features(days), "choices": choices(days),
            "words": {t: usage.top_words(k, days) for k, t in WORDS.items()}, "refs": accounts.ref_stats()}


def report_text(days: int = 30) -> str:
    r = report(days)
    lines = [f"📊 За {days} дней", "", "Воронка:"] + [f"{s['users']} · {s['step']}" for s in r["funnel"]]
    lines += ["", "Функции (людей · раз):"] + [f"{f['users']} · {f['times']} · {f['name']}" for f in r["features"][:12]]
    for title, rows in r["words"].items():
        if rows:
            lines += ["", f"{title}: " + ", ".join(f"{w} ({n})" for w, n in rows[:10])]
    lines += ["", "Подробнее в админке, раздел «Пользователи»."]
    return "\n".join(lines)[:3900]
