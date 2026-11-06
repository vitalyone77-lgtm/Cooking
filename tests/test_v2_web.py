"""Сайт: вход через Telegram, общий аккаунт с ботом (лимиты, избранное, профиль), полка, подписка, защита."""
import json
import os
import sys
import time

PROJ = "/tmp/ct"
os.chdir(PROJ)
sys.path.insert(0, PROJ)
for f in ("usage_w2.db", "accounts_w2.db"):
    if os.path.exists(f):
        os.remove(f)
os.environ["USAGE_DB"] = PROJ + "/usage_w2.db"
os.environ["ACCOUNTS_DB"] = PROJ + "/accounts_w2.db"
os.environ["WEB_DATA_DIR"] = "web_data_w2"
import shutil
shutil.rmtree(PROJ + "/web_data_w2", ignore_errors=True)
os.makedirs(PROJ + "/web_data_w2", exist_ok=True)
# старое избранное сайта (JSON): устройство 1 — ключ 10^12+1; должно переехать в общую базу
json.dump({str(10**12 + 1): [{"id": "111", "title": "Старый борщ", "text": "Борщ", "cuisine": "classic"}]},
          open(PROJ + "/web_data_w2/favorites.json", "w", encoding="utf-8"), ensure_ascii=False)

from fastapi.testclient import TestClient
import config
import accounts
import usage
import plans
import profile_ctx
import web_api
import favorites as fav

fails = []


def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{x}]"))
    if not c:
        fails.append(n)


TG = 777000500
seen_profile = []


async def fake_search(d):
    return "q", []


async def fake_gen(data, q, r):
    seen_profile.append(data.get("excluded"))
    return "🍽 Суп\n\nПродукты:\n- Масло подсолнечное — 10 мл\n- Картофель — 300 г\n\nИнструкция:\n1. Свари.\n\nПРОДУКТЫ_СПИСОК: масло подсолнечное, картофель"
web_api.search_recipes = fake_search
web_api.generate_recipe = fake_gen

P = {"cuisine": "classic", "appliance": ["stove"], "time": "30", "servings": 1}


def wait_job(cl, h, j):
    for _ in range(50):
        s = cl.get("/api/job/" + j, headers=h).json()
        if s["status"] != "running":
            return s
        time.sleep(0.1)
    return s


