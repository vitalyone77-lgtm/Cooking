"""
Автономная веб-версия кухонного помощника (не зависит от Telegram-бота как процесса).
Запуск:  uvicorn web_api:app --host 127.0.0.1 --port 8081
Отдаёт и страницу (/), и API (/api/*), и админку (/admin). Использует то же ядро, что бот:
ai.py, search.py, prompts.py, day_menu.py, week_menu.py, favorites.py, shopping.py, stores/.

Пользователь = устройство: при первом визите браузер получает случайный код и хранит его у себя
(без регистрации). Данные веба лежат отдельно от бота (папка web_data/), поэтому два процесса не
портят друг другу JSON-файлы.
"""
import asyncio
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
from fastapi.responses import FileResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import config
import web_db
import web_pdf
import day_menu
import week_menu
import favorites as fav_mod

# Данные веба — в отдельные файлы (до первого использования модулей)
day_menu.DAY_MENU_FILE = web_db.DATA_DIR / "day_menu.json"
week_menu.WEEK_MENU_FILE = web_db.DATA_DIR / "week_menu.json"
fav_mod.FAVORITES_FILE = web_db.DATA_DIR / "favorites.json"

from ai import generate_recipe
from cuisines import CUISINE_LABELS
from keyboards import MEAL_LABELS
from search import search_recipes, format_results_for_prompt
from shopping import extract_shopping_terms, extract_dish_title
from storage import get_all_users
from stores.links import STORES, DEFAULT_STORES, search_url, short_name
from stores.basket import build_priced_basket

