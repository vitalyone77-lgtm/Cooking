"""
Личный кабинет: один аккаунт = id в Telegram. Общий SQLite для бота и сайта (два процесса, режим WAL).

- «полка»: долгоживущие продукты, которые человек купил или отметил «есть дома»;
- привязка устройства сайта к Telegram (одноразовый код, сайт → бот);
- подписка: до какого времени действует, платежи (без повторного зачисления), промокоды, откуда пришёл (ref);
- короткие сессии бота (режим готовки по шагам, список покупок с галочками) — переживают перезапуск.

uid аккаунта: str(tg_id) для Telegram и привязанного сайта, "w<id устройства>" для сайта без привязки.
"""
import json
import os
import re
import secrets
import sqlite3
import threading
import time

DB_PATH = os.getenv("ACCOUNTS_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "accounts.db"))
_lock = threading.Lock()
_ready = False

SCHEMA = """
CREATE TABLE IF NOT EXISTS shelf(uid TEXT, item TEXT, ts REAL, PRIMARY KEY(uid, item));
CREATE TABLE IF NOT EXISTS links(dev_id INTEGER PRIMARY KEY, tg_id INTEGER, ts REAL);
CREATE INDEX IF NOT EXISTS links_tg ON links(tg_id);
CREATE TABLE IF NOT EXISTS link_codes(code TEXT PRIMARY KEY, dev_id INTEGER, created REAL, tg_id INTEGER);
CREATE TABLE IF NOT EXISTS subs(uid TEXT PRIMARY KEY, until REAL, source TEXT, trial_used INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS payments(charge_id TEXT PRIMARY KEY, uid TEXT, amount INTEGER, currency TEXT,
  days INTEGER, plan TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS promo_used(uid TEXT, code TEXT, ts REAL, PRIMARY KEY(uid, code));
CREATE TABLE IF NOT EXISTS refs(uid TEXT PRIMARY KEY, ref TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS friend_codes(code TEXT PRIMARY KEY, uid TEXT, ts REAL);
CREATE INDEX IF NOT EXISTS friend_codes_uid ON friend_codes(uid);
CREATE TABLE IF NOT EXISTS friend_rewards(friend TEXT PRIMARY KEY, inviter TEXT, base INTEGER, days INTEGER, paid INTEGER DEFAULT 0, ts REAL);
CREATE INDEX IF NOT EXISTS friend_rewards_inviter ON friend_rewards(inviter, ts);
CREATE TABLE IF NOT EXISTS sess(key TEXT PRIMARY KEY, data TEXT, ts REAL);
CREATE INDEX IF NOT EXISTS sess_ts ON sess(ts);
CREATE TABLE IF NOT EXISTS favs(uid TEXT, id TEXT, title TEXT, text TEXT, cuisine TEXT, ts REAL, PRIMARY KEY(uid, id));
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT, ts REAL, who TEXT);
CREATE TABLE IF NOT EXISTS admin_log(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, who TEXT, action TEXT, details TEXT);
CREATE TABLE IF NOT EXISTS diary(id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT, day TEXT, ts REAL, title TEXT, kcal INTEGER,
  protein REAL, fat REAL, carb REAL, src TEXT);
CREATE INDEX IF NOT EXISTS diary_uid_day ON diary(uid, day);
CREATE TABLE IF NOT EXISTS energy(uid TEXT PRIMARY KEY, activity TEXT, tdee INTEGER, updated REAL);
CREATE TABLE IF NOT EXISTS pub_recipes(slug TEXT PRIMARY KEY, ts REAL, title TEXT, body TEXT, image BLOB);
CREATE TABLE IF NOT EXISTS feedback(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, uid TEXT, kind TEXT, text TEXT, contact TEXT,
  src TEXT, done INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS consents(uid TEXT PRIMARY KEY, version INTEGER, ts REAL);
CREATE TABLE IF NOT EXISTS goals(uid TEXT PRIMARY KEY, feats TEXT DEFAULT '', never TEXT DEFAULT '', kcal INTEGER, protein INTEGER,
  fat INTEGER, carb INTEGER, weight_goal REAL, updated REAL);
CREATE TABLE IF NOT EXISTS weights(uid TEXT, day TEXT, kg REAL, PRIMARY KEY(uid, day));
CREATE TABLE IF NOT EXISTS book(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, title TEXT, key TEXT UNIQUE, text TEXT, terms TEXT, cuisine TEXT,
  meal TEXT, minutes INTEGER, kcal INTEGER, protein REAL, fat REAL, carb REAL, servings INTEGER, portion TEXT, appliance TEXT, tags TEXT,
  text_l TEXT, uses INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS book_find ON book(cuisine, servings, portion);
CREATE INDEX IF NOT EXISTS book_meal ON book(meal);
CREATE TABLE IF NOT EXISTS book_seen(uid TEXT, book_id INTEGER, ts REAL, PRIMARY KEY(uid, book_id));
CREATE TABLE IF NOT EXISTS alias(src TEXT PRIMARY KEY, dst TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS pay_orders(id TEXT PRIMARY KEY, uid TEXT, plan TEXT, days INTEGER, amount TEXT, test INTEGER, ts REAL, status TEXT, op TEXT);
CREATE INDEX IF NOT EXISTS pay_orders_uid ON pay_orders(uid, ts);
CREATE TABLE IF NOT EXISTS book_votes(book_id INTEGER, uid TEXT, vote INTEGER, ts REAL, PRIMARY KEY(book_id, uid));
CREATE TABLE IF NOT EXISTS partners(code TEXT PRIMARY KEY, token TEXT UNIQUE, share REAL, tax REAL, clicks INTEGER, ts REAL);
CREATE TABLE IF NOT EXISTS partner_settlements(id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT, gross REAL, amount REAL, stars INTEGER, ts REAL);
CREATE TABLE IF NOT EXISTS macro_ref(uid TEXT PRIMARY KEY, p REAL, f REAL, c REAL);
CREATE TABLE IF NOT EXISTS shop_lists(id TEXT PRIMARY KEY, uid TEXT, title TEXT, items TEXT, done TEXT, ts REAL);
CREATE INDEX IF NOT EXISTS shop_lists_uid ON shop_lists(uid, ts);
CREATE TABLE IF NOT EXISTS ai_foods(id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT UNIQUE, name TEXT, grams REAL, kcal100 REAL, p100 REAL, f100 REAL, c100 REAL, label TEXT, ts REAL);
"""


class _KeepOpen(sqlite3.Connection):
    def close(self):          # вызывающий код закрывает «по привычке» — соединение общее, не закрываем
        pass


_shared = None


def _conn():
    global _ready
    global _shared
    if _shared is not None:
        return _shared
    # одно соединение на процесс (доступ — под _lock): открытие/закрытие на каждый запрос в режиме WAL
    # каждый раз запускает checkpoint и под нагрузкой тормозило бота в разы
    c = sqlite3.connect(DB_PATH, timeout=3, check_same_thread=False, factory=_KeepOpen)
    c.execute("PRAGMA synchronous=NORMAL")   # с WAL безопасно и без ожидания диска на каждой записи
    if not _ready:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)
        if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='profiles'").fetchone():
            # аллергии и «не использовать» больше не храним: удаляем таблицу и вычищаем её следы из файла
            c.execute("DROP TABLE profiles")
            c.commit()
            c.execute("VACUUM")
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        _ready = True
    _shared = c
    return c


