"""
Книга рецептов: общая библиотека. Каждый новый рецепт, который составил бот, попадает сюда (с разделом и хештегами), поэтому книга
растёт каждый день. Когда человек просит рецепт, сначала ищем подходящий в книге (кухня, время, порции, техника, продукты,
исключения, цель по КБЖУ) и показываем тот, который он ещё не видел за последние BOOK_REPEAT_DAYS дней; если подходящего нет,
рецепт составляет ИИ и пополняет книгу. Так выходит быстрее и дешевле по токенам.

В книге нет данных о людях: ни кто просил рецепт, ни его профиль. Кому что показывали (book_seen) хранится отдельно и стирается кнопкой
«Удалить мои данные».
"""
import json
import logging
import random
import re
import time

import accounts
import cards
import config
import profile_ctx

logger = logging.getLogger(__name__)

MEALS = ["завтрак", "обед", "ужин", "перекус", "десерт"]
MEAL_TITLES = {"завтрак": "🌅 Завтраки", "обед": "🍲 Обеды", "ужин": "🍽 Ужины", "перекус": "🥨 Перекусы", "десерт": "🍰 Десерты"}
_WORDS = {
    "десерт": ("десерт", "торт", "пирог", "пирожн", "печень", "кекс", "маффин", "мусс", "пудинг", "мороженое", "шарлотк", "чизкейк", "брауни",
               "желе", "пончик", "сладк", "панна", "тирамису", "эклер", "рогалик", "булочк", "конфет", "варень", "крем "),
    "завтрак": ("каша", "каши", "овсян", "овсянк", "омлет", "яичниц", "сырник", "оладь", "блин", "гранола", "мюсли", "тост", "шакшук", "завтрак",
                "творожн", "драник", "вафл", "гречневая каша", "рисовая каша", "яйца"),
    "обед": ("суп", "борщ", "щи", "уха", "солянк", "бульон", "рассольник", "харчо", "лагман", "окрошк", "салат", "рагу"),
    "перекус": ("закуск", "бутерброд", "снэк", "хумус", "чипсы", "крекер", "батончик", "канапе", "тарталетк", "смузи", "коктейл", "ролл ", "роллы",
                "паштет", "энергетическ"),
}
_MEAT = ("мяс", "куриц", "курин", "говяд", "свинин", "баранин", "индейк", "фарш", "колбас", "бекон", "ветчин", "рыб", "лосос", "форел",
         "тунец", "креветк", "кальмар", "сёмг", "семг", "треск", "печень", "утк", "кролик", "сало", "сосиск", "окорок", "грудк", "филе")
_CUISINE_TAGS = {"ayurveda": "#аюрведа", "balanced": "#сбалансированное", "mass_gain": "#набормассы", "athlete_endurance": "#спорт",
                 "snack": "#перекус"}
_STOP = {"для", "или", "что", "как", "это", "очень", "хочу", "нужно", "мне", "надо", "без", "под", "при", "про"}


def _num(v):
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _minutes(time_text: str) -> int | None:
    """«30 мин», «1 ч 20 мин», «45–60 минут» -> минуты (для диапазона верхняя граница)."""
    t = (time_text or "").lower()
    h = re.search(r"(\d+)\s*(?:ч\b|час)", t)
    m = re.findall(r"(\d+)\s*(?:–|-|до)?\s*(\d+)?\s*мин", t)
    mins = None
    if m:
        a, b = m[0]
        mins = int(b or a)
    if h:
        return int(h.group(1)) * 60 + (mins or 0)
    return mins


def meta(title: str, text: str, cuisine: str = "") -> dict:
    """Раздел, хештеги, время и КБЖУ из самого рецепта (без запросов к ИИ)."""
    d = cards.parse(title, text)
    low = (d["title"] or "").lower()
    meal = "ужин"
    for name in ("десерт", "завтрак", "перекус", "обед"):
        if any(w in low for w in _WORDS[name]):
            meal = name
            break
    minutes = _minutes(d["time"])
    kcal, protein = _num(d["kcal"]), _num(d["protein"])
    tags = ["#" + meal]
    if cuisine in _CUISINE_TAGS and _CUISINE_TAGS[cuisine] not in tags:
        tags.append(_CUISINE_TAGS[cuisine])
    if minutes is not None and minutes <= 30:
        tags.append("#быстро")
    if kcal is not None and kcal <= 350:
        tags.append("#лёгкое")
    if kcal is not None and kcal >= 600:
        tags.append("#сытное")
    if protein is not None and protein >= 25:
        tags.append("#белок")
    if not any(w in (text or "").lower() for w in _MEAT):
        tags.append("#безмяса")
    return {"meal": meal, "tags": tags, "minutes": minutes, "kcal": int(kcal) if kcal else None, "protein": protein,
            "fat": _num(d["fat"]), "carb": _num(d["carb"]), "servings": int(d["servings"]) if d["servings"] else None}


def _norm(title: str) -> str:
    return re.sub(r"[^а-яa-z0-9]+", " ", (title or "").lower().replace("ё", "е")).strip()


