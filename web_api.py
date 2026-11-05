"""
Автономная веб-версия кухонного помощника (не зависит от Telegram-бота как процесса).
Запуск:  uvicorn web_api:app --host 127.0.0.1 --port 8081
Отдаёт и страницу (/), и API (/api/*), и админку (/admin). Использует то же ядро, что бот:
ai.py, search.py, prompts.py, day_menu.py, week_menu.py, favorites.py, shopping.py, stores/.

Пользователь = устройство: при первом визите браузер получает случайный код и хранит его у себя
(без регистрации). Меню и планы веба лежат отдельно от бота (папка web_data/). После «Войти через Telegram»
устройство привязано к аккаунту (accounts.py): лимиты, подписка, полка и избранное — общие с ботом.
"""
import asyncio
import contextvars
import functools
import httpx
from collections import deque
from html import escape
import hashlib
import hmac
import json
import random
import re
import secrets
import time
import uuid
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Header, HTTPException, Request, Response, UploadFile, File, Form as FForm
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import config
import web_db
import web_pdf
import seo_pages
import missing as missing_mod
import day_menu
import week_menu
import favorites as fav_mod

# Данные веба — в отдельные файлы (до первого использования модулей)
day_menu.DAY_MENU_FILE = web_db.DATA_DIR / "day_menu.json"
week_menu.WEEK_MENU_FILE = web_db.DATA_DIR / "week_menu.json"
fav_mod.FAVORITES_FILE = web_db.DATA_DIR / "favorites.json"

from ai import generate_recipe
import usage
import accounts
import book
import cards
import charts
import food_ai
import foods
import payanyway
import payinfo
import plans
import profile_ctx
import shoplists
import vision
from cuisines import CUISINE_LABELS, PORTION_LABELS, PORTION_ORDER
from keyboards import MEAL_LABELS, sort_meals, CUISINE_BUTTONS
from search import search_recipes, format_results_for_prompt
from shopping import extract_shopping_terms, extract_dish_title, amounts_from_recipe_text, item_amounts, _fmt_amount, _find_amount
from storage import get_all_users
from stores.links import STORES, DEFAULT_STORES, search_url, short_name
from stores.basket import build_priced_basket