def _q(sql: str, args=(), one=False, all_=False):
    with _lock:
        c = _conn()
        try:
            with c:
                cur = c.execute(sql, args)
                if one:
                    return cur.fetchone()
                if all_:
                    return cur.fetchall()
                return cur.rowcount
        finally:
            c.close()


# ---------- общие помощники ----------


def _clean_list(text: str) -> str:
    """«Орехи;  молоко,\nкинза» -> «орехи, молоко, кинза» (до 300 символов)."""
    parts = [p.strip(" .;-").lower() for p in text.replace(";", ",").replace("\n", ",").split(",")]
    out = []
    for p in parts:
        if p and p not in out and p not in ("нет", "-", "ничего"):
            out.append(p[:40])
    return ", ".join(out)[:300]


# ---------- полка (долгоживущие продукты) ----------

# Сколько дней продукт «скорее всего ещё есть»: ключ — начало слова, значение — дни
SHELF_LIFE = {
    "масл": 60, "соль": 365, "сахар": 180, "перец": 180, "специ": 180, "приправ": 180, "паприк": 180,
    "куркум": 180, "кумин": 180, "зира": 180, "кориандр": 180, "корица": 180, "карри": 180, "имбирь мол": 180,
    "орегано": 180, "базилик сух": 180, "прованск": 180, "лавров": 365, "мука": 120, "крахмал": 180,
    "сода": 365, "разрыхл": 180, "уксус": 365, "соев": 120, "горчиц": 90, "кетчуп": 60, "томатн паст": 30,
    "мёд": 365, "мед ": 365, "рис": 120, "гречк": 120, "овсян": 90, "хлопь": 90, "макарон": 120, "паст": 120,
    "спагетти": 120, "чечевиц": 180, "нут": 180, "фасоль сух": 180, "пшен": 120, "булгур": 120, "киноа": 120,
    "кускус": 120, "семен": 120, "кунжут": 120, "чиа": 120, "орех": 60, "какао": 180, "чеснок сух": 180,
    "ваниль": 365, "желатин": 365, "бульонн": 180, "дрожж": 60,
}
_SHELF_EXCLUDE = ("сливочн", "свеж", "зелен", "лист")   # «масло сливочное», «свежий базилик» — не на полку


def shelf_life_days(item: str) -> int | None:
    """Сколько дней держится продукт на полке, или None — скоропортящийся (не для полки)."""
    t = " " + item.lower().replace("ё", "е") + " "
    if any(x in t for x in _SHELF_EXCLUDE):
        return None
    for key, days in SHELF_LIFE.items():
        k = key.replace("ё", "е")
        if all((" " + w) in t for w in k.split()):
            return days
    return None


def shelf_add(uid, item: str) -> bool:
    item = item.strip().lower()[:60]
    if not item:
        return False
    _q("INSERT INTO shelf(uid, item, ts) VALUES(?,?,?) ON CONFLICT(uid, item) DO UPDATE SET ts=excluded.ts",
       (str(uid), item, time.time()))
    return True


def shelf_remove(uid, item: str) -> None:
    _q("DELETE FROM shelf WHERE uid=? AND item=?", (str(uid), item.strip().lower()))


def shelf_list(uid) -> list[dict]:
    """Что «скорее всего ещё есть»: устаревшие по сроку не показываем (и удаляем)."""
    rows = _q("SELECT item, ts FROM shelf WHERE uid=? ORDER BY item", (str(uid),), all_=True)
    now, out = time.time(), []
    for item, ts in rows:
        life = shelf_life_days(item) or 60
        if now - ts > life * 86400:
            shelf_remove(uid, item)
            continue
        out.append({"item": item, "ts": ts})
    return out


def _norm(w: str) -> str:
    return w.lower().replace("ё", "е").strip()


def _same_product(a: str, b: str) -> bool:
    """«масло подсолнечное» ~ «Масло подсолнечное рафинированное», «соль» ~ «соль морская»: совпадают основы
    первого слова и (если у обоих есть) второго."""
    wa, wb = _norm(a).split(), _norm(b).split()
    if not wa or not wb:
        return False
    stem = min(len(wa[0]), len(wb[0]), 5)
    if stem < 3 or wa[0][:stem] != wb[0][:stem]:
        return False
    if len(wa) > 1 and len(wb) > 1:
        return wa[1][:4] == wb[1][:4]
    return True


def shelf_match(uid, terms: list[str]) -> set[int]:
    """Номера продуктов из списка покупок, которые уже на полке (только долгоживущие — «масло сливочное» нет)."""
    shelf = [s["item"] for s in shelf_list(uid)]
    if not shelf:
        return set()
    return {i for i, term in enumerate(terms)
            if shelf_life_days(term) and any(_same_product(term, s) for s in shelf)}


# ---------- привязка сайта к Telegram ----------

LINK_CODE_TTL = 600   # 10 минут


def new_link_code(dev_id: int) -> str:
    code = secrets.token_urlsafe(18)          # 24 символа [A-Za-z0-9_-] — помещается в /start (до 64)
    _q("DELETE FROM link_codes WHERE created < ? OR (dev_id=? AND tg_id IS NULL)", (time.time() - LINK_CODE_TTL, dev_id))
    _q("INSERT INTO link_codes(code, dev_id, created) VALUES(?,?,?)", (code, dev_id, time.time()))
    return code


def use_link_code(code: str, tg_id: int) -> int | None:
    """Бот получил /start link_<код>: привязать устройство к аккаунту. Одноразово. -> dev_id или None."""
    if not code or len(code) > 64:
        return None
    with _lock:
        c = _conn()
        try:
            with c:
                cur = c.execute("UPDATE link_codes SET tg_id=? WHERE code=? AND tg_id IS NULL AND created >= ?",
                                (tg_id, code, time.time() - LINK_CODE_TTL))
                if cur.rowcount != 1:
                    return None
                dev_id = c.execute("SELECT dev_id FROM link_codes WHERE code=?", (code,)).fetchone()[0]
                _link(c, dev_id, tg_id)
                return dev_id
        finally:
            c.close()


def new_login_code(tg_id: int) -> str:
    """Ссылка «открыть сайт уже со входом» из бота: одноразовый код, 10 минут."""
    code = secrets.token_urlsafe(18)
    _q("INSERT INTO link_codes(code, dev_id, created, tg_id) VALUES(?, -1, ?, ?)", (code, time.time(), tg_id))
    return code


def claim_login_code(code: str, dev_id: int) -> int | None:
    """Сайт открыт по ссылке из бота: привязать это устройство. Одноразово (код помечается dev_id). -> tg_id."""
    if not code or len(code) > 64:
        return None
    with _lock:
        c = _conn()
        try:
            with c:
                cur = c.execute("UPDATE link_codes SET dev_id=? WHERE code=? AND dev_id=-1 AND created >= ?",
                                (dev_id, code, time.time() - LINK_CODE_TTL))
                if cur.rowcount != 1:
                    return None
                tg_id = c.execute("SELECT tg_id FROM link_codes WHERE code=?", (code,)).fetchone()[0]
                _link(c, dev_id, tg_id)
                return tg_id
        finally:
            c.close()


