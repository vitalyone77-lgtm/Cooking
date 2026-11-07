"""
Партнёры: именная ссылка, кабинет по секретной ссылке и подсчёт доли с людей, пришедших по ссылке. Доля постоянная, с каждой оплаты.
Рубли: (неоплаченные партнёру оплаты − налог − комиссия за выплату) × доля. Выплата раз в месяц до 10 числа, если набралось от 1000 ₽, иначе сумма переносится.
Комиссия 4,5%, но не меньше 40 ₽, берётся с каждой выплаты, поэтому считается от всей накопленной суммы. Звёзды: доля считается и выплачивается звёздами отдельно.
Деньги владелец переводит сам и отмечает командой /partnerpaid.
"""
import re
import secrets
import time
from html import escape

import accounts
import config

CODE_RE = re.compile(r"^[A-Za-z0-9_-]{3,32}$")
DEFAULT_SHARE = 80
DEFAULT_TAX = 4        # налог самозанятого с оплат от физлиц, %
COMMISSION = 4.5       # комиссия платёжного сервиса за выплату, %
MIN_FEE = 40           # и не меньше этой суммы за каждую выплату
MIN_PAYOUT = 1000      # выплата от этой суммы
PAYOUT_DAY = 10        # выплата раз в месяц до этого числа


def site() -> str:
    return (config.WEB_APP_URL or "https://menu.ymayaka.ru").rstrip("/")


def ensure(code: str) -> dict:
    """Партнёр по коду; нет — создаём с новой секретной ссылкой. ValueError, если код негодный."""
    if not CODE_RE.match(code or "") or code.startswith("fr_"):      # fr_ занято личными ссылками пользователей
        raise ValueError("Код: от 3 до 32 латинских букв, цифр, - или _, и не начинается с fr_")
    accounts._q("INSERT OR IGNORE INTO partners(code, token, share, tax, clicks, ts) VALUES(?,?,?,?,0,?)",
                (code, secrets.token_urlsafe(18), DEFAULT_SHARE, DEFAULT_TAX, time.time()))
    return get(code)


def _row(r) -> dict | None:
    return {"code": r[0], "token": r[1], "share": r[2], "tax": r[3], "clicks": r[4]} if r else None


def get(code: str) -> dict | None:
    return _row(accounts._q("SELECT code, token, share, tax, clicks FROM partners WHERE code=?", (code,), one=True))


def by_token(token: str) -> dict | None:
    return _row(accounts._q("SELECT code, token, share, tax, clicks FROM partners WHERE token=?", (token,), one=True)) if token else None


def all_partners() -> list[dict]:
    return [_row(r) for r in accounts._q("SELECT code, token, share, tax, clicks FROM partners", all_=True)]


def click(code: str) -> bool:
    return accounts._q("UPDATE partners SET clicks=clicks+1 WHERE code=?", (code,)) > 0


def links(p: dict) -> dict:
    return {"site": f"{site()}/p/{p['code']}", "bot": f"https://t.me/{config.BOT_USERNAME}?start=ref_{p['code']}", "cabinet": f"{site()}/partner/{p['token']}"}


def _money(gross: float, p: dict) -> dict:
    """Расчёт одной выплаты с суммы gross. Отрицательная сумма: возврат после выплаты, вычтется из следующей."""
    tax = gross * p["tax"] / 100
    fee = max(gross * COMMISSION / 100, MIN_FEE) if gross > 0 else gross * COMMISSION / 100
    return {"gross": round(gross, 2), "tax": round(tax, 2), "fee": round(fee, 2), "due": round(max(gross - tax - fee, 0 if gross > 0 else gross) * p["share"] / 100, 2)}