BASE = Path(__file__).parent
app = FastAPI(title="Kitchen", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/uploads", StaticFiles(directory=web_db.DATA_DIR / "uploads"), name="uploads")

APPLIANCES = {"airfryer": "Аэрогриль", "multicooker": "Мультиварка", "oven": "Духовка", "stove": "Плита"}


# ---------------- вспомогательное ----------------

def _dev(x_device: str | None) -> int:
    dev_id = web_db.device_id((x_device or "").strip())
    if dev_id is None:
        raise HTTPException(401, "device")
    return dev_id


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
    appliance: list[str] = []
    meals: list[str] = []

    def to_data(self) -> dict:
        if self.cuisine not in CUISINE_LABELS:
            raise HTTPException(400, "Неизвестный тип питания")
        keys = [a for a in self.appliance if a in APPLIANCES]
        if not keys:
            raise HTTPException(400, "Выбери, в чём готовить")
        return {"cuisine": self.cuisine, "macro_goal": self.macro_goal.strip(), "preferred": self.preferred.strip(),
                "excluded": self.excluded.strip(), "time": self.time, "servings": self.servings,
                "appliance": keys, "appliance_labels": [APPLIANCES[k] for k in keys]}

    def meal_keys(self) -> list[str]:
        keys = [m for m in self.meals if m in MEAL_LABELS]
        if not keys:
            raise HTTPException(400, "Выбери хотя бы один приём пищи")
        return keys


# ---------------- фоновые задачи (генерация долгая) ----------------

JOBS: dict[str, dict] = {}


def _spend(dev_id: int, kind: str):
    if web_db.count_today(dev_id, "gen") >= config.WEB_DAILY_LIMIT:
        raise HTTPException(429, f"Дневной лимит ({config.WEB_DAILY_LIMIT} генераций) исчерпан — приходи завтра 🙂")
    web_db.log_event(dev_id, "gen")
    web_db.log_event(dev_id, kind)


def _start_job(dev_id: int, coro_fn) -> dict:
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

    asyncio.create_task(runner())
    return {"job": job_id}


@app.get("/api/job/{job_id}")
def job(job_id: str, x_device: str | None = Header(default=None)):
    dev_id = _dev(x_device)
    j = JOBS.get(job_id)
    if not j or j["dev"] != dev_id:
        raise HTTPException(404, "Задача не найдена")
    return {k: v for k, v in j.items() if k in ("status", "result", "error")}


# ---------------- устройства и статистика ----------------

_new_dev_log: dict[str, list[float]] = {}


@app.post("/api/device")
def new_device(request: Request):
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[0].strip()
    hist = [t for t in _new_dev_log.get(ip, []) if time.time() - t < 3600]
    if len(hist) >= 20:
        raise HTTPException(429, "Слишком много новых устройств с одного адреса")
    hist.append(time.time())
    _new_dev_log[ip] = hist
    return {"device": web_db.create_device()}


@app.post("/api/visit")
def visit(x_device: str | None = Header(default=None)):
    web_db.log_event(_dev(x_device), "visit")
    return {"ok": True}


@app.get("/api/meta")
def meta():
    return {
        "cuisines": [{"key": k, "label": v} for k, v in CUISINE_LABELS.items()],
        "appliances": [{"key": k, "label": v} for k, v in APPLIANCES.items()],
        "meals": [{"key": k, "label": v} for k, v in MEAL_LABELS.items()],
        "week_days": config.WEEK_MENU_DAYS,
        "limit": config.WEB_DAILY_LIMIT,
    }


# ---------------- генерация ----------------

@app.post("/api/recipe")
async def api_recipe(p: Params, x_device: str | None = Header(default=None)):
    dev_id = _dev(x_device)
    data = p.to_data()
    _spend(dev_id, "recipe")

    async def work():
        query, results = await search_recipes(data)
        text = await generate_recipe(data, query, format_results_for_prompt(results))
        if text.startswith("😔"):
            raise RuntimeError("ИИ сейчас недоступен, попробуй позже")
        text, terms = extract_shopping_terms(text)
        if not text.strip():
            raise RuntimeError("Не получилось составить рецепт под эти параметры")
        return {"title": extract_dish_title(text) or "Рецепт", "text": text, "shopping": links_for(terms)}

    return _start_job(dev_id, work)


@app.post("/api/daymenu")
async def api_daymenu(p: Params, x_device: str | None = Header(default=None)):
    dev_id = _dev(x_device)
    data, meals = p.to_data(), p.meal_keys()
    _spend(dev_id, "daymenu")
    cid = _cid(dev_id)

    async def work():
        summary = await day_menu.generate_day_menu(cid, meals, data)
        shopping = parse_shopping(summary.get("shopping_message", ""))
        web_db.kv_set(f"dayshop:{cid}", json.dumps(shopping, ensure_ascii=False))
        return {"ok": True}

    return _start_job(dev_id, work)


@app.post("/api/week")
async def api_week(p: Params, x_device: str | None = Header(default=None)):
    dev_id = _dev(x_device)
    data, meals = p.to_data(), p.meal_keys()
    _spend(dev_id, "week")
    cid = _cid(dev_id)

    async def work():
        _, shopping_md = await week_menu.start_week_plan(cid, meals, config.WEEK_MENU_DAYS, data)
        info = week_menu.get_basket_info(cid)
        priced = await build_priced_basket(info["inventory_initial"]) if info else None
        if priced:
            week_menu.save_priced_basket(cid, priced)
        return {"ok": True}

    return _start_job(dev_id, work)


@app.post("/api/week/cook")
async def api_week_cook(x_device: str | None = Header(default=None)):
    dev_id = _dev(x_device)
    cid = _cid(dev_id)
    index = week_menu.current_day_first_index(cid)
    if index is None:
        raise HTTPException(400, "План завершён или не найден")
    _spend(dev_id, "weekday")

    async def work():
        slot = await week_menu.ensure_slot(cid, index)
        if slot.get("just_generated"):
            day = slot["day_number"]
            web_db.kv_set(f"weekshop:{cid}:{day}",
                          json.dumps(parse_shopping(slot.get("shopping_message", "")), ensure_ascii=False))
        return {"ok": True}

    return _start_job(dev_id, work)


# ---------------- чтение меню ----------------

def _meal_out(key: str, meal: dict) -> dict:
    return {"key": key, "label": MEAL_LABELS.get(key, key), "title": meal.get("title", ""), "text": meal.get("text", "")}


@app.get("/api/overview")
def overview(x_device: str | None = Header(default=None)):
    cid = _cid(_dev(x_device))
    return {"has_day": day_menu.has_menu(cid), "has_week": week_menu.has_plan(cid),
            "favorites": len(fav_mod.get_favorites(cid))}


@app.get("/api/day")
def day(x_device: str | None = Header(default=None)):
    cid = _cid(_dev(x_device))
    entry = day_menu._load_menu(cid)
    if not entry:
        raise HTTPException(404, "Меню на день ещё не собрано.")
    shop = web_db.kv_get(f"dayshop:{cid}")
    return {"meals": [_meal_out(k, entry["meals"][k]) for k in entry["order"]],
            "shopping": json.loads(shop) if shop else []}


@app.get("/api/week")
def week(x_device: str | None = Header(default=None)):
    cid = _cid(_dev(x_device))
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
                     "extra_shopping": json.loads(extra) if extra else []})
    info = week_menu.get_basket_info(cid)
    items = [{"name": k, "amount": v,
              "links": [{"store": short_name(s), "url": search_url(s, k)} for s in DEFAULT_STORES]}
             for k, v in info["inventory_initial"].items()]
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
    cid = _cid(_dev(x_device))
    return {"items": fav_mod.get_favorites(cid)}