def link_code_valid(code: str) -> bool:
    """Код входа с сайта ещё действует и не использован (для кнопки подтверждения в боте)."""
    row = _q("SELECT 1 FROM link_codes WHERE code=? AND tg_id IS NULL AND dev_id >= 0 AND created >= ?",
             (code[:64], time.time() - LINK_CODE_TTL), one=True)
    return bool(row)


def unlink_all(tg_id: int) -> int:
    """Отвязать от аккаунта все устройства сайта (например, если входили по чужой ссылке)."""
    return _q("DELETE FROM links WHERE tg_id=?", (tg_id,))


def devices_of(tg_id: int) -> int:
    return _q("SELECT COUNT(*) FROM links WHERE tg_id=?", (tg_id,), one=True)[0]


def link_code_status(code: str, dev_id: int) -> bool:
    """Сайт опрашивает: код уже подтвердили в боте?"""
    row = _q("SELECT tg_id FROM link_codes WHERE code=? AND dev_id=?", (code, dev_id), one=True)
    return bool(row and row[0])


VK_OFFSET = 500_000_000_000     # аккаунт ВКонтакте хранится как «число-аккаунт» = смещение + номер в ВК (id в Telegram такими большими не бывают)


def link_device_vk(dev_id: int, vk_id: int) -> int:
    """Вошли через VK ID. Устройство ещё без входа: привязываем к аккаунту ВК (или к Telegram, если этот ВК уже объединён).
    Устройство уже вошло через Telegram: ВК вливается в этот профиль (ValueError, если этот ВК уже объединён с другим Telegram).
    -> номер аккаунта, к которому привязано устройство."""
    acc = VK_OFFSET + int(vk_id)
    with _lock:
        c = _conn()
        try:
            with c:
                al = c.execute("SELECT dst FROM alias WHERE src=?", (str(acc),)).fetchone()
                cur = c.execute("SELECT tg_id FROM links WHERE dev_id=?", (dev_id,)).fetchone()
                if cur and cur[0] and cur[0] < VK_OFFSET:
                    if al and al[0] != str(cur[0]):
                        raise ValueError("Этот ВКонтакте уже объединён с другим профилем Telegram")
                    _merge_accounts(c, str(acc), str(cur[0]))
                    return cur[0]
                target = int(al[0]) if al else acc
                c.execute("INSERT INTO links(dev_id, tg_id, ts) VALUES(?,?,?) ON CONFLICT(dev_id) DO UPDATE SET "
                          "tg_id=excluded.tg_id, ts=excluded.ts", (dev_id, target, time.time()))
                _merge_device(c, dev_id, target)
                return target
        finally:
            c.close()


def has_vk_merged(tg_id) -> bool:
    """К этому аккаунту Telegram уже присоединён ВКонтакте: профиль общий, входить можно через оба."""
    return bool(_q("SELECT 1 FROM alias WHERE dst=? LIMIT 1", (str(tg_id),), one=True))


def tg_of_device(dev_id: int) -> int | None:
    row = _q("SELECT tg_id FROM links WHERE dev_id=?", (dev_id,), one=True)
    return row[0] if row else None


def unlink_device(dev_id: int) -> None:
    _q("DELETE FROM links WHERE dev_id=?", (dev_id,))


def web_uid(dev_id: int) -> str:
    tg = tg_of_device(dev_id)
    return str(tg) if tg else f"w{dev_id}"


# ---------- подписка ----------

def premium_until(uid) -> float:
    row = _q("SELECT until FROM subs WHERE uid=?", (str(uid),), one=True)
    return (row[0] or 0) if row else 0


def is_premium(uid) -> bool:
    return premium_until(uid) > time.time()


def grant_days(uid, days: int, source: str) -> float:
    """Продлить подписку на days дней (от текущего конца, если ещё действует). -> новое время окончания."""
    with _lock:
        c = _conn()
        try:
            with c:
                row = c.execute("SELECT until, source FROM subs WHERE uid=?", (str(uid),)).fetchone()
                start = max(time.time(), (row[0] or 0) if row else 0)
                until = start + days * 86400
                if row and (row[0] or 0) > time.time() and row[1] == "payment":
                    source = "payment"              # оплаченная остаётся оплаченной: по источнику считают подписки партнёра и сообщения о пробном периоде
                c.execute("INSERT INTO subs(uid, until, source) VALUES(?,?,?) ON CONFLICT(uid) DO UPDATE SET "
                          "until=excluded.until, source=excluded.source", (str(uid), until, source))
                return until
        finally:
            c.close()


def add_bonus_days(uid, days: int) -> float:
    """Дни за друзей: прибавить к сроку или убрать при возврате. Источник действующей подписки не меняем: оплаченная остаётся оплаченной и для партнёра."""
    with _lock:
        c = _conn()
        try:
            with c:
                row = c.execute("SELECT until, source FROM subs WHERE uid=?", (str(uid),)).fetchone()
                now, cur = time.time(), (row[0] or 0) if row else 0
                if days < 0 and not row:
                    return 0
                until = (cur if days < 0 else max(now, cur)) + days * 86400
                source = row[1] if row and cur > now else "friend"
                c.execute("INSERT INTO subs(uid, until, source) VALUES(?,?,?) ON CONFLICT(uid) DO UPDATE SET until=excluded.until, source=excluded.source", (str(uid), until, source))
                return until
        finally:
            c.close()


def trial_used(uid) -> bool:
    """Пробный период уже был, либо человек уже платил: тогда бесплатная неделя не положена."""
    row = _q("SELECT trial_used FROM subs WHERE uid=?", (str(uid),), one=True)
    if row and row[0]:
        return True
    return bool(_q("SELECT 1 FROM payments WHERE uid=? AND currency NOT IN ('RUBTEST', 'RUBREFUND') LIMIT 1", (str(uid),), one=True))


def start_trial(uid, days: int) -> float | None:
    """Пробный период один раз: проверка и выдача одной транзакцией, два одновременных нажатия не дадут две недели."""
    if days <= 0:
        return None
    with _lock:
        c = _conn()
        try:
            with c:
                row = c.execute("SELECT until, trial_used FROM subs WHERE uid=?", (str(uid),)).fetchone()
                paid = c.execute("SELECT 1 FROM payments WHERE uid=? AND currency NOT IN ('RUBTEST', 'RUBREFUND') LIMIT 1", (str(uid),)).fetchone()
                if (row and row[1]) or paid:
                    return None
                until = max(time.time(), (row[0] or 0) if row else 0) + days * 86400
                c.execute("INSERT INTO subs(uid, until, source, trial_used) VALUES(?,?,'trial',1) ON CONFLICT(uid) DO UPDATE SET "
                          "until=excluded.until, source=excluded.source, trial_used=1", (str(uid), until))
                return until
        finally:
            c.close()


def record_payment(charge_id: str, uid, amount: int, currency: str, days: int, plan: str) -> bool:
    """Запомнить платёж. False — этот платёж уже был зачислен (Telegram может прислать его повторно)."""
    try:
        _q("INSERT INTO payments(charge_id, uid, amount, currency, days, plan, ts) VALUES(?,?,?,?,?,?,?)",
           (charge_id, str(uid), amount, currency, days, plan, time.time()))
        return True
    except sqlite3.IntegrityError:
        return False