BASE = Path(__file__).parent
app = FastAPI(title="Kitchen", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(GZipMiddleware, minimum_size=1000)   # HTML/JS сайта в 3-4 раза меньше по сети
app.mount("/uploads", StaticFiles(directory=web_db.DATA_DIR / "uploads"), name="uploads")

APPLIANCES = {"airfryer": "Аэрогриль", "multicooker": "Мультиварка", "oven": "Духовка", "stove": "Плита"}


# ---------------- вспомогательное ----------------

def _dev(x_device: str | None) -> int:
    dev_id = web_db.device_id((x_device or "").strip())
    if dev_id is None:
        raise HTTPException(401, "device")
    tg = accounts.tg_of_device(dev_id)
    if tg and tg < accounts.VK_OFFSET:
        usage.touch(tg)          # заходит на сайт — напоминания в Telegram не нужны (аккаунтам ВК напоминать в Telegram нечем)
    return dev_id


def _uid(dev_id: int) -> str:
    return accounts.web_uid(dev_id)


def _mark_home(dev_id: int, items: list[dict]) -> list[dict]:
    """Продукты, которые уже на полке аккаунта, помечаем home=True (на сайте они идут отдельным списком)."""
    uid = _uid(dev_id)
    if items and not plans.is_anon(uid) and plans.shelf_on(uid):
        for i in accounts.shelf_match(uid, [it.get("name", "") for it in items]):
            items[i]["home"] = True
    return items


def _cid(dev_id: int) -> int:
    return web_db.chat_id_of(dev_id)


def parse_shopping(md: str) -> list[dict]:
    """Из Markdown-сообщения со ссылками (как в боте) делает структуру для веба."""
    items = []
    for line in (md or "").splitlines():
        if line.startswith("• ") and " — " in line:
            name, _, rest = line[2:].partition(" — ")
            links = [{"store": s, "url": u} for s, u in re.findall(r"\[([^\]]+)\]\(([^)]+)\)", rest)]
            items.append({"name": name.strip(), "links": links})
    return items


def with_amounts(items: list[dict], amounts: dict[str, int], text: str = "") -> list[dict]:
    """Дописывает к продуктам, сколько их нужно купить (из рецепта/меню). text — рецепт: для «2 ст. л.» и «по вкусу»."""
    for it, am in zip(items, item_amounts([i["name"] for i in items], amounts, text)):
        if am:
            it["amount"] = am
    return items


def links_for(terms: list[str]) -> list[dict]:
    return [{"name": t, "links": [{"store": short_name(s), "url": search_url(s, t)} for s in DEFAULT_STORES]}
            for t in terms]


class Params(BaseModel):
    cuisine: str = "classic"
    macro_goal: str = Field("", max_length=200)
    preferred: str = Field("", max_length=300)
    excluded: str = Field("", max_length=300)
    time: str = Field("Не важно", max_length=20)
    servings: int = Field(2, ge=1, le=6)
    portion: str = Field("normal", pattern="^(small|normal|large)$")
    appliance: list[str] = []
    meals: list[str] = []

    def to_data(self) -> dict:
        if self.cuisine not in CUISINE_LABELS:
            raise HTTPException(400, "Неизвестный тип питания")
        keys = [a for a in self.appliance if a in APPLIANCES]
        if not keys:
            raise HTTPException(400, "Выбери, в чём готовить")
        return {"cuisine": self.cuisine, "macro_goal": self.macro_goal.strip(), "preferred": self.preferred.strip(),
                "excluded": self.excluded.strip(), "time": self.time, "servings": self.servings, "portion": self.portion,
                "appliance": keys, "appliance_labels": [APPLIANCES[k] for k in keys]}

    def meal_keys(self) -> list[str]:
        keys = sort_meals(self.meals)
        if not keys:
            raise HTTPException(400, "Выбери хотя бы один приём пищи")
        return keys


# ---------------- фоновые задачи (генерация долгая) ----------------

JOBS: dict[str, dict] = {}


MSK = 3 * 3600   # Москва = UTC+3 без перехода на летнее время


def _msk_midnight(days_back: int = 0) -> float:
    now = time.time() + MSK
    return now - (now % 86400) - days_back * 86400 - MSK


def _spend(dev_id: int, kind: str) -> dict:
    """Списать подбор с аккаунта (usage.try_spend: тариф, дневные потолки, лимит на IP). Отказ — 429 с понятным текстом."""
    # повторный запрос, пока первый ещё готовится, отклоняем ДО списания — иначе дубль съест попытку
    if any(j["dev"] == dev_id and j["status"] == "running" for j in JOBS.values()):
        raise HTTPException(429, "Предыдущий запрос ещё выполняется — подожди минутку.")
    uid = _uid(dev_id)
    spent, why = usage.try_spend(uid, kind, "web", ip=_ip_var.get())
    if not spent:
        raise HTTPException(429, why)
    profile_ctx.set_user(uid)     # список «никогда не использовать» из профиля (задача копирует контекст)
    web_db.log_event(dev_id, "gen")
    web_db.log_event(dev_id, kind)
    return {"id": spent, "kind": kind, "notice": why}


def _refund(dev_id: int, spend: dict):
    """Подбор не получился (сбой ИИ) — возвращаем попытку."""
    web_db.drop_last_event(dev_id, spend["kind"])
    web_db.drop_last_event(dev_id, "gen")
    usage.unlog(spend["id"])


def _start_job(dev_id: int, coro_fn, spend: dict | None = None) -> dict:
    # чистим старые задачи
    for k in [k for k, v in JOBS.items() if time.time() - v["ts"] > 3600]:
        JOBS.pop(k, None)
    if any(j["dev"] == dev_id and j["status"] == "running" for j in JOBS.values()):
        raise HTTPException(429, "Предыдущий запрос ещё выполняется — подожди минутку.")
    job_id = uuid.uuid4().hex
    JOBS[job_id] = {"dev": dev_id, "status": "running", "ts": time.time()}

    async def runner():
        try:
            JOBS[job_id]["result"] = await coro_fn()
            JOBS[job_id]["status"] = "done"
        except Exception as e:  # noqa: BLE001
            JOBS[job_id]["status"] = "error"
            JOBS[job_id]["error"] = str(e)[:300]
            if spend:
                _refund(dev_id, spend)

    asyncio.create_task(runner())
    return {"job": job_id, "notice": (spend or {}).get("notice", "")}


@app.get("/api/job/{job_id}")
def job(job_id: str, x_device: str | None = Header(default=None)):
    dev_id = _dev(x_device)
    j = JOBS.get(job_id)
    if not j or j["dev"] != dev_id:
        raise HTTPException(404, "Задача не найдена")
    return {k: v for k, v in j.items() if k in ("status", "result", "error")}


# ---------------- устройства и статистика ----------------

_new_dev_log: dict[str, list[float]] = {}


def _prune(log: dict[str, list[float]], window: float) -> None:
    """Счётчики попыток по IP: удаляем устаревшие, чтобы словарь не рос бесконечно."""
    if len(log) > 1000:
        now = time.time()
        for k in [k for k, v in log.items() if not v or now - v[-1] > window]:
            log.pop(k, None)


_ip_var: contextvars.ContextVar = contextvars.ContextVar("client_ip", default=None)
_rate: dict[str, deque] = {}
API_RATE_PER_MIN = 600     # запросов к /api в минуту с одного IP (за одним IP мобильного оператора — много людей)


def _client_ip(request: Request) -> str:
    return request.headers.get("x-real-ip") or (request.client.host if request.client else "?")


@app.middleware("http")
async def _ip_and_rate(request: Request, call_next):
    ip = _client_ip(request)
    _ip_var.set(ip)
    if request.url.path.startswith("/api/") and not request.url.path.startswith("/api/job/"):   # опрос задачи — не в счёт
        now = time.time()
        q = _rate.setdefault(ip, deque())
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= API_RATE_PER_MIN:
            return JSONResponse({"detail": "Слишком много запросов — подожди минутку."}, status_code=429)
        q.append(now)
        if len(_rate) > 5000:
            for key in [k for k, v in _rate.items() if not v or now - v[-1] > 60]:
                _rate.pop(key, None)
    return await call_next(request)


@app.post("/api/device")
def new_device(request: Request):
    ip = _client_ip(request)
    hist = [t for t in _new_dev_log.get(ip, []) if time.time() - t < 3600]
    if len(hist) >= 20:
        raise HTTPException(429, "Слишком много новых устройств с одного адреса")
    hist.append(time.time())
    _new_dev_log[ip] = hist
    _prune(_new_dev_log, 3600)
    return {"device": web_db.create_device()}


_stat_log: dict[str, deque] = {}
STAT_PER_HOUR = 60     # счётчики статистики (визит, «нажал на закрытое») с одного IP в час: чтобы нельзя было раздуть базу


def _stat_ok(ip: str) -> bool:
    now = time.time()
    q = _stat_log.setdefault(ip, deque())
    while q and now - q[0] > 3600:
        q.popleft()
    if len(q) >= STAT_PER_HOUR:
        return False
    q.append(now)
    if len(_stat_log) > 5000:
        for k in [k for k, v in _stat_log.items() if not v or now - v[-1] > 3600]:
            _stat_log.pop(k, None)
    return True


@app.post("/api/visit")
def visit(request: Request, x_device: str | None = Header(default=None)):
    dev = _dev(x_device)
    if _stat_ok(_client_ip(request)):
        web_db.log_event(dev, "visit")
    return {"ok": True}


@app.get("/api/meta")
def meta():
    return {
        "cuisines": [{"key": k, "label": v} for k, v in CUISINE_BUTTONS.items()],   # порядок и подписи как в боте
        "appliances": [{"key": k, "label": v} for k, v in APPLIANCES.items()],
        "meals": [{"key": k, "label": v} for k, v in MEAL_LABELS.items()],
        "portions": [{"key": k, "label": PORTION_LABELS[k]} for k in PORTION_ORDER],
        "week_days": config.WEEK_MENU_DAYS,
        "limit": config.WEB_DAILY_LIMIT,
        "limits": {"recipe": config.WEB_LIMIT_RECIPE, "daymenu": config.WEB_LIMIT_DAYMENU, "week": config.WEB_LIMIT_WEEK},
        "paywall": plans.paywall(),
        "bot": config.BOT_USERNAME,
    }


# ---------------- генерация ----------------

@app.post("/api/recipe")
async def api_recipe(p: Params, x_device: str | None = Header(default=None)):
    dev_id = _dev(x_device)
    data = p.to_data()
    sp = _spend(dev_id, "recipe")

    async def work():
        hit = book.find(_uid(dev_id), data)
        bid = None
        if hit:
            text, terms, bid = hit["text"], hit["terms"], hit["id"]
        else:
            query, results = await search_recipes(data)
            text = await generate_recipe(data, query, format_results_for_prompt(results))
            if text.startswith("😔"):
                raise RuntimeError("ИИ сейчас недоступен, попробуй позже")
            text, terms = extract_shopping_terms(text)
            if not text.strip():
                raise RuntimeError("Не получилось составить рецепт под эти параметры")
            bid = book.add(data, extract_dish_title(text) or "Рецепт", text, terms)       # новый рецепт пополняет книгу
        return {"title": extract_dish_title(text) or "Рецепт", "text": text, "card": f"/api/bookimg/{bid}.png" if bid else None,
                "shopping": _mark_home(dev_id, with_amounts(links_for(terms), amounts_from_recipe_text(text), text))}

    return _start_job(dev_id, work, sp)


# ---------------- книга рецептов ----------------

BOOK_PAGE = 8


@app.get("/api/book")
def api_book(meal: str = "", tag: str = "", q: str = "", max_min: int = 0, max_kcal: int = 0, appliance: str = "", servings: int = 0,
             main: str = "", page: int = 0, x_device: str | None = Header(default=None)):
    """Листать общую книгу рецептов: разделы, хештеги, слово, время, калории, техника, порции, основной ингредиент. Без лимитов (ИИ не вызывается)."""
    _dev(x_device)
    page = max(0, min(page, 500))
    rows, total = book.search(meal=meal or None, tag=tag[:30] or None, q=q[:40] or None, max_min=max_min if 0 < max_min <= 600 else None,
                              max_kcal=max_kcal if 0 < max_kcal <= 5000 else None, appliance=appliance or None,
                              servings=servings if 1 <= servings <= 6 else None, main=main or None, limit=BOOK_PAGE, offset=page * BOOK_PAGE)
    counts = book.counts_by_meal()
    return {"items": [{"id": r["id"], "title": r["title"], "text": r["text"], "tags": r["tags"], "kcal": r["kcal"], "minutes": r["minutes"],
                       "meal": r["meal"], "servings": r["servings"], "card": f"/api/bookimg/{r['id']}.png"} for r in rows],
            "total": total, "pages": max(1, -(-total // BOOK_PAGE)), "page": page,
            "meta": {"total": book.total(), "today": book.added_today(), "meals": [{"key": m, "title": book.MEAL_TITLES[m], "count": counts.get(m, 0)} for m in book.MEALS],
                     "tags": [[t, n] for t, n in book.top_tags(12)], "mains": list(book.MAINS)}}


@functools.lru_cache(maxsize=300)
def _book_card(book_id: int) -> bytes | None:
    import cards
    r = book.get(book_id)
    return cards.make_card(r["title"], r["text"]) if r else None


@app.get("/api/bookimg/{book_id}.png")
def api_bookimg(book_id: int):
    """Карточка рецепта из книги (рисуется на лету, кэшируется)."""
    png = _book_card(book_id) if 0 < book_id < 10**9 else None
    if not png:
        raise HTTPException(404, "Рецепта нет")
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


@app.post("/api/daymenu")
async def api_daymenu(p: Params, x_device: str | None = Header(default=None)):
    dev_id = _dev(x_device)
    data, meals = p.to_data(), p.meal_keys()
    sp = _spend(dev_id, "daymenu")
    cid = _cid(dev_id)

    async def work():
        summary = await day_menu.generate_day_menu(cid, meals, data)
        shopping = with_amounts(parse_shopping(summary.get("shopping_message", "")), day_menu.get_shopping_amounts(cid))
        web_db.kv_set(f"dayshop:{cid}", json.dumps(shopping, ensure_ascii=False))
        return {"ok": True}

    return _start_job(dev_id, work, sp)


@app.post("/api/week")
async def api_week(p: Params, x_device: str | None = Header(default=None)):
    dev_id = _dev(x_device)
    data, meals = p.to_data(), p.meal_keys()
    sp = _spend(dev_id, "week")
    cid = _cid(dev_id)

    async def work():
        _, shopping_md = await week_menu.start_week_plan(cid, meals, config.WEEK_MENU_DAYS, data)
        return {"ok": True}

    return _start_job(dev_id, work, sp)


@app.post("/api/week/cart")
async def api_week_cart(x_device: str | None = Header(default=None)):
    """Корзина плана с ценами во ВкусВилле — только по запросу, после выбора магазина (бывает до ~40 с)."""
    dev_id = _dev(x_device)
    cid = _cid(dev_id)
    info = week_menu.get_basket_info(cid)
    if not info:
        raise HTTPException(404, "Плана нет")
    if not plans.has_feature(_uid(dev_id), "cart"):
        raise HTTPException(402, "Корзина с ценами — в подписке 💎")
    if web_db.count_today(dev_id, "cart") >= 30:
        raise HTTPException(429, "Слишком много запросов корзины — попробуй завтра.")
    web_db.log_event(dev_id, "cart")
    priced = await build_priced_basket(info["inventory_initial"])
    if not priced:
        return {"text": "", "link": ""}
    m = re.search(r"\]\((https?://[^)]+)\)", priced)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", "", priced)
    text = re.sub(r"[*_`]", "", text)
    text = "\n".join(ln for ln in text.splitlines() if "Открыть корзину" not in ln)
    return {"text": text.strip(), "link": m.group(1) if m else ""}


@app.post("/api/week/cook")
async def api_week_cook(x_device: str | None = Header(default=None)):
    dev_id = _dev(x_device)
    cid = _cid(dev_id)
    index = week_menu.current_day_first_index(cid)
    if index is None:
        raise HTTPException(400, "План завершён или не найден")
    sp = _spend(dev_id, "weekday")

    async def work():
        slot = await week_menu.ensure_slot(cid, index)
        if slot.get("just_generated"):
            day = slot["day_number"]
            web_db.kv_set(f"weekshop:{cid}:{day}",
                          json.dumps(parse_shopping(slot.get("shopping_message", "")), ensure_ascii=False))
        return {"ok": True}

    return _start_job(dev_id, work, sp)


# ---------------- чтение меню ----------------

def _meal_out(key: str, meal: dict) -> dict:
    return {"key": key, "label": MEAL_LABELS.get(key, key), "title": meal.get("title", ""), "text": meal.get("text", "")}


@app.get("/api/overview")
def overview(x_device: str | None = Header(default=None)):
    dev_id = _dev(x_device)
    cid = _cid(dev_id)
    return {"has_day": day_menu.has_menu(cid), "has_week": week_menu.has_plan(cid),
            "favorites": len(fav_mod.get_favorites(_uid(dev_id)))}


@app.get("/api/day")
def day(x_device: str | None = Header(default=None)):
    cid = _cid(_dev(x_device))
    entry = day_menu._load_menu(cid)
    if not entry:
        raise HTTPException(404, "Меню на день ещё не собрано.")
    shop = web_db.kv_get(f"dayshop:{cid}")
    return {"meals": [_meal_out(k, entry["meals"][k]) for k in entry["order"]],
            "shopping": _mark_home(_dev(x_device), json.loads(shop) if shop else [])}


@app.get("/api/week")
def week(x_device: str | None = Header(default=None)):
    dev_id = _dev(x_device)
    cid = _cid(dev_id)
    state = week_menu._load(cid)
    if not state:
        raise HTTPException(404, "План питания ещё не собран.")
    n = len(state["meal_keys"])
    days = []
    for d in range(1, state["days_total"] + 1):
        entry = state["days"].get(str(d))
        first = (d - 1) * n
        meals = []
        if entry:
            for j, key in enumerate(state["meal_keys"]):
                m = _meal_out(key, entry["meals"][key])
                m["index"], m["done"] = first + j, first + j < state["progress_index"]
                meals.append(m)
        extra = web_db.kv_get(f"weekshop:{cid}:{d}")
        days.append({"day": d, "generated": bool(entry), "is_next": d == state["current_day"], "meals": meals,
                     "done_count": max(0, min(n, state["progress_index"] - first)), "total": n,
                     "extra_shopping": _mark_home(dev_id, json.loads(extra) if extra else [])})
    info = week_menu.get_basket_info(cid)
    items = [{"name": k, "amount": _fmt_amount(k, v),
              "links": [{"store": short_name(s), "url": search_url(s, k)} for s in DEFAULT_STORES]}
             for k, v in info["inventory_initial"].items()]
    _mark_home(dev_id, items)
    priced = re.sub(r"[*_`]|\[([^\]]+)\]\(([^)]+)\)", lambda m: m.group(1) or "", info["priced"] or "")
    return {"days": days, "basket": {"items": items, "priced_text": priced, "advice": info["basket_text"]},
            "remaining": {k: v for k, v in state["inventory"].items() if v > 0}}


@app.post("/api/week/done/{index}")
def week_done(index: int, x_device: str | None = Header(default=None)):
    week_menu.mark_done(_cid(_dev(x_device)), index)
    return {"ok": True}


# ---------------- избранное (сохранение страниц на устройство/сервер без регистрации) ----------------

class FavIn(BaseModel):
    title: str = Field(max_length=200)
    text: str = Field(max_length=20000)
    cuisine: str = ""


@app.get("/api/favorites")
def favorites(x_device: str | None = Header(default=None)):
    cid = _uid(_dev(x_device))
    return {"items": fav_mod.get_favorites(cid)}


@app.post("/api/favorites")
def favorites_add(f: FavIn, x_device: str | None = Header(default=None)):
    cid = _uid(_dev(x_device))
    if len(fav_mod.get_favorites(cid)) >= 200:
        raise HTTPException(400, "Слишком много сохранённых рецептов (лимит 200)")
    usage.event("fav_save", cid)
    return {"id": fav_mod.add_favorite(cid, f.title, f.text, f.cuisine)}


@app.delete("/api/favorites/{fav_id}")
def favorites_delete(fav_id: str, x_device: str | None = Header(default=None)):
    return {"ok": fav_mod.remove_favorite(_uid(_dev(x_device)), fav_id)}


# ---------------- личный кабинет: вход через Telegram, полка ----------------

_link_log: dict[int, list[float]] = {}


@app.get("/api/me")
def me(x_device: str | None = Header(default=None)):
    dev_id = _dev(x_device)
    tg = accounts.tg_of_device(dev_id)
    uid = _uid(dev_id)
    memory = bool(tg) and plans.has_feature(uid, "memory")
    shelf_on = bool(tg) and plans.shelf_on(uid)
    left = {k: list(v) for k in ("recipe", "daymenu", "week") if (v := usage.left(uid, k))}
    feat = {f: plans.has_feature(uid, f) for f in ("memory", "pdf", "cart", "noads")}
    out = {"linked": bool(tg), "plan": plans.plan_of(uid), "status": plans.status_text(uid), "left": left,
           "paywall": plans.paywall(), "memory": memory, "shelf_on": shelf_on, "bot": config.BOT_USERNAME, "feat": feat, "via": ("vk" if tg and tg >= accounts.VK_OFFSET else "tg" if tg else ""),
           "diary_on": plans.diary_on(uid), "seller": payinfo.seller(), "photo_on": plans.feature_on(uid, "photo_recipe"),
           "vk_app": (config.VK_CLIENT_ID if config.VK_LOGIN else ""),
           "pay_rub": bool(tg) and payanyway.allowed_for(uid),
           "pay_offers": ([{"key": o["key"], "title": o["title"], "rub": o["rub"], "days": o["days"]} for o in plans.pay_offers_for(uid)]
                          if bool(tg) and payanyway.allowed_for(uid) else []),
           "vk_redirect": (config.WEB_APP_URL or "https://menu.ymayaka.ru").rstrip("/") + "/api/vk/callback", "name": plans.PREMIUM_NAME,
           "trial": plans.trial_available(uid), "trial_days": int(plans.cfg("TRIAL_DAYS") or 0),
           "offers": [{"key": o["key"], "days": o["days"], "rub": o["rub"]} for o in plans.offers()],
           "favorites": len(fav_mod.get_favorites(uid))}
    if tg:
        out["consent"] = accounts.consent_get(uid)
        if out["consent"]:
            g = accounts.goals_get(uid)
            out["feats"] = g["feats"]
            out["goal_text"] = accounts.goal_text(uid) if "macro" in g["feats"] else ""
            out["never"] = g["never"] if "never" in g["feats"] and plans.has_feature(uid, "memory") else ""
    if shelf_on:
        out["shelf"] = [x["item"] for x in accounts.shelf_list(uid)]
    return out


@app.post("/api/link/start")
def link_start(x_device: str | None = Header(default=None)):
    """«Войти через Telegram»: одноразовый код → ссылка t.me/бот?start=link_<код>; сайт ждёт подтверждения."""
    dev_id = _dev(x_device)
    hist = [t for t in _link_log.get(dev_id, []) if time.time() - t < 3600]
    if len(hist) >= 10:
        raise HTTPException(429, "Слишком много попыток входа — попробуй через час.")
    _link_log[dev_id] = hist + [time.time()]
    _prune(_link_log, 3600)
    code = accounts.new_link_code(dev_id)
    return {"code": code, "url": f"https://t.me/{config.BOT_USERNAME}?start=link_{code}"}


@app.get("/api/link/status")
def link_status(code: str = "", x_device: str | None = Header(default=None)):
    return {"linked": accounts.link_code_status(code[:64], _dev(x_device))}


class CodeIn(BaseModel):
    code: str = Field(max_length=64)


@app.post("/api/link/claim")
def link_claim(body: CodeIn, x_device: str | None = Header(default=None)):
    """Сайт открыт по одноразовой ссылке из бота (?login=<код>)."""
    tg = accounts.claim_login_code(body.code, _dev(x_device))
    if tg:
        usage.event("site_link", tg)
    if not tg:
        raise HTTPException(400, "Ссылка для входа устарела или уже использована — возьми новую в боте (Профиль → Открыть сайт).")
    return {"ok": True}


class VkIn(BaseModel):
    access_token: str = Field(min_length=10, max_length=4096)


@app.post("/api/vk/login")
def vk_login(body: VkIn, x_device: str | None = Header(default=None)):
    """Вход через VK ID: виджет отдал токен, сервер спрашивает у ВК, чей он. Номер из браузера не принимаем: верим только ответу ВК."""
    if not config.VK_LOGIN:
        raise HTTPException(404, "Вход через ВКонтакте выключен")
    dev_id = _dev(x_device)
    hist = [t for t in _link_log.get(dev_id, []) if time.time() - t < 3600]
    if len(hist) >= 10:
        raise HTTPException(429, "Слишком много попыток входа — попробуй через час.")
    _link_log[dev_id] = hist + [time.time()]
    _prune(_link_log, 3600)
    try:
        resp = httpx.post(config.VK_ID_API_URL.rstrip("/") + "/oauth2/user_info",
                          data={"client_id": config.VK_CLIENT_ID, "access_token": body.access_token}, timeout=10)
        data = resp.json()
    except (httpx.HTTPError, ValueError):
        raise HTTPException(502, "ВКонтакте не ответил — попробуй ещё раз")
    user = data.get("user") if isinstance(data, dict) else None
    vk_id = str((user or {}).get("user_id") or "")
    if not vk_id.isdigit() or not 0 < int(vk_id) < 10**11:
        raise HTTPException(400, "ВКонтакте не подтвердил вход — попробуй ещё раз")
    try:
        accounts.link_device_vk(dev_id, int(vk_id))
    except ValueError as e:
        raise HTTPException(409, str(e))
    usage.event("site_link_vk", accounts.VK_OFFSET + int(vk_id))
    return {"ok": True}


@app.get("/api/vk/callback", response_class=HTMLResponse)
def vk_callback():
    """Адрес возврата, который зарегистрирован в приложении VK ID. Виджет работает без перехода, страница нужна на всякий случай."""
    return HTMLResponse("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
                        "<title>Вход</title><body style='font-family:sans-serif;padding:24px'>Можно закрыть это окно и вернуться на сайт."
                        "<p><a href='/'>На главную</a></p></body>")


class PayIn(BaseModel):
    plan: str = Field(min_length=1, max_length=30)


@app.post("/api/pay/create")
def pay_create(body: PayIn, x_device: str | None = Header(default=None)):
    """«Оплатить картой или СБП»: создаём заказ, возвращаем адрес страницы, которая отправит подписанную форму в PayAnyWay."""
    dev_id = _dev(x_device)
    if not accounts.tg_of_device(dev_id):
        raise HTTPException(403, "Сначала войдите в профиль (через ВКонтакте или Telegram)")
    uid = _uid(dev_id)
    if not payanyway.allowed_for(uid):
        raise HTTPException(403, "Оплата пока недоступна")
    try:
        order = payanyway.create_order(uid, body.plan)
    except ValueError as e:
        raise HTTPException(400, str(e))
    usage.event("pay_start", uid)
    return {"url": (config.WEB_APP_URL or "https://menu.ymayaka.ru").rstrip("/") + "/pay/" + order["id"]}


@app.get("/pay/{oid}", response_class=HTMLResponse)
def pay_page(oid: str):
    order = payanyway.get_order(oid) if payanyway.enabled() else None
    if not order or order["status"] != "new" or time.time() - order["ts"] > payanyway.ORDER_TTL:
        raise HTTPException(404, "Заказ не найден или уже оплачен")
    return HTMLResponse(payanyway.pay_page(order), headers={"Cache-Control": "no-store"})


@app.api_route("/api/pay/payanyway", methods=["GET", "POST"])
async def pay_notify(request: Request):
    """Pay URL: уведомление PayAnyWay об оплате. Ответ SUCCESS (текстом) только после проверки подписи, суммы и заказа."""
    params = dict(request.query_params)
    if request.method == "POST":
        try:
            params.update({k: str(v) for k, v in (await request.form()).items()})
        except Exception:  # noqa: BLE001
            return Response("FAIL", media_type="text/plain; charset=utf-8")
    text, info = payanyway.handle_notification(params)
    if info and info["new"]:
        usage.event("payment_pw", info["uid"])
        await asyncio.to_thread(payanyway.notify_paid, info)
    if text == "SUCCESS":
        return Response(payanyway.success_response(params, info), media_type="application/xml; charset=utf-8")
    return Response(text, media_type="text/plain; charset=utf-8")


@app.post("/api/unlink")
def unlink(x_device: str | None = Header(default=None)):
    accounts.unlink_device(_dev(x_device))
    return {"ok": True}


def _need_memory(dev_id: int) -> str:
    uid = _uid(dev_id)
    if plans.is_anon(uid):
        raise HTTPException(403, "Сначала войди через Telegram")
    if not plans.has_feature(uid, "memory"):
        raise HTTPException(402, "Эта функция — в тарифе «Повар» 💎")
    return uid


class ProfileIn(BaseModel):
    feats: list[str] | None = None
    never: str | None = Field(None, max_length=300)
    kcal: int | float | str | None = None
    protein: int | float | str | None = None
    fat: int | float | str | None = None
    carb: int | float | str | None = None
    weight_goal: int | float | str | None = None


class WeightIn(BaseModel):
    kg: int | float | str
    day: str | None = Field(None, max_length=10)


def _need_account(dev_id: int) -> str:
    uid = _uid(dev_id)
    if plans.is_anon(uid):
        raise HTTPException(403, "Сначала войди через Telegram")
    return uid


def _need_consent(dev_id: int) -> str:
    uid = _need_account(dev_id)
    if not accounts.consent_get(uid):
        raise HTTPException(403, "Сначала нужно согласие на хранение данных профиля")
    return uid


def _profile_json(uid: str) -> dict:
    g = accounts.goals_get(uid)
    return {**g, "consent": True, "goal_text": accounts.goal_text(uid), "weights": accounts.weights_list(uid),
            "never_allowed": plans.has_feature(uid, "memory")}


@app.get("/api/profile")
def profile_get(x_device: str | None = Header(default=None)):
    uid = _need_account(_dev(x_device))
    if not accounts.consent_get(uid):
        return {"consent": False, "text": accounts.CONSENT_TEXT}
    return _profile_json(uid)


@app.post("/api/profile/consent")
def profile_consent(x_device: str | None = Header(default=None)):
    uid = _need_account(_dev(x_device))
    accounts.consent_set(uid)
    usage.event("profile_consent", uid)
    return _profile_json(uid)


@app.put("/api/profile")
def profile_put(body: ProfileIn, x_device: str | None = Header(default=None)):
    uid = _need_consent(_dev(x_device))
    fields = body.model_dump(exclude_unset=True)
    if "never" in fields and not plans.has_feature(uid, "memory"):
        raise HTTPException(402, "Исключение продуктов входит в тариф «Повар» 💎")
    if "feats" in fields and not plans.has_feature(uid, "memory"):      # «не использовать» и «полка» входят в тариф
        fields["feats"] = [f for f in (fields["feats"] or []) if f not in ("never", "shelf")]
    try:
        accounts.goals_set(uid, **fields)
    except ValueError as e:
        raise HTTPException(400, str(e))
    usage.event("profile_edit", uid)
    return _profile_json(uid)


@app.post("/api/weight")
def weight_add(body: WeightIn, x_device: str | None = Header(default=None)):
    uid = _need_consent(_dev(x_device))
    try:
        accounts.weight_set(uid, body.kg, body.day)
    except ValueError as e:
        raise HTTPException(400, str(e))
    usage.event("weight_add", uid)
    return _profile_json(uid)


@app.delete("/api/weight")
def weight_del(day: str = "", x_device: str | None = Header(default=None)):
    uid = _need_consent(_dev(x_device))
    accounts.weight_delete(uid, day[:10])
    return _profile_json(uid)


# ---------- дневник питания на сайте (те же данные, что в боте) ----------

class DiaryIn(BaseModel):
    food: str | None = Field(None, max_length=20)
    grams: float | None = None
    title: str | None = Field(None, max_length=80)
    kcal: int | float | str | None = None


class DiaryRecipeIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=20000)


class EnergyIn(BaseModel):
    sex: str = Field(max_length=1)
    age: int | float | str
    height: int | float | str
    weight: int | float | str
    activity: str = Field(max_length=20)


def _need_diary(dev_id: int) -> str:
    uid = _need_consent(dev_id)
    if not plans.diary_on(uid):
        raise HTTPException(403, "Включите «Дневник питания» в настройках профиля")
    return uid


def _food_json(f: dict) -> dict:
    return {"id": f["id"], "name": f["name"], "label": f["label"], "grams": f["grams"], "kcal100": f["kcal100"],
            "portion_kcal": foods.portion_kcal(f), "ai": bool(f.get("ai"))}


@app.get("/api/diary")
def diary_get(days: int = 30, x_device: str | None = Header(default=None)):
    uid = _need_diary(_dev(x_device))
    days = days if days in (7, 30, 90) else 30
    series = accounts.diary_series(uid, days)
    en, g = accounts.energy_get(uid), accounts.goals_get(uid)
    target = {"kcal": g["kcal"], "title": "цель"} if g["kcal"] else {"kcal": en["tdee"], "title": "расход"} if en else None
    by_name = {f["name"]: f for f in foods.FOODS}
    return {"days": days, "today": accounts.diary_day(uid), "target": target, "energy": en, "series": series, "stats": accounts.diary_stats(series),
            "shares": charts.macro_shares(series), "reference": charts.REFERENCE, "note": accounts.DIARY_NOTE,
            "weights": [w for w in accounts.weights_list(uid, days + 5) if w["day"] >= series[0]["day"]], "weight_goal": g["weight_goal"],
            "frequent": [_food_json(by_name[n]) for n in accounts.diary_frequent(uid) if n in by_name],
            "activities": [{"key": k, "title": v[1]} for k, v in accounts.ACTIVITY.items()]}


@app.get("/api/foods")
async def foods_find(q: str = "", ai: int = 0, x_device: str | None = Header(default=None)):
    """Поиск продукта: сначала справочник; если там пусто или попросили (ai=2), оценка ИИ. ai=0: только справочник."""
    uid = await asyncio.to_thread(lambda: _need_diary(_dev(x_device)))
    q = q.strip()[:60]
    found = foods.search(q) if ai != 2 else []
    if found or not ai:
        return {"items": [_food_json(f) for f in found], "why": ""}
    food, why = await food_ai.estimate(uid, q)
    return {"items": [_food_json(food)] if food else [], "why": why}


@app.post("/api/diary")
def diary_add(body: DiaryIn, x_device: str | None = Header(default=None)):
    uid = _need_diary(_dev(x_device))
    try:
        if body.food:
            food = food_ai.lookup(body.food)
            if not food or body.grams is None or not 5 <= body.grams <= 2000:
                raise ValueError("Нужен вес от 5 до 2000 г")
            n = foods.nutrition(food, body.grams)
            accounts.diary_add(uid, f"{food['name']} ({int(round(body.grams))} г)", n["kcal"], n["protein"], n["fat"], n["carb"], "ai" if food.get("ai") else "list")
            kcal = n["kcal"]
        else:
            accounts.diary_add(uid, body.title or "", body.kcal, src="manual")
            kcal = int(float(str(body.kcal).replace(",", ".")))
    except ValueError as e:
        raise HTTPException(400, str(e))
    usage.event("diary_add", uid)
    return {"ok": True, "kcal": kcal}


@app.post("/api/diary/recipe")
def diary_recipe(body: DiaryRecipeIn, x_device: str | None = Header(default=None)):
    """«Съел порцию» под рецептом: КБЖУ берётся из самого рецепта."""
    uid = _need_diary(_dev(x_device))
    d = cards.parse(body.title, body.text)
    num = lambda v: float(str(v or 0).replace(",", "."))
    try:
        if not d["kcal"]:
            raise ValueError("В рецепте нет калорийности")
        kcal = int(num(d["kcal"]))
        accounts.diary_add(uid, d["title"], kcal, num(d["protein"]), num(d["fat"]), num(d["carb"]), "recipe")
    except ValueError as e:
        raise HTTPException(400, str(e))
    usage.event("diary_add", uid)
    return {"ok": True, "kcal": kcal}


@app.delete("/api/diary/{entry_id}")
def diary_del(entry_id: int, x_device: str | None = Header(default=None)):
    uid = _need_diary(_dev(x_device))
    return {"ok": accounts.diary_delete(uid, entry_id)}


@app.post("/api/energy")
def energy_calc(body: EnergyIn, x_device: str | None = Header(default=None)):
    """Примерный расход. Пол, возраст, рост и вес не сохраняем: только итог и уровень активности."""
    uid = _need_diary(_dev(x_device))
    try:
        tdee = accounts.calc_tdee(body.sex, body.age, body.height, body.weight, body.activity)
    except ValueError as e:
        raise HTTPException(400, str(e))
    accounts.energy_set(uid, body.activity, tdee)
    usage.event("diary_calc", uid)
    return {"tdee": tdee}


# ---------- рецепт по фото продуктов ----------

PHOTO_TYPES = {"image/jpeg", "image/png", "image/webp"}
PHOTO_MAX_BYTES = 10_000_000


@app.post("/api/photo")
async def photo_items(image: UploadFile = File(...), x_device: str | None = Header(default=None)):
    """Продукты на фото: список подставляется в поле «Что использовать», человек его правит. Новая функция, включается в админке."""
    dev_id = _dev(x_device)
    if not plans.feature_on(_uid(dev_id), "photo_recipe"):
        raise HTTPException(403, "Рецепт по фото пока недоступен")
    if (image.content_type or "") not in PHOTO_TYPES:
        raise HTTPException(400, "Нужна фотография: jpg, png или webp")
    blob = await image.read(PHOTO_MAX_BYTES + 1)
    if not blob or len(blob) > PHOTO_MAX_BYTES:
        raise HTTPException(400, "Фото больше 10 МБ, снимите ещё раз")
    sp = _spend(dev_id, "photo")
    try:
        items = await vision.photo_items([(blob, image.content_type)])
    except vision.VisionError:
        _refund(dev_id, sp)
        raise HTTPException(502, "Не получилось разобрать фото, попробуйте ещё раз поближе и при хорошем свете")
    if not items:
        _refund(dev_id, sp)
        raise HTTPException(422, "Не вижу на фото продуктов, попробуйте снять поближе")
    usage.event("photo_recipe", _uid(dev_id))
    return {"items": items}


# ---------- список покупок по ссылке ----------

class ListIn(BaseModel):
    title: str = Field("", max_length=120)
    items: list[dict] = Field(max_length=100)
    done: list[int] = Field(default_factory=list, max_length=100)


class ListToggle(BaseModel):
    i: int
    on: bool


_list_log: dict[str, deque] = {}
LIST_TOGGLES_PER_HOUR = 1000          # отметок в одном списке в час: хватит семье, но база не раздуется


@app.post("/api/list")
def list_create(body: ListIn, x_device: str | None = Header(default=None)):
    uid = _uid(_dev(x_device))
    try:
        lid = shoplists.create(uid, body.title, body.items, body.done)
    except ValueError as e:
        raise HTTPException(400, str(e))
    usage.event("list_share", uid)
    return {"id": lid, "url": shoplists.url(lid)}


@app.get("/api/list/{lid}")
def list_get(lid: str):
    rec = shoplists.get(lid)
    if not rec:
        raise HTTPException(404, "Список не найден")
    return Response(json.dumps(rec, ensure_ascii=False), media_type="application/json", headers={"Cache-Control": "no-store"})


@app.post("/api/list/{lid}/toggle")
def list_toggle(lid: str, body: ListToggle):
    q = _list_log.setdefault(lid, deque())
    now = time.time()
    while q and now - q[0] > 3600:
        q.popleft()
    if len(q) >= LIST_TOGGLES_PER_HOUR:
        raise HTTPException(429, "Слишком много отметок, подождите немного")
    q.append(now)
    if len(_list_log) > 5000:
        for k in [k for k, v in _list_log.items() if not v or now - v[-1] > 3600]:
            _list_log.pop(k, None)
    done = shoplists.toggle(lid, body.i, body.on)
    if done is None:
        raise HTTPException(404, "Список не найден")
    return {"done": done}


@app.api_route("/oplata", methods=["GET", "HEAD"], response_class=HTMLResponse)
def pay_terms():
    """Открытая страница: цены, порядок оплаты и возврата, реквизиты продавца."""
    return HTMLResponse(payinfo.page())


@app.api_route("/l/{lid}", methods=["GET", "HEAD"], response_class=HTMLResponse)
def list_page(lid: str):
    rec = shoplists.get(lid)
    if not rec:
        raise HTTPException(404, "Список не найден или устарел")
    return HTMLResponse(shoplists.page(lid, rec), headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"})


@app.delete("/api/profile/data")
def profile_delete(x_device: str | None = Header(default=None)):
    uid = _need_account(_dev(x_device))
    accounts.delete_my_data(uid)
    usage.event("profile_delete", uid)
    return {"ok": True}


@app.post("/api/trial")
def trial_start(x_device: str | None = Header(default=None)):
    """«Попробовать тариф»: пробная неделя по кнопке на замке (один раз на аккаунт)."""
    uid = _need_account(_dev(x_device))
    if not plans.paywall():
        raise HTTPException(409, "Сейчас все функции и так открыты")
    until = plans.maybe_start_trial(uid)
    if not until:
        raise HTTPException(409, "Пробный период уже использован")
    usage.event("trial_start", uid)
    return {"ok": True, "until": until, "days": int(plans.cfg("TRIAL_DAYS") or 0)}


class FeedbackIn(BaseModel):
    kind: str = Field("idea", max_length=10)
    text: str = Field(min_length=3, max_length=1500)
    contact: str = Field("", max_length=100)


@app.api_route("/r/{slug}", methods=["GET", "HEAD"], response_class=HTMLResponse)
def recipe_page(slug: str):
    """Публичная страница рецепта дня: превью для ВК и мессенджеров (og:image) и текст для поисковиков."""
    import recipe_pages
    rec = accounts.pub_recipe_get(slug) if recipe_pages.SLUG_RE.match(slug) else None
    if not rec:
        raise HTTPException(404, "Рецепт не найден")
    base = (config.WEB_APP_URL or "https://menu.ymayaka.ru").rstrip("/")
    return HTMLResponse(recipe_pages.render(rec, slug, base, config.BOT_USERNAME))


@app.api_route("/rimg/{slug}.png", methods=["GET", "HEAD"])
def recipe_image(slug: str):
    import recipe_pages
    img = accounts.pub_recipe_image(slug) if recipe_pages.SLUG_RE.match(slug) else None
    if not img:
        raise HTTPException(404, "Картинки нет")
    return Response(content=img, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/help")
def help_get():
    import helpdata
    return {"sections": [{"key": k, "title": t, "text": x} for k, t, x in helpdata.SECTIONS]}


@app.post("/api/feedback")
def feedback_send(body: FeedbackIn, x_device: str | None = Header(default=None)):
    """«Написать разработчику»: идея, ошибка, вопрос. Не больше 5 в сутки с устройства или аккаунта."""
    import feedback
    uid = _uid(_dev(x_device))
    try:
        fid = accounts.feedback_add(uid, body.kind, body.text, body.contact, "web")
    except ValueError as e:
        raise HTTPException(429 if "много" in str(e) else 400, str(e))
    usage.event("feedback", uid)
    feedback.notify_sync(feedback.admin_text(fid, body.kind, "web", uid, body.text, body.contact))
    return {"ok": True}


GATE_NAMES = {"pdf", "cart", "memory", "daymenu", "week", "photo", "voice"}


@app.post("/api/gate")
def gate_seen(request: Request, body: dict, x_device: str | None = Header(default=None)):
    """Человек нажал на закрытую функцию — считаем, чтобы видеть, что его привлекает."""
    dev = _dev(x_device)
    name = str(body.get("feature", ""))[:20]
    if name in GATE_NAMES and _stat_ok(_client_ip(request)):
        usage.event("gate_" + name, _uid(dev))
    return {"ok": True}


def _need_shelf(dev_id: int) -> str:
    uid = _need_memory(dev_id)
    if not plans.shelf_on(uid):
        raise HTTPException(403, "Включите «Полку» в профиле, раздел «Мои функции»")
    return uid


class ShelfIn(BaseModel):
    item: str = Field(min_length=1, max_length=60)


@app.post("/api/shelf")
def shelf_add(body: ShelfIn, x_device: str | None = Header(default=None)):
    uid = _need_shelf(_dev(x_device))
    if len(accounts.shelf_list(uid)) >= 100:
        raise HTTPException(400, "На полке уже 100 продуктов")
    accounts.shelf_add(uid, body.item)
    return {"shelf": [x["item"] for x in accounts.shelf_list(uid)]}


@app.delete("/api/shelf")
def shelf_del(item: str = "", x_device: str | None = Header(default=None)):
    uid = _need_shelf(_dev(x_device))
    accounts.shelf_remove(uid, item[:60])
    return {"shelf": [x["item"] for x in accounts.shelf_list(uid)]}


# ---------------- PDF (скачать рецепт/меню одним файлом, чтобы не потерять) ----------------

class PdfItem(BaseModel):
    title: str = Field("", max_length=200)
    text: str = Field("", max_length=20000)


class PdfIn(BaseModel):
    items: list[PdfItem] = Field(min_length=1, max_length=200)
    name: str = Field("", max_length=120)   # имя файла и заголовок документа


def _pdf_filename(name: str) -> str:
    safe = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", name).strip(" .")[:80]
    return (safe or "recipe") + ".pdf"


@app.post("/api/pdf")
def api_pdf(body: PdfIn, x_device: str | None = Header(default=None)):
    dev_id = _dev(x_device)
    if not plans.has_feature(_uid(dev_id), "pdf"):
        raise HTTPException(402, "PDF — в подписке 💎")
    if web_db.count_today(dev_id, "pdf") >= 200:
        raise HTTPException(429, "Слишком много PDF за сутки — попробуй завтра")
    if sum(len(i.text) for i in body.items) > 600_000:
        raise HTTPException(413, "Слишком большой документ")
    try:
        pdf = web_pdf.build_pdf([i.model_dump() for i in body.items], site=config.WEB_APP_URL, doc_title=body.name)
    except web_pdf.PdfUnavailable as e:
        raise HTTPException(501, f"PDF на сервере недоступен: {e}")
    web_db.log_event(dev_id, "pdf")
    usage.event("pdf", _uid(dev_id))
    filename = _pdf_filename(body.name or body.items[0].title)
    return Response(
        pdf, media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=\"recipe.pdf\"; filename*=UTF-8''{quote(filename)}",
                 "Cache-Control": "no-store"},
    )


# ---------------- реклама ----------------

@app.get("/api/ad")
def get_ad(slot: str = "top", x_device: str | None = Header(default=None)):
    if plans.paywall() and "noads" in (plans.cfg("PREMIUM_FEATURES") or []):
        dev_id = web_db.device_id((x_device or "").strip())
        if dev_id and plans.plan_of(_uid(dev_id)) == "premium":
            return {"ad": None}
    with web_db.db() as c:
        rows = c.execute("SELECT * FROM ads WHERE slot=? AND active=1", (slot,)).fetchall()
        if not rows:
            return {"ad": None}
        a = random.choice(rows)
        c.execute("INSERT INTO ad_events(ad_id,kind,ts) VALUES(?,?,?)", (a["id"], "view", time.time()))
    return {"ad": {"id": a["id"], "title": a["title"], "image": f"/uploads/{a['image']}", "go": f"/go/{a['id']}"}}


@app.get("/go/{ad_id}")
def go(ad_id: int):
    with web_db.db() as c:
        a = c.execute("SELECT url FROM ads WHERE id=?", (ad_id,)).fetchone()
        if not a:
            raise HTTPException(404)
        c.execute("INSERT INTO ad_events(ad_id,kind,ts) VALUES(?,?,?)", (ad_id, "click", time.time()))
    return RedirectResponse(a["url"], status_code=302)


# ---------------- админка ----------------

_login_fail: dict[str, list[float]] = {}
ROLE_TITLES = {"owner": "владелец", "partner": "партнёр"}


def _role_password(role: str) -> str:
    return {"owner": config.ADMIN_PASSWORD, "partner": config.PARTNER_PASSWORD}.get(role, "")


def _admin_cookie(role: str) -> str:
    """«роль.подпись»: подпись зависит от пароля роли — сменили пароль, и старые входы перестают работать."""
    sig = hmac.new(config.WEB_SECRET.encode(), f"admin:{role}:{_role_password(role)}".encode(), hashlib.sha256).hexdigest()
    return f"{role}.{sig}"


def _need_admin(request: Request, owner: bool = False) -> str:
    """Роль вошедшего: owner (владелец — смотрит и меняет всё) или partner (партнёр — только смотрит).
    owner=True — действие меняет данные, партнёру недоступно."""
    raw = request.cookies.get("adm", "")
    role = raw.split(".", 1)[0]
    if (config.WEB_SECRET and _role_password(role) and role in ROLE_TITLES
            and hmac.compare_digest(raw.encode(), _admin_cookie(role).encode())):
        if owner and role != "owner":
            raise HTTPException(403, "Партнёр может только смотреть. Менять настройки может владелец.")
        return role
    raise HTTPException(401, "admin")


@app.post("/admin/login")
async def admin_login(request: Request, response: Response):
    ip = _client_ip(request)
    fails = [t for t in _login_fail.get(ip, []) if time.time() - t < 600]
    if len(fails) >= 5:
        raise HTTPException(429, "Слишком много попыток, подожди 10 минут")
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "Неверный запрос")
    if not isinstance(body, dict):
        raise HTTPException(400, "Неверный запрос")
    pw = str(body.get("password", "")).encode()
    role = next((r for r in ("owner", "partner") if _role_password(r) and config.WEB_SECRET
                 and hmac.compare_digest(pw, _role_password(r).encode())), None)
    if not role:
        fails.append(time.time())
        _login_fail[ip] = fails
        _prune(_login_fail, 600)
        raise HTTPException(401, "Неверный пароль")
    accounts.admin_log(role, "вход", ip)
    resp = JSONResponse({"ok": True, "role": role})
    resp.set_cookie("adm", _admin_cookie(role), httponly=True, samesite="strict",
                    secure=request.headers.get("x-forwarded-proto") == "https", max_age=30 * 86400)
    return resp


@app.post("/admin/logout")
def admin_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("adm")
    return resp


def _settings_view() -> dict:
    """Текущие настройки тарифов в удобном для формы виде (что можно менять — и кем)."""
    plans.reload()
    return {
        "paywall": plans.paywall(),
        "limits": {p: {k: list(v) for k, v in plans.limits_of(p).items()} for p in ("free", "premium", "anon")},
        "features": list(plans.cfg("PREMIUM_FEATURES") or []),
        "feature_titles": plans.FEATURE_TITLES,
        "kind_titles": plans.KIND_TITLES,
        "rollout": plans.rollout(),
        "new_features": plans.NEW_FEATURES,
        "rollout_levels": plans.ROLLOUT_LEVELS,
        "testers": sorted(plans.testers()),
        "trial_days": int(plans.cfg("TRIAL_DAYS") or 0),
        "offers": plans.offers(),
        "promo": plans.promo_codes(),
        "daily_cap": int(plans.cfg("DAILY_GEN_CAP")),
        "anon_cap": int(plans.cfg("DAILY_GEN_CAP_ANON")),
        "ip_cap": int(plans.cfg("WEB_IP_DAILY_GENS")),
        "partner_share": int(plans.cfg("PARTNER_SHARE_PCT")),
        "editable": {k: v[0] for k, v in plans.EDITABLE.items()},
    }


@app.get("/admin/api/stats")
async def admin_stats(request: Request):
    role = _need_admin(request)
    out = web_db.stats(tg_users=len(get_all_users()))
    out["role"] = role
    out["usage"] = usage.admin_stats()
    out["money"] = accounts.money_stats()
    out["accounts"] = accounts.account_stats()
    out["refs"] = accounts.ref_stats()
    out["promos"] = accounts.promo_stats()
    out["settings"] = _settings_view()
    out["bot"] = config.BOT_USERNAME
    import alerts
    out["deepseek_balance_usd"] = await alerts.deepseek_balance()
    return out


def _limits_spec(d: dict) -> str:
    parts = []
    for kind in ("recipe", "daymenu", "week", "missing", "photo"):
        n, days = (d.get(kind) or [0, 1])[:2]
        parts.append(f"{kind}={max(0, min(int(n), 100))}/{max(1, min(int(days), 30))}")
    return ",".join(parts)


@app.post("/admin/api/settings")
async def admin_settings(request: Request):
    """Сохранить тарифы/лимиты/функции/промокоды — только владелец (партнёр видит, но не меняет)."""
    role = _need_admin(request, owner=True)
    try:
        body = await request.json()
        assert isinstance(body, dict)
    except (ValueError, AssertionError):
        raise HTTPException(400, "Неверный запрос")
    new: dict = {}
    try:
        if "paywall" in body:
            new["PAYWALL_ENABLED"] = bool(body["paywall"])
        for key, name in (("daily_cap", "DAILY_GEN_CAP"), ("anon_cap", "DAILY_GEN_CAP_ANON"),
                          ("ip_cap", "WEB_IP_DAILY_GENS"), ("trial_days", "TRIAL_DAYS")):
            if key in body:
                new[name] = max(0, min(int(body[key]), 100000))
        if "partner_share" in body:
            new["PARTNER_SHARE_PCT"] = max(0, min(int(body["partner_share"]), 100))
        for plan, name in (("free", "LIMITS_FREE"), ("premium", "LIMITS_PREMIUM"), ("anon", "LIMITS_ANON")):
            if plan in (body.get("limits") or {}):
                new[name] = _limits_spec(body["limits"][plan])
        if "features" in body:
            new["PREMIUM_FEATURES"] = [f for f in body["features"] if f in plans.FEATURE_TITLES]
        if "rollout" in body:
            new["FEATURE_ROLLOUT"] = {k: v for k, v in (body["rollout"] or {}).items()
                                      if k in plans.NEW_FEATURES and v in plans.ROLLOUT_LEVELS}
        if "testers" in body:
            raw = body["testers"]
            ids = re.findall(r"-?\d{3,15}", raw if isinstance(raw, str) else " ".join(map(str, raw or [])))
            new["TESTER_IDS"] = sorted(set(ids))[:200]
        if "offers" in body:
            rows = []
            for o in body["offers"][:6]:
                key = re.sub(r"[^a-z0-9_]", "", str(o.get("key", "")).lower())[:16]
                days, rub, stars = int(o.get("days", 0)), int(o.get("rub", 0)), int(o.get("stars", 0))
                if key and 1 <= days <= 3650 and rub >= 0 and stars >= 0:
                    rows.append(f"{key}={days}:{rub}:{stars}")
            new["PREMIUM_PLANS"] = ",".join(rows)
        if "promo" in body:
            rows = []
            for code, days in (body["promo"] or {}).items():
                code = re.sub(r"[^A-Za-z0-9_-]", "", str(code)).upper()[:32]
                if code and 0 < int(days) <= 365:
                    rows.append(f"{code}={int(days)}")
            new["PROMO_CODES"] = ",".join(rows)
    except (TypeError, ValueError, AttributeError):
        raise HTTPException(400, "Проверь значения — где-то не число")
    before = {k: plans.cfg(k) for k in new}
    diff = {k: v for k, v in new.items() if v != before[k]}
    if diff:
        accounts.settings_set(diff, role)
        accounts.admin_log(role, "настройки", "; ".join(f"{k}: {before[k]} → {v}" for k, v in diff.items()))
    plans.reload()
    return {"ok": True, "changed": list(diff), "settings": _settings_view()}


class GrantIn(BaseModel):
    uid: int
    days: int = Field(ge=1, le=365)


@app.post("/admin/api/grant")
def admin_grant(body: GrantIn, request: Request):
    """Выдать подписку вручную (например, блогеру или в подарок) — по id в Telegram."""
    role = _need_admin(request, owner=True)
    until = accounts.grant_days(body.uid, body.days, f"admin:{role}")
    accounts.admin_log(role, "подписка вручную", f"{body.uid}: +{body.days} дн.")
    return {"until": until}


@app.get("/admin/api/feedback")
async def admin_feedback(request: Request):
    _need_admin(request, owner=True)      # сообщения людей видит только владелец
    return {"items": accounts.feedback_list(), "kinds": accounts.FEEDBACK_KINDS}


@app.post("/admin/api/feedback/{fid}/done")
async def admin_feedback_done(fid: int, request: Request):
    _need_admin(request, owner=True)
    accounts.feedback_done(fid)
    return {"ok": True}


@app.get("/admin/api/log")
def admin_log_view(request: Request):
    _need_admin(request)
    return {"items": accounts.admin_log_list(200)}


@app.post("/admin/api/ads")
async def admin_ad_add(request: Request, slot: str = FForm(...), title: str = FForm(""), url: str = FForm(...),
                       image: UploadFile = File(...)):
    role = _need_admin(request, owner=True)
    accounts.admin_log(role, "реклама: новый баннер", url[:200])
    if slot not in ("top", "inline"):
        raise HTTPException(400, "slot")
    if not re.match(r"^https?://", url):
        raise HTTPException(400, "Ссылка должна начинаться с http:// или https://")
    ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}.get(image.content_type or "")
    if not ext:
        raise HTTPException(400, "Только png, jpg, webp или gif")
    blob = await image.read()
    if len(blob) > 1_500_000:
        raise HTTPException(400, "Файл больше 1,5 МБ")
    name = f"{secrets.token_hex(8)}.{ext}"
    (web_db.DATA_DIR / "uploads" / name).write_bytes(blob)
    with web_db.db() as c:
        c.execute("INSERT INTO ads(slot,title,image,url,active,created) VALUES(?,?,?,?,1,?)",
                  (slot, title[:120], name, url, time.time()))
    return {"ok": True}


@app.post("/admin/api/ads/{ad_id}/toggle")
def admin_ad_toggle(ad_id: int, request: Request):
    _need_admin(request, owner=True)
    with web_db.db() as c:
        c.execute("UPDATE ads SET active=1-active WHERE id=?", (ad_id,))
    return {"ok": True}


@app.delete("/admin/api/ads/{ad_id}")
def admin_ad_delete(ad_id: int, request: Request):
    _need_admin(request, owner=True)
    with web_db.db() as c:
        row = c.execute("SELECT image FROM ads WHERE id=?", (ad_id,)).fetchone()
        c.execute("DELETE FROM ads WHERE id=?", (ad_id,))
    if row:
        (web_db.DATA_DIR / "uploads" / row["image"]).unlink(missing_ok=True)
    return {"ok": True}


# ---------------- страницы ----------------

def _verify_meta() -> str:
    """Метатеги подтверждения прав на сайт для Яндекс Вебмастера и Google Search Console (коды — в .env)."""
    tags = []
    if config.YANDEX_VERIFICATION:
        tags.append(f'<meta name="yandex-verification" content="{escape(config.YANDEX_VERIFICATION)}">')
    if config.GOOGLE_SITE_VERIFICATION:
        tags.append(f'<meta name="google-site-verification" content="{escape(config.GOOGLE_SITE_VERIFICATION)}">')
    return "".join(tags)


@app.get("/")
def index():
    html = (BASE / "web" / "index.html").read_text(encoding="utf-8").replace("<!--VERIFY-->", _verify_meta(), 1)
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


@app.get("/pitanie")
def seo_index():
    return HTMLResponse(seo_pages.render_index(_site()))


@app.get("/pitanie/{slug}")
def seo_page(slug: str):
    html = seo_pages.render_page(slug, _site())
    if html is None:
        raise HTTPException(404, "Страница не найдена")
    return HTMLResponse(html)


@app.get("/admin")
def admin_page():
    return FileResponse(BASE / "web" / "admin.html", media_type="text/html; charset=utf-8")


# PWA: «Добавить на главный экран» — манифест, service worker (должен лежать в корне, чтобы
# управлять всем сайтом) и иконки.
@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(BASE / "web" / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js")
def service_worker():
    return FileResponse(BASE / "web" / "sw.js", media_type="application/javascript",
                        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


app.mount("/icons", StaticFiles(directory=BASE / "web" / "icons"), name="icons")


@app.get("/api/health")
def health():
    return {"ok": True}


# ---------------- SEO / GEO: robots, sitemap, llms.txt ----------------

def _site() -> str:
    return (config.WEB_APP_URL or "https://menu.ymayaka.ru").rstrip("/")


@app.middleware("http")
async def _noindex_private(request: Request, call_next):
    resp = await call_next(request)
    if request.url.path.startswith(("/api", "/admin", "/go", "/uploads")):
        resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


@app.get("/robots.txt")
def robots():
    body = ("User-agent: *\nAllow: /\nDisallow: /api/\nDisallow: /admin\nDisallow: /go/\nDisallow: /uploads/\n\n"
            f"Sitemap: {_site()}/sitemap.xml\n")
    return Response(body, media_type="text/plain; charset=utf-8")


@app.get("/sitemap.xml")
def sitemap():
    lastmod = time.strftime("%Y-%m-%d", time.gmtime(max(
        (BASE / "web" / "index.html").stat().st_mtime, (BASE / "seo_pages.py").stat().st_mtime)))
    urls = [("/", "1.0")] + [(p, "0.8" if p != "/pitanie" else "0.6") for p in seo_pages.all_paths()]
    rows = "".join(f"  <url><loc>{_site()}{p}</loc><lastmod>{lastmod}</lastmod><priority>{pr}</priority></url>\n"
                   for p, pr in urls)
    body = ('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + rows + "</urlset>\n")
    return Response(body, media_type="application/xml")


@app.get("/llms.txt")
def llms():
    body = (
        "# Кухонный помощник\n\n"
        "> Бесплатный русскоязычный веб-помощник по питанию без регистрации: рецепты по кухне и продуктам, "
        "меню на день, план питания на 3 дня, калории и КБЖУ, список покупок, PDF.\n\n"
        "## Возможности\n"
        "- Рецепт с калорийностью и КБЖУ на порцию, учитывает кухню (классическая, аюрведа, спорт и др.) и технику\n"
        "- Меню на день по приёмам пищи с общим списком покупок\n"
        "- План питания на 3 дня с единой корзиной продуктов и учётом остатков\n"
        "- Ссылки на поиск продуктов в Пятёрочке, ВкусВилле, Перекрёстке, Яндекс Лавке, Купере; "
        "корзина с ценами во ВкусВилле\n"
        "- Скачивание в PDF, добавление на главный экран телефона (PWA)\n"
        "- Есть Telegram-бот с теми же функциями\n\n"
        f"## Ссылки\n- [Приложение]({_site()}/)\n"
        + "".join(f"- [{p['h1']}]({_site()}/pitanie/{s}): {p['description']}\n" for s, p in seo_pages.PAGES.items())
    )
    return Response(body, media_type="text/plain; charset=utf-8")


class CartItem(BaseModel):
    name: str = Field(max_length=120)
    amount: str | int | float | None = ""


class CartIn(BaseModel):
    items: list[CartItem] = Field(max_length=60)


@app.post("/api/cart")
async def api_cart(body: CartIn, x_device: str | None = Header(default=None)):
    """Корзина ВкусВилла с ценами для списка продуктов (рецепт, день или план) — после выбора магазина."""
    dev_id = _dev(x_device)
    if not plans.has_feature(_uid(dev_id), "cart"):
        raise HTTPException(402, "Корзина с ценами — в подписке 💎")
    if web_db.count_today(dev_id, "cart") >= 30:
        raise HTTPException(429, "Слишком много запросов корзины — попробуй завтра.")
    web_db.log_event(dev_id, "cart")
    amounts: dict[str, int] = {}
    for it in body.items:
        if isinstance(it.amount, (int, float)):
            grams = int(it.amount)
        else:
            parsed = amounts_from_recipe_text(f"Продукты:\n- {it.name} — {it.amount}")
            grams = next(iter(parsed.values()), 0)
        if grams > 0:
            key = it.name.strip().lower()
            amounts[key] = amounts.get(key, 0) + grams
    if not amounts:
        return {"text": "", "link": ""}
    priced = await build_priced_basket(amounts, "выбранные продукты")
    if not priced:
        return {"text": "", "link": ""}
    m = re.search(r"\]\((https?://[^)]+)\)", priced)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", "", priced)
    text = re.sub(r"[*_`]", "", text)
    text = "\n".join(ln for ln in text.splitlines() if "Открыть корзину" not in ln)
    return {"text": text.strip(), "link": m.group(1) if m else ""}


class MissingIn(BaseModel):
    ctx: str = Field(pattern="^(recipe|day|week)$")
    items: list[str] = Field(min_length=1, max_length=20)
    text: str = Field("", max_length=20000)     # для рецепта — его текст
    excluded: str = Field("", max_length=500)    # что исключено в запросе (с устройства — на сервере не хранится)


@app.post("/api/missing")
async def api_missing(body: MissingIn, x_device: str | None = Header(default=None)):
    """«Нет каких-то продуктов в магазине»: ИИ заменяет их аналогами или переделывает рецепт / меню на день / план."""
    dev_id = _dev(x_device)
    cid = _cid(dev_id)
    names = missing_mod.clean_missing([i[:80] for i in body.items])
    if not names:
        raise HTTPException(400, "Укажи, каких продуктов нет")
    if body.ctx == "recipe" and not body.text.strip():
        raise HTTPException(400, "Нет текста рецепта")
    if body.ctx == "day" and not day_menu.has_menu(cid):
        raise HTTPException(404, "Меню на день не найдено")
    if body.ctx == "week" and not week_menu.has_plan(cid):
        raise HTTPException(404, "План не найден")
    sp = _spend(dev_id, "missing")

    async def work():
        if body.ctx == "recipe":
            text, terms = await missing_mod.adapt_recipe(body.text, names, body.excluded)
            return {"title": extract_dish_title(text) or "Рецепт", "text": text,
                    "shopping": _mark_home(dev_id, with_amounts(links_for(terms), amounts_from_recipe_text(text), text))}
        if body.ctx == "day":
            await missing_mod.rebuild_day(cid, names)
            terms = day_menu.get_shopping_terms(cid)
            shopping = with_amounts(links_for(terms), day_menu.get_shopping_amounts(cid))
            web_db.kv_set(f"dayshop:{cid}", json.dumps(shopping, ensure_ascii=False))
            return {"ok": True}
        mode = await missing_mod.adjust_plan(cid, names)
        return {"ok": True, "mode": mode}

    return _start_job(dev_id, work, sp)