def valid(title: str, text: str) -> bool:
    """В книгу берём только цельные рецепты: с названием, калориями и составом, без ссылок и предупреждений ИИ."""
    t = text or ""
    if not title or not 300 <= len(t) <= 8000:
        return False
    low = t.lower()
    if t.lstrip().startswith(("😔", "⚠️")) or any(x in low for x in ("http", "www.", "t.me", "@", "ignore previous")):
        return False
    if "ккал" not in low or not re.search(r"(продукты|ингредиенты)[^\n]{0,4}\n\s*[-•*]", low):
        return False
    return cards.parse(title, t)["kcal"] is not None


def add(data: dict, title: str, text: str, terms=None) -> int | None:
    """Положить новый рецепт в книгу. -> id или None (не подошёл или уже есть такой). Сбой записи книге не страшен."""
    try:
        title = (title or "").strip()
        if not valid(title, text):
            return None
        m = meta(title, text, data.get("cuisine") or "")
        servings = int(data.get("servings") or m["servings"] or 2)
        key = f"{_norm(title)}|{data.get('cuisine') or ''}|{servings}"
        appliance = ",".join(sorted(data.get("appliance") or []))
        with accounts._lock:
            c = accounts._conn()
            try:
                with c:
                    cur = c.execute(
                        "INSERT OR IGNORE INTO book(ts, title, key, text, terms, cuisine, meal, minutes, kcal, protein, fat, carb, servings, portion,"
                        " appliance, tags, text_l) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (time.time(), title[:200], key, text, json.dumps(list(terms or []), ensure_ascii=False), data.get("cuisine") or "",
                         m["meal"], m["minutes"], m["kcal"], m["protein"], m["fat"], m["carb"], servings, data.get("portion") or "normal",
                         appliance, " ".join(m["tags"]), text.lower().replace("ё", "е")))
                    return cur.lastrowid if cur.rowcount else None
            finally:
                c.close()
    except Exception:  # noqa: BLE001
        logger.exception("Рецепт не попал в книгу")
        return None


def _stem(w: str) -> str:
    w = w.lower().replace("ё", "е")
    return w if len(w) <= 4 else w[:max(4, len(w) - 2)]


def _wanted(preferred: str) -> list[str]:
    parts = re.split(r"[,;\n]+|\s+и\s+", preferred or "")
    out = []
    for p in parts:
        words = [w for w in re.findall(r"[а-яёa-z]{3,}", p.lower()) if w not in _STOP]
        out += [_stem(w) for w in words]
    return out


def _goal_ok(goal_text: str, row) -> bool | None:
    """Подходит ли рецепт под цель по КБЖУ. None — цель не разобрать (тогда берём ИИ)."""
    try:
        g = accounts.parse_goal(goal_text)
    except ValueError:
        return None
    g = {k: v for k, v in g.items() if v}
    if not g:
        return None
    for key, tol, val in (("kcal", 0.10, row["kcal"]), ("protein", 0.20, row["protein"]), ("fat", 0.20, row["fat"]), ("carb", 0.20, row["carb"])):
        if g.get(key) and (val is None or abs(val - g[key]) > g[key] * tol):
            return False
    return True


COLS = "id, title, text, terms, cuisine, meal, minutes, kcal, protein, fat, carb, servings, portion, appliance, tags, uses"


def _row(r) -> dict:
    keys = [c.strip() for c in COLS.split(",")]
    d = dict(zip(keys, r))
    d["terms"] = json.loads(d["terms"] or "[]")
    d["tags"] = (d["tags"] or "").split()
    return d


def seen_days() -> int:
    return int(getattr(config, "BOOK_REPEAT_DAYS", 14))


def find(uid, data: dict) -> dict | None:
    """Подходящий рецепт из книги, который этот человек ещё не видел за последние BOOK_REPEAT_DAYS дней. Нашли — отмечаем показ."""
    try:
        cuisine, servings = data.get("cuisine") or "", int(data.get("servings") or 2)
        rows = accounts._q(f"SELECT {COLS} FROM book WHERE cuisine=? AND servings=? AND portion=? AND id NOT IN "
                           "(SELECT book_id FROM book_seen WHERE uid=? AND ts > ?) ORDER BY uses, RANDOM() LIMIT 80",
                           (cuisine, servings, data.get("portion") or "normal", str(uid), time.time() - seen_days() * 86400), all_=True)
        limit = _minutes(str(data.get("time") or ""))
        have = set(data.get("appliance") or [])
        want = _wanted(data.get("preferred") or "")
        profile_ctx.set_user(uid)
        profile_ctx.set_avoid(data.get("excluded"))
        goal = (data.get("macro_goal") or "").strip()
        ok = []
        for r in rows:
            d = _row(r)
            if limit and (d["minutes"] is None or d["minutes"] > limit):
                continue
            ap = {a for a in (d["appliance"] or "").split(",") if a}
            if have and not ap <= have:
                continue
            low = d["text"].lower().replace("ё", "е")
            if want and not all(w in low for w in want):
                continue
            if profile_ctx.hits(d["text"]):
                continue
            if goal:
                g = _goal_ok(goal, d)
                if not g:
                    continue
            ok.append(d)
            if len(ok) >= 12:
                break
        if not ok:
            return None
        pick = random.choice(ok)
        mark_seen(uid, pick["id"])
        accounts._q("UPDATE book SET uses=uses+1 WHERE id=?", (pick["id"],))
        return pick
    except Exception:  # noqa: BLE001 — если книга недоступна, рецепт просто составит ИИ
        logger.exception("Поиск в книге не удался")
        return None