def apply_payment(charge_id: str, uid, amount: int, currency: str, days: int, plan: str) -> float | None:
    """Зачислить платёж и продлить подписку ОДНОЙ транзакцией (сбой посередине не «съест» деньги).
    None — этот платёж уже зачислен (Telegram может прислать его повторно)."""
    with _lock:
        c = _conn()
        try:
            with c:
                try:
                    c.execute("INSERT INTO payments(charge_id, uid, amount, currency, days, plan, ts) VALUES(?,?,?,?,?,?,?)",
                              (charge_id, str(uid), amount, currency, days, plan, time.time()))
                except sqlite3.IntegrityError:
                    return None
                row = c.execute("SELECT until FROM subs WHERE uid=?", (str(uid),)).fetchone()
                until = max(time.time(), (row[0] or 0) if row else 0) + days * 86400
                c.execute("INSERT INTO subs(uid, until, source) VALUES(?,?,?) ON CONFLICT(uid) DO UPDATE SET "
                          "until=excluded.until, source=excluded.source", (str(uid), until, "payment"))
                return until
        finally:
            c.close()


REFUNDED = "RUBREFUND"       # платёж возвращён: запись остаётся для истории, в выручку и долю партнёра не идёт
RUB_SQL = "CASE WHEN p.charge_id LIKE 'pw:%' THEN p.amount ELSE p.amount / 100.0 END"     # PayAnyWay хранит рубли, Telegram копейки


def payment_by_op(op: str) -> dict | None:
    r = _q("SELECT charge_id, uid, amount, currency, days, plan FROM payments WHERE charge_id=? OR charge_id=?", (f"pw:{op}", str(op)), one=True)
    return dict(zip(("charge_id", "uid", "amount", "currency", "days", "plan"), r)) if r else None


def refund_payment(op: str) -> dict | None:
    """Возврат сделан в кабинете платёжного сервиса: платёж больше не считается, а подписка сокращается на его срок. Повторно ничего не меняет.
    -> {"uid", "amount", "until"} или None, если такого действующего платежа нет."""
    with _lock:
        c = _conn()
        try:
            with c:
                r = c.execute("SELECT charge_id, uid, amount, days FROM payments WHERE (charge_id=? OR charge_id=?) AND currency='RUB'", (f"pw:{op}", str(op))).fetchone()
                if not r:
                    return None
                c.execute("UPDATE payments SET currency=? WHERE charge_id=?", (REFUNDED, r[0]))
                c.execute("UPDATE pay_orders SET status='refunded' WHERE op=?", (str(op),))
                s = c.execute("SELECT until FROM subs WHERE uid=?", (r[1],)).fetchone()
                until = (s[0] or 0) - r[3] * 86400 if s else 0
                if s:
                    c.execute("UPDATE subs SET until=? WHERE uid=?", (until, r[1]))
                return {"uid": r[1], "amount": r[2], "until": until}
        finally:
            c.close()


def use_promo(uid, code: str) -> bool:
    try:
        _q("INSERT INTO promo_used(uid, code, ts) VALUES(?,?,?)", (str(uid), code.upper(), time.time()))
        return True
    except sqlite3.IntegrityError:
        return False


def set_ref(uid, ref: str) -> None:
    """Откуда пришёл человек (t.me/бот?start=ref_блогер). Запоминаем только первый источник и только у нового человека:
    кто уже пользовался сервисом, не считается приведённым по ссылке, иначе партнёру засчитывались бы чужие оплаты."""
    import usage
    ref = "".join(ch for ch in ref if ch.isalnum() or ch in "_-")[:32]
    if ref and not usage.is_old_user(uid):
        _q("INSERT OR IGNORE INTO refs(uid, ref, ts) VALUES(?,?,?)", (str(uid), ref, time.time()))


def ref_stats() -> list[dict]:
    """Для админки: сколько людей пришло по каждой ссылке и сколько из них оплатили."""
    label = "CASE WHEN r.ref LIKE 'fr!_%' ESCAPE '!' THEN 'друзья' ELSE r.ref END"          # личные ссылки пользователей одной строкой
    rows = _q(f"SELECT {label}, COUNT(DISTINCT r.uid), COUNT(DISTINCT p.uid), COALESCE(SUM(CASE WHEN p.currency = 'RUB' THEN {RUB_SQL} ELSE 0 END), 0) FROM refs r "
              f"LEFT JOIN payments p ON p.uid = r.uid AND p.currency NOT IN ('RUBTEST', 'RUBREFUND') GROUP BY {label} ORDER BY 2 DESC", all_=True)
    return [{"ref": r[0], "users": r[1], "paid_users": r[2], "amount": r[3]} for r in rows]


def sub_stats() -> dict:
    now = time.time()
    one = lambda q, *a: _q(q, a, one=True)[0]
    return {
        "active": one("SELECT COUNT(*) FROM subs WHERE until > ?", now),
        "trial_active": one("SELECT COUNT(*) FROM subs WHERE until > ? AND source='trial'", now),
        "payments": one("SELECT COUNT(*) FROM payments"),
        "revenue": one("SELECT COALESCE(SUM(amount), 0) FROM payments"),
    }


# ---------- короткие сессии бота ----------

SESS_TTL = 3 * 86400


def sess_get(key: str) -> dict | None:
    row = _q("SELECT data FROM sess WHERE key=? AND ts > ?", (key, time.time() - SESS_TTL), one=True)
    return json.loads(row[0]) if row else None


def sess_set(key: str, data: dict) -> None:
    _q("INSERT INTO sess(key, data, ts) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET data=excluded.data, ts=excluded.ts",
       (key, json.dumps(data, ensure_ascii=False), time.time()))


def sess_mark_forever(key: str) -> None:
    """Служебная отметка, которую не чистит sess_prune (например, «избранное уже перенесено»)."""
    _q("UPDATE sess SET ts=? WHERE key=?", (4e12, key))


# ---------- избранное ----------

FAV_LIMIT = 200    # на аккаунт: защита от раздувания базы


def fav_add(uid: str, title: str, text: str, cuisine: str) -> str:
    fav_id = str(int(time.time() * 1000))
    with _lock:
        c = _conn()
        try:
            with c:
                same = c.execute("SELECT id FROM favs WHERE uid=? AND title=? AND text=?",
                                 (uid, title[:200], text[:20000])).fetchone()
                if same:              # двойное нажатие «В избранное» — уже сохранено
                    return same[0]
                n = c.execute("SELECT COUNT(*) FROM favs WHERE uid=?", (uid,)).fetchone()[0]
                if n >= FAV_LIMIT:      # самое старое уходит
                    c.execute("DELETE FROM favs WHERE uid=? AND id=(SELECT id FROM favs WHERE uid=? ORDER BY ts LIMIT 1)",
                              (uid, uid))
                while c.execute("SELECT 1 FROM favs WHERE uid=? AND id=?", (uid, fav_id)).fetchone():
                    fav_id = str(int(fav_id) + 1)
                c.execute("INSERT INTO favs(uid, id, title, text, cuisine, ts) VALUES(?,?,?,?,?,?)",
                          (uid, fav_id, title[:200], text[:20000], cuisine or "", time.time()))
        finally:
            c.close()
    return fav_id


def fav_list(uid: str) -> list[dict]:
    rows = _q("SELECT id, title, text, cuisine FROM favs WHERE uid=? ORDER BY ts", (uid,), all_=True)
    return [{"id": r[0], "title": r[1], "text": r[2], "cuisine": r[3]} for r in rows]


