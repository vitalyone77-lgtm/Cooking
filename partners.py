"""
Партнёры: именная ссылка, кабинет по секретной ссылке и подсчёт доли. Считаем только настоящие рубли с людей, пришедших по ссылке:
(оплаты − налог − комиссия платёжного сервиса 4,5%, минимум 40 ₽ в месяц) × доля, постоянно, с каждой оплаты. Выплаты владелец делает сам и отмечает командой /partnerpaid.
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
COMMISSION = 4.5       # комиссия платёжного сервиса, %
MIN_FEE = 40           # и не меньше этой суммы: считаем за календарный месяц, как один вывод денег


def site() -> str:
    return (config.WEB_APP_URL or "https://menu.ymayaka.ru").rstrip("/")


def ensure(code: str) -> dict:
    """Партнёр по коду; нет — создаём с новой секретной ссылкой. ValueError, если код негодный."""
    if not CODE_RE.match(code or ""):
        raise ValueError("Код: от 3 до 32 латинских букв, цифр, - или _")
    accounts._q("INSERT OR IGNORE INTO partners(code, token, share, tax, clicks, ts) VALUES(?,?,?,?,0,?)",
                (code, secrets.token_urlsafe(18), DEFAULT_SHARE, DEFAULT_TAX, time.time()))
    return get(code)


def _row(r) -> dict | None:
    return {"code": r[0], "token": r[1], "share": r[2], "tax": r[3], "clicks": r[4]} if r else None


def get(code: str) -> dict | None:
    return _row(accounts._q("SELECT code, token, share, tax, clicks FROM partners WHERE code=?", (code,), one=True))


def by_token(token: str) -> dict | None:
    return _row(accounts._q("SELECT code, token, share, tax, clicks FROM partners WHERE token=?", (token,), one=True)) if token else None


def click(code: str) -> bool:
    return accounts._q("UPDATE partners SET clicks=clicks+1 WHERE code=?", (code,)) > 0


def links(p: dict) -> dict:
    return {"site": f"{site()}/p/{p['code']}", "bot": f"https://t.me/{config.BOT_USERNAME}?start=ref_{p['code']}", "cabinet": f"{site()}/partner/{p['token']}"}


def _money(gross: float, p: dict) -> dict:
    tax, fee = gross * p["tax"] / 100, (max(gross * COMMISSION / 100, MIN_FEE) if gross > 0 else 0)
    return {"gross": round(gross, 2), "tax": round(tax, 2), "fee": round(fee, 2), "due": round((gross - tax - fee) * p["share"] / 100, 2)}


def stats(p: dict) -> dict:
    """Пришло людей, оплатили, суммы всего и по месяцам. Только настоящие рубли: тестовые платежи и звёзды не считаем."""
    users = accounts._q("SELECT COUNT(*) FROM refs WHERE ref=?", (p["code"],), one=True)[0]
    rows = accounts._q(f"SELECT p.uid, {accounts.RUB_SQL}, p.ts FROM payments p JOIN refs r ON r.uid=p.uid WHERE r.ref=? AND p.currency='RUB' ORDER BY p.ts", (p["code"],), all_=True)
    months: dict[str, float] = {}
    for _, amount, ts in rows:
        m = time.strftime("%Y-%m", time.localtime(ts))
        months[m] = months.get(m, 0) + amount
    paid = accounts._q("SELECT COALESCE(SUM(amount), 0) FROM partner_payouts WHERE code=?", (p["code"],), one=True)[0]
    by_month = [{"month": m, **_money(v, p)} for m, v in sorted(months.items(), reverse=True)]
    total = {k: round(sum(m[k] for m in by_month), 2) for k in ("gross", "tax", "fee", "due")}       # минимум комиссии действует в каждом месяце
    now = time.time()
    active = accounts._q("SELECT COUNT(DISTINCT s.uid) FROM subs s JOIN refs r ON r.uid=s.uid JOIN payments p ON p.uid=s.uid "
                         "WHERE r.ref=? AND s.until > ? AND s.source='payment' AND p.currency='RUB'", (p["code"], now), one=True)[0]
    return {"clicks": p["clicks"], "users": users, "buyers": len({r[0] for r in rows}), "active": active, "payments": len(rows), **total, "paid": round(paid, 2),
            "balance": round(total["due"] - paid, 2), "months": by_month}


def payout(code: str, amount: float) -> None:
    accounts._q("INSERT INTO partner_payouts(code, amount, ts) VALUES(?,?,?)", (code, float(amount), time.time()))


def page(p: dict) -> str:
    s, ln = stats(p), links(p)
    fmt = lambda v: f"{v:,.2f}".replace(",", " ").replace(".00", "") + " ₽"
    months = "".join(f"<tr><td>{m['month']}</td><td>{fmt(m['gross'])}</td><td>{fmt(m['due'])}</td></tr>" for m in s["months"]) or "<tr><td colspan=3>Оплат пока нет</td></tr>"
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>Кабинет партнёра</title><style>
:root{{--bg:#FBF7F2;--surface:#fff;--text:#2B2622;--muted:#6B625A;--line:#E7DED3;--primary:#2F6B4F}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1A1714;--surface:#25211D;--text:#F1EAE2;--muted:#B5AA9E;--line:#3A342E;--primary:#7CC5A0}}}}
*{{box-sizing:border-box}}body{{margin:0 auto;max-width:640px;padding:16px;background:var(--bg);color:var(--text);font:17px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif}}
h1{{font-size:24px;margin:8px 0}}h2{{font-size:18px;margin:20px 0 6px}}.sub{{color:var(--muted);font-size:15px}}code{{word-break:break-all}}
table{{border-collapse:collapse;width:100%;background:var(--surface)}}th,td{{border:1px solid var(--line);padding:8px 10px;text-align:left;font-variant-numeric:tabular-nums}}th{{font-weight:600}}
.big{{font-size:32px;font-weight:700;color:var(--primary)}}
</style></head><body><h1>Кабинет партнёра: {escape(p['code'])}</h1>
<p class="sub">К выплате сейчас</p><p class="big" style="margin:0">{fmt(s['balance'])}</p>
<h2>Ваши ссылки</h2><p>Сайт: <code>{escape(ln['site'])}</code><br>Бот: <code>{escape(ln['bot'])}</code></p>
<h2>Итоги</h2><table><tr><th>Переходов на сайт</th><td>{s['clicks']}</td></tr><tr><th>Пришло людей</th><td>{s['users']}</td></tr><tr><th>Оплатили хотя бы раз</th><td>{s['buyers']}</td></tr><tr><th>Оплаченных подписок сейчас</th><td>{s['active']}</td></tr><tr><th>Оплат</th><td>{s['payments']}</td></tr>
<tr><th>Сумма оплат</th><td>{fmt(s['gross'])}</td></tr><tr><th>Налог {p['tax']:g}%</th><td>{fmt(s['tax'])}</td></tr><tr><th>Комиссия платёжного сервиса {COMMISSION:g}%, минимум {MIN_FEE} ₽ в месяц</th><td>{fmt(s['fee'])}</td></tr><tr><th>Ваша доля {p['share']:g}%</th><td>{fmt(s['due'])}</td></tr><tr><th>Уже выплачено</th><td>{fmt(s['paid'])}</td></tr></table>
<h2>По месяцам</h2><table><tr><th>Месяц</th><th>Оплаты</th><th>Ваша доля</th></tr>{months}</table>
<p class="sub" style="margin-top:16px">Считаются оплаты в рублях от людей, которые впервые пришли по вашей ссылке. Оплаченные подписки сейчас: сколько ваших клиентов имеют действующую подписку, когда бы они ни пришли; пробный период не считается. Эта страница личная, не пересылайте её адрес.</p></body></html>"""