def stats(p: dict) -> dict:
    """Пришло людей, оплатили, суммы и выплаты. Тестовые и возвращённые платежи не считаем, звёзды считаем отдельно."""
    code = p["code"]
    users = accounts._q("SELECT COUNT(*) FROM refs WHERE ref=?", (code,), one=True)[0]
    rows = accounts._q(f"SELECT p.uid, {accounts.RUB_SQL}, p.ts FROM payments p JOIN refs r ON r.uid=p.uid WHERE r.ref=? AND p.currency='RUB' ORDER BY p.ts", (code,), all_=True)
    stars = accounts._q("SELECT p.uid, p.amount FROM payments p JOIN refs r ON r.uid=p.uid WHERE r.ref=? AND p.currency='XTR'", (code,), all_=True)
    months: dict[str, dict] = {}
    for _, amount, ts in rows:
        m = months.setdefault(time.strftime("%Y-%m", time.localtime(ts)), {"gross": 0, "count": 0})
        m["gross"] += amount
        m["count"] += 1
    done = accounts._q("SELECT COALESCE(SUM(gross), 0), COALESCE(SUM(amount), 0), COALESCE(SUM(stars), 0) FROM partner_settlements WHERE code=?", (code,), one=True)
    gross = sum(r[1] for r in rows)
    now = _money(gross - done[0], p)
    stars_total = sum(r[1] for r in stars)
    stars_earned = int(stars_total * p["share"] // 100)
    active = accounts._q("SELECT COUNT(DISTINCT s.uid) FROM subs s JOIN refs r ON r.uid=s.uid JOIN payments p ON p.uid=s.uid "
                         "WHERE r.ref=? AND s.until > ? AND s.source='payment' AND p.currency IN ('RUB', 'XTR')", (code, time.time()), one=True)[0]
    history = accounts._q("SELECT ts, amount, stars FROM partner_settlements WHERE code=? ORDER BY ts DESC LIMIT 24", (code,), all_=True)
    return {"clicks": p["clicks"], "users": users, "buyers": len({r[0] for r in rows} | {r[0] for r in stars}), "active": active, "payments": len(rows), "gross": round(gross, 2),
            "unpaid": now["gross"], "tax": now["tax"], "fee": now["fee"], "due": now["due"], "paid": round(done[1], 2), "balance": now["due"], "ready": now["due"] >= MIN_PAYOUT,
            "stars": stars_total, "stars_due": stars_earned - int(done[2]), "stars_paid": int(done[2]),
            "months": [{"month": m, **v} for m, v in sorted(months.items(), reverse=True)],
            "history": [{"ts": h[0], "amount": h[1], "stars": h[2]} for h in history]}


def settle(code: str) -> float:
    """Выплата рублями сделана: всё накопленное считается выплаченным. -> сумма."""
    p = get(code)
    s = stats(p)
    accounts._q("INSERT INTO partner_settlements(code, gross, amount, stars, ts) VALUES(?,?,?,0,?)", (code, s["unpaid"], s["due"], time.time()))
    return s["due"]


def settle_stars(code: str) -> int:
    """Выплата звёздами сделана. -> сколько звёзд."""
    s = stats(get(code))
    accounts._q("INSERT INTO partner_settlements(code, gross, amount, stars, ts) VALUES(?,0,0,?,?)", (code, s["stars_due"], time.time()))
    return s["stars_due"]


async def monthly_reminder(bot) -> None:
    """Первого числа напоминает владельцу, кому пора выплатить до 10 числа."""
    lines = []
    for p in all_partners():
        s = stats(p)
        if s["ready"] or s["stars_due"] > 0:
            lines.append(f"{p['code']}: {s['due']:g} ₽" + (f", {s['stars_due']} ⭐" if s["stars_due"] > 0 else "") + ("" if s["ready"] else f" (рубли ещё меньше {MIN_PAYOUT} ₽, переносятся)"))
    if not lines:
        return
    for admin in config.ADMIN_TG_IDS:
        try:
            await bot.send_message(admin, f"🤝 Выплаты партнёрам до {PAYOUT_DAY} числа:\n" + "\n".join(lines) + "\n\nПосле перевода: /partnerpaid код", parse_mode=None)
        except Exception:  # noqa: BLE001
            pass


def page(p: dict) -> str:
    s, ln = stats(p), links(p)
    fmt = lambda v: f"{v:,.2f}".replace(",", " ").replace(".00", "") + " ₽"
    months = "".join(f"<tr><td>{m['month']}</td><td>{m['count']}</td><td>{fmt(m['gross'])}</td></tr>" for m in s["months"]) or "<tr><td colspan=3>Оплат пока нет</td></tr>"
    hist = "".join(f"<tr><td>{time.strftime('%d.%m.%Y', time.localtime(h['ts']))}</td><td>{fmt(h['amount']) if h['amount'] else str(int(h['stars'])) + ' ⭐'}</td></tr>" for h in s["history"]) or "<tr><td colspan=2>Выплат пока не было</td></tr>"
    note = "Выплата в этом месяце, до 10 числа." if s["ready"] else f"До выплаты нужно набрать {MIN_PAYOUT} ₽, сумма переносится на следующий месяц."
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>Кабинет партнёра</title><style>
:root{{--bg:#FBF7F2;--surface:#fff;--text:#2B2622;--muted:#6B625A;--line:#E7DED3;--primary:#2F6B4F}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1A1714;--surface:#25211D;--text:#F1EAE2;--muted:#B5AA9E;--line:#3A342E;--primary:#7CC5A0}}}}
*{{box-sizing:border-box}}body{{margin:0 auto;max-width:640px;padding:16px;background:var(--bg);color:var(--text);font:17px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif}}
h1{{font-size:24px;margin:8px 0}}h2{{font-size:18px;margin:20px 0 6px}}.sub{{color:var(--muted);font-size:15px}}code{{word-break:break-all}}
table{{border-collapse:collapse;width:100%;background:var(--surface)}}th,td{{border:1px solid var(--line);padding:8px 10px;text-align:left;font-variant-numeric:tabular-nums}}th{{font-weight:600}}
.big{{font-size:32px;font-weight:700;color:var(--primary)}}
</style></head><body><h1>Кабинет партнёра: {escape(p['code'])}</h1>
<p class="sub" style="margin-bottom:0">Накоплено к выплате</p><p class="big" style="margin:0">{fmt(s['due'])}</p><p class="sub" style="margin-top:0">{note}</p>
<h2>Ваши ссылки</h2><p>Сайт: <code>{escape(ln['site'])}</code><br>Бот: <code>{escape(ln['bot'])}</code></p>
<h2>Клиенты</h2><table><tr><th>Переходов на сайт</th><td>{s['clicks']}</td></tr><tr><th>Пришло людей</th><td>{s['users']}</td></tr><tr><th>Оплатили хотя бы раз</th><td>{s['buyers']}</td></tr><tr><th>Оплаченных подписок сейчас</th><td>{s['active']}</td></tr></table>
<h2>Расчёт следующей выплаты</h2><table><tr><th>Оплаты с прошлой выплаты</th><td>{fmt(s['unpaid'])}</td></tr><tr><th>Налог {p['tax']:g}%</th><td>{fmt(s['tax'])}</td></tr><tr><th>Комиссия платёжного сервиса {COMMISSION:g}%, минимум {MIN_FEE} ₽ за выплату</th><td>{fmt(s['fee'])}</td></tr><tr><th>Ваша доля {p['share']:g}%</th><td>{fmt(s['due'])}</td></tr></table>
<h2>Оплаты звёздами в Telegram</h2><table><tr><th>Всего звёзд</th><td>{s['stars']}</td></tr><tr><th>Ваша доля к выплате</th><td>{s['stars_due']} ⭐</td></tr><tr><th>Выплачено</th><td>{s['stars_paid']} ⭐</td></tr></table>
<h2>Оплаты по месяцам</h2><table><tr><th>Месяц</th><th>Оплат</th><th>Сумма</th></tr>{months}</table>
<h2>Выплаты</h2><table><tr><th>Дата</th><th>Сумма</th></tr>{hist}</table><p class="sub">Всего выплачено: {fmt(s['paid'])}</p>
<p class="sub" style="margin-top:16px">Считаются оплаты от людей, которые впервые пришли по вашей ссылке. Оплаченные подписки сейчас: сколько ваших клиентов имеют действующую подписку, когда бы они ни пришли; пробный период не считается. Выплаты раз в месяц до {PAYOUT_DAY} числа, от {MIN_PAYOUT} ₽. Эта страница личная, не пересылайте её адрес.</p></body></html>"""