def fav_remove(uid: str, fav_id: str) -> bool:
    return _q("DELETE FROM favs WHERE uid=? AND id=?", (uid, fav_id)) > 0


def fav_import(rows: list[tuple]) -> None:
    with _lock:
        c = _conn()
        try:
            with c:
                c.executemany("INSERT OR IGNORE INTO favs(uid, id, title, text, cuisine, ts) VALUES(?,?,?,?,?,?)", rows)
        finally:
            c.close()


_MERGE_TABLES = ("favs", "shelf", "weights", "goals", "energy", "consents", "promo_used", "refs", "book_seen", "macro_ref", "book_votes")


def _merge_accounts(c, src: str, dst: str) -> None:
    """Аккаунт ВКонтакте (src) вливается в аккаунт Telegram (dst): всё, что человек нажил, переходит к dst. При совпадениях остаётся то,
    что уже есть у dst; подписка берётся самая длинная."""
    if src == dst:
        return
    for t in _MERGE_TABLES:
        c.execute(f"UPDATE OR IGNORE {t} SET uid=? WHERE uid=?", (dst, src))
        c.execute(f"DELETE FROM {t} WHERE uid=?", (src,))
    c.execute("UPDATE diary SET uid=? WHERE uid=?", (dst, src))
    c.execute("UPDATE payments SET uid=? WHERE uid=?", (dst, src))
    c.execute("UPDATE pay_orders SET uid=? WHERE uid=?", (dst, src))       # оплата, начатая до объединения, должна прийти в общий профиль
    c.execute("UPDATE shop_lists SET uid=? WHERE uid=?", (dst, src))
    c.execute("UPDATE friend_codes SET uid=? WHERE uid=?", (dst, src))     # старая ссылка продолжает работать и начислять в общий аккаунт
    c.execute("UPDATE friend_rewards SET inviter=? WHERE inviter=?", (dst, src))
    c.execute("UPDATE OR IGNORE friend_rewards SET friend=? WHERE friend=?", (dst, src))
    c.execute("DELETE FROM friend_rewards WHERE friend=?", (src,))
    s = c.execute("SELECT until, source, trial_used FROM subs WHERE uid=?", (src,)).fetchone()
    if s:
        d = c.execute("SELECT until, trial_used FROM subs WHERE uid=?", (dst,)).fetchone()
        if d:
            src_longer = (s[0] or 0) > (d[0] or 0)       # источник от более длинной подписки: оплаченная не должна стать «пробной»
            c.execute("UPDATE subs SET until=?, trial_used=?, source=CASE WHEN ? THEN ? ELSE source END WHERE uid=?",
                      (max(s[0] or 0, d[0] or 0), max(s[2] or 0, d[1] or 0), int(src_longer), s[1], dst))
        else:
            c.execute("INSERT INTO subs(uid, until, source, trial_used) VALUES(?,?,?,?)", (dst, s[0], s[1], s[2]))
        c.execute("DELETE FROM subs WHERE uid=?", (src,))
    c.execute("INSERT INTO alias(src, dst, ts) VALUES(?,?,?) ON CONFLICT(src) DO UPDATE SET dst=excluded.dst, ts=excluded.ts", (src, dst, time.time()))


def _link(c, dev_id: int, tg_id: int) -> None:
    """Привязать устройство сайта к аккаунту Telegram. Если оно уже было привязано к аккаунту ВК, ВК вливается в Telegram."""
    old = c.execute("SELECT tg_id FROM links WHERE dev_id=?", (dev_id,)).fetchone()
    c.execute("INSERT INTO links(dev_id, tg_id, ts) VALUES(?,?,?) ON CONFLICT(dev_id) DO UPDATE SET tg_id=excluded.tg_id, ts=excluded.ts",
              (dev_id, tg_id, time.time()))
    _merge_device(c, dev_id, tg_id)
    if old and old[0] and old[0] >= VK_OFFSET and tg_id < VK_OFFSET:
        _merge_accounts(c, str(old[0]), str(tg_id))


def _merge_device(c, dev_id: int, tg_id: int) -> None:
    """Устройство привязали к Telegram: его избранное (сделанное без входа) переходит в аккаунт."""
    c.execute("UPDATE OR IGNORE favs SET uid=? WHERE uid=?", (str(tg_id), f"w{dev_id}"))
    import usage
    if not usage.is_old_user(tg_id):                                                         # откуда пришёл гость: важно для партнёрской доли, но только у нового аккаунта
        c.execute("UPDATE OR IGNORE refs SET uid=? WHERE uid=?", (str(tg_id), f"w{dev_id}"))
    c.execute("DELETE FROM refs WHERE uid=?", (f"w{dev_id}",))


def sess_items(prefix: str) -> list[tuple[str, dict]]:
    rows = _q("SELECT key, data FROM sess WHERE key LIKE ? AND ts > ?", (prefix + "%", time.time() - SESS_TTL), all_=True)
    out = []
    for k, d in rows:
        try:
            out.append((k, json.loads(d)))
        except ValueError:
            continue
    return out


def sess_prune() -> None:
    _q("DELETE FROM sess WHERE ts < ?", (time.time() - SESS_TTL,))
    _q("DELETE FROM link_codes WHERE created < ?", (time.time() - 86400,))


# ---------- настройки из админки и журнал изменений ----------

def settings_all() -> dict:
    out = {}
    for k, v in _q("SELECT key, value FROM settings", all_=True):
        try:
            out[k] = json.loads(v)
        except ValueError:
            continue
    return out


def settings_set(values: dict, who: str) -> None:
    with _lock:
        c = _conn()
        try:
            with c:
                for k, v in values.items():
                    c.execute("INSERT INTO settings(key, value, ts, who) VALUES(?,?,?,?) ON CONFLICT(key) DO UPDATE SET "
                              "value=excluded.value, ts=excluded.ts, who=excluded.who",
                              (k, json.dumps(v, ensure_ascii=False), time.time(), who))
        finally:
            c.close()


def admin_log(who: str, action: str, details: str = "") -> None:
    _q("INSERT INTO admin_log(ts, who, action, details) VALUES(?,?,?,?)", (time.time(), who, action, details[:2000]))


def admin_log_list(limit: int = 100) -> list[dict]:
    rows = _q("SELECT ts, who, action, details FROM admin_log ORDER BY id DESC LIMIT ?", (limit,), all_=True)
    return [{"ts": r[0], "who": r[1], "action": r[2], "details": r[3]} for r in rows]


# ---------- статистика для админки ----------