def mark_seen(uid, book_id: int) -> None:
    accounts._q("INSERT INTO book_seen(uid, book_id, ts) VALUES(?,?,?) ON CONFLICT(uid, book_id) DO UPDATE SET ts=excluded.ts",
                (str(uid), int(book_id), time.time()))


def get(book_id) -> dict | None:
    try:
        r = accounts._q(f"SELECT {COLS} FROM book WHERE id=?", (int(book_id),), one=True)
    except (TypeError, ValueError):
        return None
    return _row(r) if r else None


def total() -> int:
    return accounts._q("SELECT COUNT(*) FROM book", one=True)[0]


def added_today() -> int:
    return accounts._q("SELECT COUNT(*) FROM book WHERE ts > ?", (time.time() - 86400,), one=True)[0]


def counts_by_meal() -> dict[str, int]:
    rows = accounts._q("SELECT meal, COUNT(*) FROM book GROUP BY meal", all_=True)
    out = {m: 0 for m in MEALS}
    out.update({m: n for m, n in rows})
    return out


def top_tags(limit: int = 16) -> list[tuple[str, int]]:
    count: dict[str, int] = {}
    for (t,) in accounts._q("SELECT tags FROM book", all_=True):
        for tag in (t or "").split():
            count[tag] = count.get(tag, 0) + 1
    return sorted(count.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]


# основной ингредиент: название на кнопке -> корни слов для поиска по составу
MAINS = {
    "говядина": ("говяд", "телятин"), "свинина": ("свинин",), "курица": ("куриц", "курин", "индейк"), "фарш": ("фарш",),
    "рыба": ("рыб", "лосос", "форел", "тунец", "треск", "семг", "сёмг", "минтай", "горбуш"), "морепродукты": ("креветк", "кальмар", "мидии"),
    "овощи": ("кабачк", "баклажан", "капуст", "морков", "тыкв", "брокколи", "цветная"), "яйца": ("яйц", "яйца"),
    "творог": ("творог", "сырник"), "крупы": ("греч", "рис", "овсян", "булгур", "пшен", "кускус", "киноа", "перлов"),
    "макароны": ("макарон", "паста", "спагетти", "лапш"), "бобовые": ("фасол", "нут", "чечевиц", "горох"),
}
APPLIANCES = {"stove": "Плита", "oven": "Духовка", "multicooker": "Мультиварка", "airfryer": "Аэрогриль"}


def search(meal: str | None = None, tag: str | None = None, q: str | None = None, max_min: int | None = None, max_kcal: int | None = None,
           appliance: str | None = None, servings: int | None = None, main: str | None = None,
           limit: int = 8, offset: int = 0) -> tuple[list[dict], int]:
    """Список для просмотра: раздел, хештег, слова, время, калорийность, на чём готовить, на сколько человек, основной ингредиент.
    Смотреть книгу можно без лимитов: ИИ не вызывается. -> (страница, всего)."""
    where, args = ["1=1"], []
    if meal in MEALS:
        where.append("meal=?")
        args.append(meal)
    if tag:
        where.append("(' ' || tags || ' ') LIKE ?")
        args.append(f"% {tag[:30]} %")
    for w in re.findall(r"[а-яёa-z0-9]{3,}", (q or "").lower())[:5]:
        where.append("text_l LIKE ?")
        args.append(f"%{_stem(w)}%")
    if max_min:
        where.append("minutes IS NOT NULL AND minutes <= ?")
        args.append(int(max_min))
    if max_kcal:
        where.append("kcal IS NOT NULL AND kcal <= ?")
        args.append(int(max_kcal))
    if appliance in APPLIANCES:
        where.append("(',' || appliance || ',') LIKE ?")
        args.append(f"%,{appliance},%")
    if servings:
        where.append("servings=?")
        args.append(int(servings))
    if main in MAINS:
        where.append("(" + " OR ".join("text_l LIKE ?" for _ in MAINS[main]) + ")")
        args += [f"%{root}%" for root in MAINS[main]]
    cond = " AND ".join(where)
    n = accounts._q(f"SELECT COUNT(*) FROM book WHERE {cond}", args, one=True)[0]
    rows = accounts._q(f"SELECT {COLS} FROM book WHERE {cond} ORDER BY id DESC LIMIT ? OFFSET ?", args + [int(limit), int(offset)], all_=True)
    return [_row(r) for r in rows], n