with TestClient(web_api.app) as cl:
    m = cl.get("/api/meta").json()
    check("сайт: типы питания в том же порядке и с теми же подписями, что в боте",
          [c["label"] for c in m["cuisines"]][:4] == ["🍲 Классическая", "⚖️ Сбалансированная", "🏃 Спорт: кардио", "💪 Спорт: сила"], str(m["cuisines"][:4]))
    dev1 = cl.post("/api/device").json()["device"]       # id устройства 1 (первое в новой базе)
    h1 = {"X-Device": dev1}
    favs = cl.get("/api/favorites", headers=h1).json()["items"]
    check("старое избранное сайта перенесено в общую базу", [f["title"] for f in favs] == ["Старый борщ"], str(favs))
    me = cl.get("/api/me", headers=h1).json()
    check("без входа: linked=false", me["linked"] is False)

    # вход через Telegram: сайт → бот
    st = cl.post("/api/link/start", headers=h1).json()
    check("ссылка входа ведёт в бота с одноразовым кодом", st["url"].startswith(f"https://t.me/{config.BOT_USERNAME}?start=link_"))
    check("до подтверждения — не вошёл", cl.get("/api/link/status", params={"code": st["code"]}, headers=h1).json()["linked"] is False)
    accounts.use_link_code(st["code"], TG)                  # пользователь нажал Start в боте
    check("после подтверждения — вошёл", cl.get("/api/link/status", params={"code": st["code"]}, headers=h1).json()["linked"] is True)
    me = cl.get("/api/me", headers=h1).json()
    check("профиль: linked, память доступна (подписка выключена)", me["linked"] and me["memory"])
    check("избранное, сделанное без входа, перешло в аккаунт", [f["title"] for f in fav.get_favorites(TG)] == ["Старый борщ"])
    fav.add_favorite(TG, "Из бота", "текст", "classic")
    check("избранное общее с ботом", {f["title"] for f in cl.get("/api/favorites", headers=h1).json()["items"]} == {"Старый борщ", "Из бота"})
    check("чужой код статуса не показывает", cl.get("/api/link/status", params={"code": st["code"]},
                                                    headers={"X-Device": cl.post("/api/device").json()["device"]}).json()["linked"] is False)

    # полка с сайта; аллергии в базе больше не хранятся — исключения приходят с самим запросом
    check("данные профиля без согласия не сохраняются", cl.put("/api/profile", json={"never": "орехи"}, headers=h1).status_code == 403)
    check("полка без галочки в профиле не работает", cl.post("/api/shelf", json={"item": "масло"}, headers=h1).status_code == 403)
    accounts.consent_set(TG)
    accounts.goals_set(TG, feats=["shelf"])
    check("после галочки полка работает", cl.post("/api/shelf", json={"item": "масло подсолнечное"}, headers=h1).status_code == 200)
    P = {**P, "excluded": "орехи"}
    j = cl.post("/api/recipe", json=P, headers=h1).json()
    res = wait_job(cl, h1, j["job"])
    check("генерация на сайте получила исключения запроса", seen_profile and seen_profile[-1] == "орехи", str(seen_profile))
    shop = res.get("result", {}).get("shopping", [])
    check("в списке покупок масло с полки помечено «есть дома»", any(i["name"].lower().startswith("масло") and i.get("home") for i in shop)
          and any(i["name"].lower().startswith("картоф") and not i.get("home") for i in shop), str(shop))
    check("лимит общий с ботом: подбор на сайте записан на аккаунт", usage.used(TG, "recipe") == 1)
    for _ in range(config.WEB_LIMIT_RECIPE - 1):
        usage.try_spend(TG, "recipe", "bot")
    r = cl.post("/api/recipe", json=P, headers=h1)
    check("лимит, потраченный в боте, действует и на сайте", r.status_code == 429, r.text)

    # вход по ссылке из бота
    dev2 = cl.post("/api/device").json()["device"]
    h2 = {"X-Device": dev2}
    code = accounts.new_login_code(TG)
    check("вход по ссылке из бота", cl.post("/api/link/claim", json={"code": code}, headers=h2).status_code == 200
          and cl.get("/api/me", headers=h2).json()["linked"])
    check("повторно ссылка не работает", cl.post("/api/link/claim", json={"code": code}, headers=h2).status_code == 400)
    cl.post("/api/unlink", headers=h2)
    check("выход: устройство снова без входа", cl.get("/api/me", headers=h2).json()["linked"] is False)
    check("без входа полка недоступна", cl.post("/api/shelf", json={"item": "x"}, headers=h2).status_code == 403)

    # подписка включена
    config.PAYWALL_ENABLED = True
    check("PDF без подписки — 402", cl.post("/api/pdf", json={"items": [{"title": "a", "text": "b"}]}, headers=h1).status_code == 402)
    check("полка без подписки — закрыта", cl.post("/api/shelf", json={"item": "x"}, headers=h1).status_code == 402
          and cl.get("/api/me", headers=h1).json()["memory"] is False)
    accounts.grant_days(TG, 30, "payment")
    check("с подпиской — PDF доступен", cl.post("/api/pdf", json={"items": [{"title": "a", "text": "b"}]}, headers=h1).status_code in (200, 501))
    with web_api.web_db.db() as c:
        c.execute("INSERT INTO ads(slot,title,image,url,active,created) VALUES('top','t','x.png','https://e.x',1,0)")
    check("с подпиской — без рекламы", cl.get("/api/ad", params={"slot": "top"}, headers=h1).json()["ad"] is None
          and cl.get("/api/ad", params={"slot": "top"}, headers=h2).json()["ad"] is not None)
    config.PAYWALL_ENABLED = False

    # защита: лимит на IP для сайта без входа, частота запросов
    config.WEB_IP_DAILY_GENS = 2
    codes = []
    for i in range(3):
        d = cl.post("/api/device", headers={"X-Real-IP": "5.5.5.5"}).json()["device"]
        codes.append(cl.post("/api/recipe", json=P, headers={"X-Device": d, "X-Real-IP": "5.5.5.5"}).status_code)
        time.sleep(0.3)
    check("с одного IP новые устройства не дают лишних подборов", codes[:2] == [200, 200] and codes[2] == 429, str(codes))
    web_api.API_RATE_PER_MIN = 30
    rs = [cl.get("/api/meta", headers={"X-Real-IP": "6.6.6.6"}).status_code for _ in range(35)]
    check("частые запросы с одного IP — 429", rs.count(429) == 5 and rs[:30].count(200) == 30, str(rs))
    check("…другой IP при этом работает", cl.get("/api/meta", headers={"X-Real-IP": "7.7.7.7"}).status_code == 200)
    web_api.API_RATE_PER_MIN = 240
    page = cl.get("/").text
    check("страница: вкладка «Профиль» и полоска любимых рецептов", 'data-tab="profile"' in page and "favStrip" in page and "Любимые рецепты" in page)

print("\nИТОГ:", "ВСЁ ОК" if not fails else "ПРОВАЛЕНО: " + ", ".join(fails))