def money_stats() -> dict:
    """Выручка по месяцам (рубли и звёзды отдельно), последние платежи, конверсия пробного периода."""
    by_month = {}
    for ts, amount, cur, charge in _q("SELECT ts, amount, currency, charge_id FROM payments", all_=True):
        if cur in ("RUBTEST", REFUNDED):
            continue
        m = time.strftime("%Y-%m", time.localtime(ts))
        d = by_month.setdefault(m, {"month": m, "rub": 0, "stars": 0, "count": 0})
        if cur == "XTR":
            d["stars"] += amount
        else:
            d["rub"] += amount if charge.startswith("pw:") else amount / 100       # PayAnyWay хранит рубли, Telegram присылает копейки
        d["count"] += 1
    recent = [{"ts": r[0], "uid": r[1], "amount": (r[2] if r[3] == "XTR" or r[5].startswith("pw") else r[2] / 100), "currency": r[3], "plan": r[4]}
              for r in _q("SELECT ts, uid, amount, currency, plan, charge_id FROM payments ORDER BY ts DESC LIMIT 50", all_=True)]

    def one(q, *a):
        return _q(q, a, one=True)[0]
    now = time.time()
    trials = one("SELECT COUNT(*) FROM subs WHERE trial_used=1")
    paid_after_trial = one("SELECT COUNT(DISTINCT p.uid) FROM payments p JOIN subs s ON s.uid=p.uid WHERE s.trial_used=1 AND p.currency NOT IN ('RUBTEST', 'RUBREFUND')")
    return {
        "months": sorted(by_month.values(), key=lambda d: d["month"], reverse=True),
        "recent": recent,
        "total_rub": sum(d["rub"] for d in by_month.values()),
        "total_stars": sum(d["stars"] for d in by_month.values()),
        "active": one("SELECT COUNT(*) FROM subs WHERE until > ?", now),
        "active_paid": one("SELECT COUNT(*) FROM subs WHERE until > ? AND source='payment'", now),
        "active_trial": one("SELECT COUNT(*) FROM subs WHERE until > ? AND source='trial'", now),
        "active_promo": one("SELECT COUNT(*) FROM subs WHERE until > ? AND source LIKE 'promo:%'", now),
        "trials_started": trials,
        "paid_users": one("SELECT COUNT(DISTINCT uid) FROM payments WHERE currency NOT IN ('RUBTEST', 'RUBREFUND')"),
        "trial_to_paid_pct": round(100 * paid_after_trial / trials, 1) if trials else 0,
        "expiring_7d": one("SELECT COUNT(*) FROM subs WHERE until > ? AND until < ?", now, now + 7 * 86400),
    }


def promo_stats() -> list[dict]:
    rows = _q("SELECT code, COUNT(*) FROM promo_used GROUP BY code ORDER BY 2 DESC", all_=True)
    return [{"code": r[0], "used": r[1]} for r in rows]


# ---------- данные профиля: только с согласием человека ----------
# Что хранится, если человек включил функции: список «не использовать», цель по калориям и БЖУ, вес и цель по весу.
# Диагнозы и аллергии не спрашиваем. Удалить всё можно в один шаг (delete_my_data).

CONSENT_VERSION = 1
CONSENT_TEXT = (
    "Для некоторых функций профиля нужны ваши данные. Ничего не включается само: вы выбираете функции галочками, и мы "
    "сохраняем только то, что вы введёте сами для включённых функций: список продуктов, которые вы не используете, «полку» "
    "(что уже есть дома), цель по калориям и БЖУ, дневник питания (что и сколько вы съели), вес и цель по весу. "
    "Они нужны только для работы этих функций, мы не показываем их другим людям и не передаём третьим лицам. "
    "Вы можете отключить любую функцию и удалить все данные одной кнопкой «Удалить мои данные». "
    "Диагнозы и сведения о здоровье вводить не нужно. Приложение не заменяет рекомендации врача, а список продуктов "
    "не гарантирует их отсутствие в готовом блюде: проверяйте состав."
)
PROFILE_FEATS = ("never", "macro", "weight", "shelf", "diary")
_RANGES = {"kcal": (800, 6000), "protein": (0, 600), "fat": (0, 600), "carb": (0, 1000)}


def consent_get(uid) -> bool:
    row = _q("SELECT version FROM consents WHERE uid=?", (str(uid),), one=True)
    return bool(row and row[0] >= CONSENT_VERSION)


def consent_set(uid) -> None:
    _q("INSERT INTO consents(uid, version, ts) VALUES(?,?,?) ON CONFLICT(uid) DO UPDATE SET version=excluded.version, ts=excluded.ts",
       (str(uid), CONSENT_VERSION, time.time()))


def goals_get(uid) -> dict:
    row = _q("SELECT feats, never, kcal, protein, fat, carb, weight_goal FROM goals WHERE uid=?", (str(uid),), one=True)
    if not row:
        return {"feats": [], "never": "", "kcal": None, "protein": None, "fat": None, "carb": None, "weight_goal": None}
    return {"feats": [f for f in (row[0] or "").split(",") if f in PROFILE_FEATS], "never": row[1] or "",
            "kcal": row[2], "protein": row[3], "fat": row[4], "carb": row[5], "weight_goal": row[6]}


def _num(value, lo, hi, as_float=False):
    if value in (None, ""):
        return None
    try:
        v = float(str(value).replace(",", "."))
    except ValueError:
        raise ValueError("Нужно число")
    if not lo <= v <= hi:
        raise ValueError(f"Допустимо от {lo} до {hi}")
    return round(v, 1) if as_float else int(round(v))


def goals_set(uid, **fields) -> dict:
    """Частичное сохранение: меняются только переданные поля. ValueError — если значение недопустимо."""
    cur = goals_get(uid)
    if "feats" in fields:
        cur["feats"] = [f for f in PROFILE_FEATS if f in (fields["feats"] or [])]
    if "never" in fields:
        cur["never"] = _clean_list(fields["never"] or "")
    for k, (lo, hi) in _RANGES.items():
        if k in fields:
            cur[k] = _num(fields[k], lo, hi)
    if "weight_goal" in fields:
        cur["weight_goal"] = _num(fields["weight_goal"], 20, 400, as_float=True)
    _q("INSERT INTO goals(uid, feats, never, kcal, protein, fat, carb, weight_goal, updated) VALUES(?,?,?,?,?,?,?,?,?) "
       "ON CONFLICT(uid) DO UPDATE SET feats=excluded.feats, never=excluded.never, kcal=excluded.kcal, protein=excluded.protein, "
       "fat=excluded.fat, carb=excluded.carb, weight_goal=excluded.weight_goal, updated=excluded.updated",
       (str(uid), ",".join(cur["feats"]), cur["never"], cur["kcal"], cur["protein"], cur["fat"], cur["carb"],
        cur["weight_goal"], time.time()))
    return cur


def goal_text(uid) -> str:
    """«2000 ккал, белки 120 г, жиры 70 г, углеводы 200 г» — готовый ответ для поля «Цель по КБЖУ»; "" если цели нет."""
    g = goals_get(uid)
    if not g["kcal"] and not (g["protein"] or g["fat"] or g["carb"]):
        return ""
    parts = [f"{g['kcal']} ккал"] if g["kcal"] else []
    for key, title in (("protein", "белки"), ("fat", "жиры"), ("carb", "углеводы")):
        if g[key]:
            parts.append(f"{title} {g[key]} г")
    return ", ".join(parts)


def weights_list(uid, limit: int = 400) -> list[dict]:
    rows = _q("SELECT day, kg FROM weights WHERE uid=? ORDER BY day DESC LIMIT ?", (str(uid), limit), all_=True)
    return [{"day": r[0], "kg": r[1]} for r in reversed(rows)]


def weight_set(uid, kg, day: str | None = None) -> dict:
    kg = _num(kg, 20, 400, as_float=True)
    if kg is None:
        raise ValueError("Нужно число")
    day = day or time.strftime("%Y-%m-%d")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        raise ValueError("Дата в формате ГГГГ-ММ-ДД")
    _q("INSERT INTO weights(uid, day, kg) VALUES(?,?,?) ON CONFLICT(uid, day) DO UPDATE SET kg=excluded.kg", (str(uid), day, kg))
    return {"day": day, "kg": kg}


