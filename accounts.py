"""
Личный кабинет: один аккаунт = id в Telegram. Общий SQLite для бота и сайта (два процесса, режим WAL).

- профиль: аллергии и продукты, которые никогда не использовать (подставляются в каждую генерацию);
- «полка»: долгоживущие продукты, которые человек купил или отметил «есть дома»;
- привязка устройства сайта к Telegram (одноразовый код, сайт → бот);
- подписка: до какого времени действует, платежи (без повторного зачисления), промокоды, откуда пришёл (ref);
- короткие сессии бота (режим готовки по шагам, список покупок с галочками) — переживают перезапуск.

uid аккаунта: str(tg_id) для Telegram и привязанного сайта, "w<id устройства>" для сайта без привязки.
"""
import json
import os
import secrets
import sqlite3
import threading
import time

DB_PATH = os.getenv("ACCOUNTS_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "accounts.db"))
_lock = threading.Lock()
_ready = False

SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles(uid TEXT PRIMARY KEY, allergies TEXT DEFAULT '', never TEXT DEFAULT '', updated REAL);
CREATE TABLE IF NOT EXISTS shelf(uid TEXT, item TEXT, ts REAL, PRIMARY KEY(uid, item));
CREATE TABLE IF NOT EXISTS links(dev_id INTEGER PRIMARY KEY, tg_id INTEGER, ts REAL);
CREATE INDEX IF NOT EXISTS links_tg ON links(tg_id);
CREATE TABLE IF NOT EXISTS link_codes(code TEXT PRIMARY KEY, dev_id INTEGER, created REAL, tg_id INTEGER);
CREATE TABLE IF NOT EXISTS subs(uid TEXT PRIMARY KEY, until REAL, source TEXT, trial_used INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS payments(charge_id TEXT PRIMARY KEY, uid TEXT, amount INTEGER, currency TEXT,
  days INTEGER, plan TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS promo_used(uid TEXT, code TEXT, ts REAL, PRIMARY KEY(uid, code));
CREATE TABLE IF NOT EXISTS refs(uid TEXT PRIMARY KEY, ref TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS sess(key TEXT PRIMARY KEY, data TEXT, ts REAL);
CREATE INDEX IF NOT EXISTS sess_ts ON sess(ts);
CREATE TABLE IF NOT EXISTS favs(uid TEXT, id TEXT, title TEXT, text TEXT, cuisine TEXT, ts REAL, PRIMARY KEY(uid, id));
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT, ts REAL, who TEXT);
CREATE TABLE IF NOT EXISTS admin_log(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, who TEXT, action TEXT, details TEXT);
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


# ---------- профиль ----------

def get_profile(uid) -> dict:
    row = _q("SELECT allergies, never FROM profiles WHERE uid=?", (str(uid),), one=True)
    return {"allergies": row[0] or "", "never": row[1] or ""} if row else {"allergies": "", "never": ""}


def set_profile(uid, allergies: str | None = None, never: str | None = None) -> dict:
    cur = get_profile(uid)
    if allergies is not None:
        cur["allergies"] = _clean_list(allergies)
    if never is not None:
        cur["never"] = _clean_list(never)
    _q("INSERT INTO profiles(uid, allergies, never, updated) VALUES(?,?,?,?) ON CONFLICT(uid) DO UPDATE SET "
       "allergies=excluded.allergies, never=excluded.never, updated=excluded.updated",
       (str(uid), cur["allergies"], cur["never"], time.time()))
    return cur


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
                c.execute("INSERT INTO links(dev_id, tg_id, ts) VALUES(?,?,?) ON CONFLICT(dev_id) DO UPDATE SET "
                          "tg_id=excluded.tg_id, ts=excluded.ts", (dev_id, tg_id, time.time()))
                _merge_device(c, dev_id, tg_id)
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
                c.execute("INSERT INTO links(dev_id, tg_id, ts) VALUES(?,?,?) ON CONFLICT(dev_id) DO UPDATE SET "
                          "tg_id=excluded.tg_id, ts=excluded.ts", (dev_id, tg_id, time.time()))
                _merge_device(c, dev_id, tg_id)
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
                row = c.execute("SELECT until FROM subs WHERE uid=?", (str(uid),)).fetchone()
                start = max(time.time(), (row[0] or 0) if row else 0)
                until = start + days * 86400
                c.execute("INSERT INTO subs(uid, until, source) VALUES(?,?,?) ON CONFLICT(uid) DO UPDATE SET "
                          "until=excluded.until, source=excluded.source", (str(uid), until, source))
                return until
        finally:
            c.close()


def trial_used(uid) -> bool:
    row = _q("SELECT trial_used FROM subs WHERE uid=?", (str(uid),), one=True)
    return bool(row and row[0])


def start_trial(uid, days: int) -> float | None:
    if days <= 0 or trial_used(uid):
        return None
    until = grant_days(uid, days, "trial")
    _q("UPDATE subs SET trial_used=1 WHERE uid=?", (str(uid),))
    return until


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


def use_promo(uid, code: str) -> bool:
    try:
        _q("INSERT INTO promo_used(uid, code, ts) VALUES(?,?,?)", (str(uid), code.upper(), time.time()))
        return True
    except sqlite3.IntegrityError:
        return False


def set_ref(uid, ref: str) -> None:
    """Откуда пришёл человек (t.me/бот?start=ref_блогер). Запоминаем только первый источник."""
    ref = "".join(ch for ch in ref if ch.isalnum() or ch in "_-")[:32]
    if ref:
        _q("INSERT OR IGNORE INTO refs(uid, ref, ts) VALUES(?,?,?)", (str(uid), ref, time.time()))


def ref_stats() -> list[dict]:
    """Для админки: сколько людей пришло по каждой ссылке и сколько из них оплатили."""
    rows = _q("SELECT r.ref, COUNT(*), COUNT(DISTINCT p.uid), COALESCE(SUM(p.amount), 0) FROM refs r "
              "LEFT JOIN payments p ON p.uid = r.uid GROUP BY r.ref ORDER BY 2 DESC", all_=True)
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


def _merge_device(c, dev_id: int, tg_id: int) -> None:
    """Устройство привязали к Telegram: его избранное (сделанное без входа) переходит в аккаунт."""
    c.execute("UPDATE OR IGNORE favs SET uid=? WHERE uid=?", (str(tg_id), f"w{dev_id}"))


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
    for ts, amount, cur in _q("SELECT ts, amount, currency FROM payments", all_=True):
        m = time.strftime("%Y-%m", time.localtime(ts))
        d = by_month.setdefault(m, {"month": m, "rub": 0, "stars": 0, "count": 0})
        if cur == "XTR":
            d["stars"] += amount
        else:
            d["rub"] += amount / 100       # Telegram присылает рубли в копейках
        d["count"] += 1
    recent = [{"ts": r[0], "uid": r[1], "amount": (r[2] if r[3] == "XTR" else r[2] / 100), "currency": r[3], "plan": r[4]}
              for r in _q("SELECT ts, uid, amount, currency, plan FROM payments ORDER BY ts DESC LIMIT 50", all_=True)]

    def one(q, *a):
        return _q(q, a, one=True)[0]
    now = time.time()
    trials = one("SELECT COUNT(*) FROM subs WHERE trial_used=1")
    paid_after_trial = one("SELECT COUNT(DISTINCT p.uid) FROM payments p JOIN subs s ON s.uid=p.uid WHERE s.trial_used=1")
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
        "paid_users": one("SELECT COUNT(DISTINCT uid) FROM payments"),
        "trial_to_paid_pct": round(100 * paid_after_trial / trials, 1) if trials else 0,
        "expiring_7d": one("SELECT COUNT(*) FROM subs WHERE until > ? AND until < ?", now, now + 7 * 86400),
    }


def promo_stats() -> list[dict]:
    rows = _q("SELECT code, COUNT(*) FROM promo_used GROUP BY code ORDER BY 2 DESC", all_=True)
    return [{"code": r[0], "used": r[1]} for r in rows]


def account_stats() -> dict:
    def one(q):
        return _q(q, one=True)[0]
    return {
        "linked_devices": one("SELECT COUNT(*) FROM links"),
        "linked_accounts": one("SELECT COUNT(DISTINCT tg_id) FROM links"),
        "profiles": one("SELECT COUNT(*) FROM profiles WHERE allergies != '' OR never != ''"),
        "shelf_users": one("SELECT COUNT(DISTINCT uid) FROM shelf"),
        "favorites": one("SELECT COUNT(*) FROM favs"),
        "fav_users": one("SELECT COUNT(DISTINCT uid) FROM favs"),
    }