@app.post("/api/favorites")
def favorites_add(f: FavIn, x_device: str | None = Header(default=None)):
    cid = _cid(_dev(x_device))
    if len(fav_mod.get_favorites(cid)) >= 200:
        raise HTTPException(400, "Слишком много сохранённых рецептов (лимит 200)")
    return {"id": fav_mod.add_favorite(cid, f.title, f.text, f.cuisine)}


@app.delete("/api/favorites/{fav_id}")
def favorites_delete(fav_id: str, x_device: str | None = Header(default=None)):
    return {"ok": fav_mod.remove_favorite(_cid(_dev(x_device)), fav_id)}


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
    if web_db.count_today(dev_id, "pdf") >= 200:
        raise HTTPException(429, "Слишком много PDF за сутки — попробуй завтра")
    if sum(len(i.text) for i in body.items) > 600_000:
        raise HTTPException(413, "Слишком большой документ")
    try:
        pdf = web_pdf.build_pdf([i.model_dump() for i in body.items], site=config.WEB_APP_URL, doc_title=body.name)
    except web_pdf.PdfUnavailable as e:
        raise HTTPException(501, f"PDF на сервере недоступен: {e}")
    web_db.log_event(dev_id, "pdf")
    filename = _pdf_filename(body.name or body.items[0].title)
    return Response(
        pdf, media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=\"recipe.pdf\"; filename*=UTF-8''{quote(filename)}",
                 "Cache-Control": "no-store"},
    )


# ---------------- реклама ----------------

@app.get("/api/ad")
def get_ad(slot: str = "top", x_device: str | None = Header(default=None)):
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


def _admin_cookie() -> str:
    key = (config.WEB_SECRET or "x").encode()
    return hmac.new(key, ("admin:" + config.ADMIN_PASSWORD).encode(), hashlib.sha256).hexdigest()


def _need_admin(request: Request):
    if not config.ADMIN_PASSWORD or not hmac.compare_digest(request.cookies.get("adm", "").encode(), _admin_cookie().encode()):
        raise HTTPException(401, "admin")


@app.post("/admin/login")
async def admin_login(request: Request, response: Response):
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[0].strip()
    fails = [t for t in _login_fail.get(ip, []) if time.time() - t < 600]
    if len(fails) >= 5:
        raise HTTPException(429, "Слишком много попыток, подожди 10 минут")
    body = await request.json()
    if not config.ADMIN_PASSWORD or not hmac.compare_digest(str(body.get("password", "")).encode(), config.ADMIN_PASSWORD.encode()):
        fails.append(time.time())
        _login_fail[ip] = fails
        raise HTTPException(401, "Неверный пароль")
    resp = JSONResponse({"ok": True})
    resp.set_cookie("adm", _admin_cookie(), httponly=True, samesite="strict",
                    secure=request.headers.get("x-forwarded-proto") == "https", max_age=30 * 86400)
    return resp


@app.get("/admin/api/stats")
def admin_stats(request: Request):
    _need_admin(request)
    return web_db.stats(tg_users=len(get_all_users()))


@app.post("/admin/api/ads")
async def admin_ad_add(request: Request, slot: str = FForm(...), title: str = FForm(""), url: str = FForm(...),
                       image: UploadFile = File(...)):
    _need_admin(request)
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
    _need_admin(request)
    with web_db.db() as c:
        c.execute("UPDATE ads SET active=1-active WHERE id=?", (ad_id,))
    return {"ok": True}


@app.delete("/admin/api/ads/{ad_id}")
def admin_ad_delete(ad_id: int, request: Request):
    _need_admin(request)
    with web_db.db() as c:
        row = c.execute("SELECT image FROM ads WHERE id=?", (ad_id,)).fetchone()
        c.execute("DELETE FROM ads WHERE id=?", (ad_id,))
    if row:
        (web_db.DATA_DIR / "uploads" / row["image"]).unlink(missing_ok=True)
    return {"ok": True}


# ---------------- страницы ----------------

@app.get("/")
def index():
    return FileResponse(BASE / "web" / "index.html", media_type="text/html; charset=utf-8")


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