def weight_delete(uid, day: str) -> None:
    _q("DELETE FROM weights WHERE uid=? AND day=?", (str(uid), day))


def delete_my_data(uid) -> dict:
    """Стереть всё, что человек сам ввёл в профиле: список «не использовать», цели, вес, полку, дневник и согласие."""
    uid = str(uid)
    out = {"weights": _q("DELETE FROM weights WHERE uid=?", (uid,)), "goals": _q("DELETE FROM goals WHERE uid=?", (uid,)),
           "shelf": _q("DELETE FROM shelf WHERE uid=?", (uid,)), "consent": _q("DELETE FROM consents WHERE uid=?", (uid,)),
           "diary": _q("DELETE FROM diary WHERE uid=?", (uid,)), "energy": _q("DELETE FROM energy WHERE uid=?", (uid,)),
           "book_seen": _q("DELETE FROM book_seen WHERE uid=?", (uid,)), "shop_lists": _q("DELETE FROM shop_lists WHERE uid=?", (uid,)),
           "macro_ref": _q("DELETE FROM macro_ref WHERE uid=?", (uid,)), "book_votes": _q("DELETE FROM book_votes WHERE uid=?", (uid,))}
    from stores import prefs
    prefs.forget(uid)
    return out


# ---------- дневник питания и расход энергии ----------
DIARY_NOTE = ("Калории оценочные: их считает справочник или ИИ, и они могут ошибаться. Готовность блюд (мясо, птица, рыба, "
              "яйца) проверяйте сами. Приложение не врач и не диетолог и не заменяет консультацию специалиста.")
ACTIVITY = {
    "sedentary": (1.2, "Сидячая работа, почти без тренировок"),
    "office_sport": (1.375, "Офис и спорт 1–3 раза в неделю"),
    "sport": (1.55, "Спорт 4–5 раз в неделю"),
    "active": (1.725, "Активная работа на ногах или ежедневный спорт"),
}
DIARY_DAILY_MAX = 60


def calc_tdee(sex: str, age, height_cm, weight_kg, activity: str) -> int:
    """Суточный расход по формуле Миффлина — Сан Жеора с обычными коэффициентами активности. ValueError — если данные неверны.
    Приблизительная оценка: реальный расход может отличаться на 10–20%."""
    if sex not in ("m", "f") or activity not in ACTIVITY:
        raise ValueError("Выберите пол и уровень активности")
    age, h, w = (_num(age, 14, 90), _num(height_cm, 120, 230), _num(weight_kg, 30, 250, as_float=True))
    if None in (age, h, w):
        raise ValueError("Нужны возраст, рост и вес")
    bmr = 10 * w + 6.25 * h - 5 * age + (5 if sex == "m" else -161)
    return int(round(bmr * ACTIVITY[activity][0] / 10.0) * 10)


def energy_set(uid, activity: str, tdee: int) -> None:
    _q("INSERT INTO energy(uid, activity, tdee, updated) VALUES(?,?,?,?) ON CONFLICT(uid) DO UPDATE SET activity=excluded.activity, "
       "tdee=excluded.tdee, updated=excluded.updated", (str(uid), activity, int(tdee), time.time()))


def energy_get(uid) -> dict | None:
    row = _q("SELECT activity, tdee FROM energy WHERE uid=?", (str(uid),), one=True)
    return {"activity": row[0], "tdee": row[1], "activity_title": ACTIVITY.get(row[0], (0, ""))[1]} if row else None


def diary_add(uid, title: str, kcal, protein=0, fat=0, carb=0, src: str = "manual", day: str | None = None) -> int:
    title = re.sub(r"\s+", " ", (title or "")).strip()[:80]
    if not title:
        raise ValueError("Напишите название")
    kcal = _num(kcal, 1, 5000)
    if kcal is None:
        raise ValueError("Нужно число калорий")
    day = day or time.strftime("%Y-%m-%d")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        raise ValueError("Дата в формате ГГГГ-ММ-ДД")
    with _lock:
        c = _conn()
        try:
            with c:
                n = c.execute("SELECT COUNT(*) FROM diary WHERE uid=? AND day=?", (str(uid), day)).fetchone()[0]
                if n >= DIARY_DAILY_MAX:
                    raise ValueError("За день уже много записей")
                cur = c.execute("INSERT INTO diary(uid, day, ts, title, kcal, protein, fat, carb, src) VALUES(?,?,?,?,?,?,?,?,?)",
                                (str(uid), day, time.time(), title, kcal, float(protein or 0), float(fat or 0), float(carb or 0), src))
                return cur.lastrowid
        finally:
            c.close()


def diary_day(uid, day: str | None = None) -> dict:
    day = day or time.strftime("%Y-%m-%d")
    rows = _q("SELECT id, title, kcal, protein, fat, carb, src FROM diary WHERE uid=? AND day=? ORDER BY id", (str(uid), day), all_=True)
    items = [{"id": r[0], "title": r[1], "kcal": r[2], "protein": r[3], "fat": r[4], "carb": r[5], "src": r[6]} for r in rows]
    return {"day": day, "items": items, "kcal": sum(i["kcal"] for i in items), "protein": round(sum(i["protein"] for i in items), 1),
            "fat": round(sum(i["fat"] for i in items), 1), "carb": round(sum(i["carb"] for i in items), 1)}


def diary_delete(uid, entry_id: int) -> bool:
    return _q("DELETE FROM diary WHERE uid=? AND id=?", (str(uid), int(entry_id))) > 0


def diary_series(uid, days: int = 14, end: str | None = None) -> list[dict]:
    """По одному элементу на каждый календарный день окна (дни без записей: n=0)."""
    import datetime
    last = datetime.date.fromisoformat(end) if end else datetime.date.today()
    first = last - datetime.timedelta(days=days - 1)
    rows = _q("SELECT day, SUM(kcal), SUM(protein), SUM(fat), SUM(carb), COUNT(*) FROM diary WHERE uid=? AND day>=? AND day<=? GROUP BY day",
              (str(uid), first.isoformat(), last.isoformat()), all_=True)
    by = {r[0]: r for r in rows}
    out = []
    for i in range(days):
        d = (first + datetime.timedelta(days=i)).isoformat()
        r = by.get(d)
        out.append({"day": d, "kcal": int(r[1]) if r else 0, "protein": round(r[2], 1) if r else 0, "fat": round(r[3], 1) if r else 0,
                    "carb": round(r[4], 1) if r else 0, "n": r[5] if r else 0})
    return out


def diary_frequent(uid, limit: int = 4) -> list[str]:
    """Названия продуктов из справочника, которые человек записывает чаще всего (за последние 90 дней)."""
    rows = _q("SELECT title FROM diary WHERE uid=? AND src='list' ORDER BY id DESC LIMIT 300", (str(uid),), all_=True)
    count: dict[str, int] = {}
    for (t,) in rows:
        name = t.split(" (")[0]
        count[name] = count.get(name, 0) + 1
    return [n for n, _ in sorted(count.items(), key=lambda kv: -kv[1])[:limit]]


def macro_ref_get(uid) -> dict | None:
    """Свой ориентир БЖУ в процентах калорий: человек вписывает его сам, приложение его не советует."""
    r = _q("SELECT p, f, c FROM macro_ref WHERE uid=?", (str(uid),), one=True)
    return {"protein": r[0], "fat": r[1], "carb": r[2]} if r else None


def macro_ref_set(uid, protein, fat, carb) -> dict | None:
    """Все поля пустые: убрать ориентир. ValueError — понятный текст. Значения не оцениваем, проверяем только, что это проценты."""
    if all(v in (None, "") for v in (protein, fat, carb)):
        _q("DELETE FROM macro_ref WHERE uid=?", (str(uid),))
        return None
    p, f, c = (_num(v, 0, 100, as_float=True) for v in (protein, fat, carb))
    if None in (p, f, c):
        raise ValueError("Нужны все три числа: белки, жиры и углеводы")
    if not 99 <= p + f + c <= 101:
        raise ValueError("Сумма процентов должна быть 100")
    _q("INSERT INTO macro_ref(uid, p, f, c) VALUES(?,?,?,?) ON CONFLICT(uid) DO UPDATE SET p=excluded.p, f=excluded.f, c=excluded.c", (str(uid), p, f, c))
    return {"protein": p, "fat": f, "carb": c}


def diary_yesterday(uid) -> list[dict]:
    """Что записано вчера: чтобы повторить привычный завтрак одним нажатием."""
    import datetime
    return diary_day(uid, (datetime.date.today() - datetime.timedelta(days=1)).isoformat())["items"]


def diary_copy(uid, entry_id: int) -> dict | None:
    """Записать такую же еду на сегодня. Только свою запись. -> запись или None."""
    r = _q("SELECT title, kcal, protein, fat, carb, src FROM diary WHERE uid=? AND id=?", (str(uid), int(entry_id)), one=True)
    if not r:
        return None
    diary_add(uid, r[0], r[1], r[2], r[3], r[4], r[5])
    return {"title": r[0], "kcal": r[1]}


def diary_stats(series: list[dict]) -> dict:
    """Среднее за день по дням, где есть записи (дни без записей не считаем «голодными»)."""
    logged = [d for d in series if d["n"]]
    if not logged:
        return {"days": len(series), "logged": 0, "avg_kcal": 0, "avg_protein": 0, "avg_fat": 0, "avg_carb": 0}
    n = len(logged)
    return {"days": len(series), "logged": n, "avg_kcal": int(round(sum(d["kcal"] for d in logged) / n)),
            "avg_protein": round(sum(d["protein"] for d in logged) / n), "avg_fat": round(sum(d["fat"] for d in logged) / n),
            "avg_carb": round(sum(d["carb"] for d in logged) / n)}


def trial_available(uid) -> bool:
    return not trial_used(uid)


def parse_goal(text: str) -> dict:
    """«2000 ккал, белки 120, жиры 70, углеводы 200» или «2000 120 70 200» или «1800» -> поля цели. «нет» -> всё очистить."""
    t = (text or "").lower().replace(",", " ")
    if re.fullmatch(r"\s*(нет|убрать|очистить)\s*", t):
        return {"kcal": None, "protein": None, "fat": None, "carb": None}
    out = {}
    for key, pat in (("kcal", r"(\d{3,4})\s*(?:ккал|kcal|кал)"), ("protein", r"белк\w*\s*(\d+)"),
                     ("fat", r"жир\w*\s*(\d+)"), ("carb", r"углев\w*\s*(\d+)")):
        m = re.search(pat, t)
        if m:
            out[key] = int(m.group(1))
    if not out:
        nums = [int(x) for x in re.findall(r"\d+", t)][:4]
        out = dict(zip(("kcal", "protein", "fat", "carb"), nums))
    if not out:
        raise ValueError("Не нашёл чисел. Напишите, например: 2000 ккал, белки 120, жиры 70, углеводы 200")
    return out


def spark(values: list[float]) -> str:
    """Мини-график из символов для Telegram: ▁▂▃▄▅▆▇█."""
    if not values:
        return ""
    lo, hi = min(values), max(values)
    if hi - lo < 1e-9:
        return "▄" * len(values)
    return "".join("▁▂▃▄▅▆▇█"[min(7, int((v - lo) / (hi - lo) * 7.999))] for v in values)


# ---------- публичные страницы рецептов (/r/<адрес>) ----------

def pub_recipe_add(slug: str, title: str, body: str, image: bytes | None) -> None:
    _q("INSERT OR REPLACE INTO pub_recipes(slug, ts, title, body, image) VALUES(?,?,?,?,?)",
       (slug, time.time(), (title or "")[:200], (body or "")[:15000], image))


def pub_recipe_get(slug: str) -> dict | None:
    row = _q("SELECT title, body, ts FROM pub_recipes WHERE slug=?", (slug,), one=True)
    return {"title": row[0], "body": row[1], "ts": row[2]} if row else None


def pub_recipe_image(slug: str) -> bytes | None:
    row = _q("SELECT image FROM pub_recipes WHERE slug=?", (slug,), one=True)
    return bytes(row[0]) if row and row[0] else None


# ---------- сообщения разработчику ----------
FEEDBACK_KINDS = {"idea": "Идея", "bug": "Ошибка", "q": "Вопрос"}
FEEDBACK_DAILY = 5


def feedback_add(uid, kind: str, text: str, contact: str = "", src: str = "web") -> int:
    """Сохранить сообщение. ValueError с понятным текстом — если пусто или человек отправил слишком много за сутки."""
    kind = kind if kind in FEEDBACK_KINDS else "idea"
    text = (text or "").strip()[:1500]
    if len(text) < 3:
        raise ValueError("Напишите хотя бы несколько слов")
    with _lock:
        c = _conn()
        try:
            with c:
                n = c.execute("SELECT COUNT(*) FROM feedback WHERE uid=? AND ts>?", (str(uid), time.time() - 86400)).fetchone()[0]
                if n >= FEEDBACK_DAILY:
                    raise ValueError("Сегодня вы уже отправили много сообщений. Напишите завтра")
                cur = c.execute("INSERT INTO feedback(ts, uid, kind, text, contact, src) VALUES(?,?,?,?,?,?)",
                                (time.time(), str(uid), kind, text, (contact or "").strip()[:100], src))
                return cur.lastrowid
        finally:
            c.close()


def feedback_list(limit: int = 200) -> list[dict]:
    rows = _q("SELECT id, ts, uid, kind, text, contact, src, done FROM feedback ORDER BY done ASC, id DESC LIMIT ?", (limit,), all_=True)
    return [{"id": r[0], "ts": r[1], "uid": r[2], "kind": r[3], "text": r[4], "contact": r[5], "src": r[6], "done": bool(r[7])} for r in rows]


def feedback_done(fid: int) -> None:
    _q("UPDATE feedback SET done=1 WHERE id=?", (int(fid),))


def account_stats() -> dict:
    def one(q):
        return _q(q, one=True)[0]
    return {
        "linked_devices": one("SELECT COUNT(*) FROM links"),
        "linked_accounts": one("SELECT COUNT(DISTINCT tg_id) FROM links"),
        "shelf_users": one("SELECT COUNT(DISTINCT uid) FROM shelf"),
        "favorites": one("SELECT COUNT(*) FROM favs"),
        "fav_users": one("SELECT COUNT(DISTINCT uid) FROM favs"),
    }
