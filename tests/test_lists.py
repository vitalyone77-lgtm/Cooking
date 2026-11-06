"""Список по ссылке, оценка блюда у ИИ, дневник на сайте, новый профиль, график с БЖУ, меню бота."""
import asyncio
import json
import os
import sys
import time

PROJ = "/tmp/ct"
os.chdir(PROJ)
sys.path.insert(0, PROJ)
for f in ("usage_ls.db", "accounts_ls.db", "accounts_ls.db-wal", "accounts_ls.db-shm"):
    if os.path.exists(f):
        os.remove(f)
os.environ["USAGE_DB"] = PROJ + "/usage_ls.db"
os.environ["ACCOUNTS_DB"] = PROJ + "/accounts_ls.db"
os.environ["WEB_DATA_DIR"] = "web_data_ls"

from fastapi.testclient import TestClient
import accounts
import ai
import charts
import config
import food_ai
import foods
import keyboards as K
import plans
import shoplists
import shopping
import web_api
import web_db

fails = []


def check(name, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f" :: {extra}"))
    if not ok:
        fails.append(name)


cl = TestClient(web_api.app)
TG = 7001
d = cl.post("/api/device").json()["device"]
DID = web_db.device_id(d)
H = {"X-Device": d}

# ---------- меню бота ----------
rows = [[b.text for b in r] for r in K.cuisine_kb(fridge=True).inline_keyboard]
check("аюрведа и рецепт по фото в одной строке", ["🍛 Аюрведа", "📷 По фото продуктов"] in rows, str(rows))
check("под рецептом кнопка «Продукты и магазины»", any("Продукты и магазины" in b.text for r in K.recipe_result_kb().inline_keyboard for b in r))

# ---------- количества ----------
TXT = "Плов\n\nПродукты:\n- рис — 300 г\n- масло — 2 ст. л.\n- соль — по вкусу\n- морковь — 1 шт (≈ 70 г)\n\nШаги:\n1. Варить"
am = shopping.item_amounts(["рис", "масло", "соль", "морковь", "укроп"], shopping.amounts_from_recipe_text(TXT), TXT)
check("количество: граммы, ложки, по вкусу, пусто если нет", am[0] == "300 г" and "ст" in am[1] and am[2] == "по вкусу" and am[4] == "", str(am))

# ---------- список по ссылке ----------
lid = shoplists.create("u1", "Плов <b>", [{"name": "рис", "amount": "300 г"}, {"name": "масло", "amount": ""}, {"name": ""}], [1])
rec = shoplists.get(lid)
check("список: пустые продукты отброшены, отметка сохранена", len(rec["items"]) == 2 and rec["done"] == [1], str(rec))
check("список: отметить и снять", shoplists.toggle(lid, 0, True) == [0, 1] and shoplists.toggle(lid, 1, False) == [0])
check("список: чужой номер продукта и чужой список не ломают", shoplists.toggle(lid, 99, True) is None and shoplists.toggle("x" * 12, 0, True) is None and shoplists.get("../etc") is None)
try:
    shoplists.create("u1", "t", [])
    ok = False
except ValueError:
    ok = True
check("список: пустой не создаётся", ok)
try:
    for _ in range(shoplists.MAX_NEW_PER_HOUR + 1):
        shoplists.create("u2", "t", [{"name": "a"}])
    ok = False
except ValueError:
    ok = True
check("список: лимит в час", ok)
accounts._q("UPDATE shop_lists SET ts=? WHERE id=?", (time.time() - shoplists.TTL - 5, lid))
check("список: через 30 дней устаревает", shoplists.get(lid) is None)

r = cl.post("/api/list", json={"title": "<script>alert(1)</script>", "items": [{"name": "<img src=x onerror=1>", "amount": "1 шт"}, {"name": "соль"}]}, headers=H)
check("сайт: список создаётся", r.status_code == 200 and "/l/" in r.json()["url"], r.text)
lid = r.json()["id"]
pg = cl.get("/l/" + lid)
check("страница списка без входа, заголовок экранирован, закрыта от поиска", pg.status_code == 200 and "<script>alert" not in pg.text and "&lt;script&gt;" in pg.text and "noindex" in pg.text, pg.text[:200])
check("страница списка отвечает на HEAD", cl.head("/l/" + lid).status_code == 200)
check("название продукта не вставлено в HTML (рисуется как текст)", "onerror" not in pg.text)
t = cl.post(f"/api/list/{lid}/toggle", json={"i": 1, "on": True})
check("галочка без входа и общая", t.status_code == 200 and cl.get(f"/api/list/{lid}").json()["done"] == [1], t.text)
check("несуществующий список: 404", cl.get("/l/aaaaaaaaaaaa").status_code == 404 and cl.post("/api/list/aaaaaaaaaaaa/toggle", json={"i": 0, "on": True}).status_code == 404)
check("удаление данных стирает и списки", accounts.delete_my_data("u2")["shop_lists"] > 0)

# ---------- поиск ----------
check("поиск: служебные слова не мешают", foods.search("суп из курицы") and foods.search("суп из курицы") == foods.search("суп курицы"))

# ---------- оценка ИИ ----------
GOOD = json.dumps({"food": True, "name": "Шаурма с курицей", "grams": 300, "label": "1 шт, 300 г", "kcal100": 210, "protein100": 11, "fat100": 10, "carb100": 18})
calls = []


async def fake_llm(messages, max_tokens=3000):
    calls.append(messages)
    return fake_llm.answer


fake_llm.answer = GOOD
ai.call_llm_with_fallback = fake_llm
food, why = asyncio.run(food_ai.estimate("u3", "шаурма по-домашнему"))
check("ИИ: оценка получена и готова для записи", food and food["id"].startswith("a") and food["ai"] and foods.nutrition(food, 300)["kcal"] == 630, str((food, why)))
n = len(calls)
food2, _ = asyncio.run(food_ai.estimate("u4", "шаурма  ПО-ДОМАШНЕМУ"))
check("ИИ: повторный запрос из памяти, без вопроса к ИИ", food2 and food2["id"] == food["id"] and len(calls) == n)
check("ИИ: найти по номеру", food_ai.lookup(food["id"])["name"] == "Шаурма с курицей" and food_ai.lookup("a999999") is None and food_ai.lookup("nope") is None)
check("ИИ: не еда, чушь и невозможные числа отвергаются", food_ai.parse('{"food": false}', "x") is None and food_ai.parse("не json", "x") is None
      and food_ai.parse('{"food":true,"name":"a","grams":100,"kcal100":5000,"protein100":1,"fat100":1,"carb100":1}', "x") is None
      and food_ai.parse('{"food":true,"name":"a","grams":100,"kcal100":200,"protein100":90,"fat100":90,"carb100":1}', "x") is None)
p = food_ai.parse('{"food":true,"name":"купи на http://spam.ru","grams":100,"kcal100":200,"protein100":10,"fat100":5,"carb100":20,"label":"порция <b>"}', "вареники")
check("ИИ: ссылка в названии заменяется запросом, разметка вырезается", p and p["name"] == "вареники" and "<" not in p["label"], str(p))
fake_llm.answer = "мусор"
f3, why3 = asyncio.run(food_ai.estimate("u3", "нечто странное"))
check("ИИ: плохой ответ — понятная причина и совет", f3 is None and "калориями" in why3, why3)
fake_llm.answer = GOOD
for i in range(food_ai.DAILY_LIMIT):
    asyncio.run(food_ai.estimate("u5", f"блюдо{i}x"))
n = len(calls)
f4, why4 = asyncio.run(food_ai.estimate("u5", "ещё одно блюдо"))
check("ИИ: дневной лимит на человека", f4 is None and "закончились" in why4 and len(calls) == n, why4)


async def boom(messages, max_tokens=3000):
    raise RuntimeError("провайдер упал: секрет")


ai.call_llm_with_fallback = boom
f5, why5 = asyncio.run(food_ai.estimate("u6", "ещё блюдо с ошибкой"))
check("ИИ недоступен: без падения и без текста ошибки", f5 is None and "секрет" not in why5, why5)
ai.call_llm_with_fallback = fake_llm

# ---------- график ----------
series = accounts.diary_series("nobody", 30)
for i, day in enumerate(series):
    if i % 3:
        day.update(kcal=2000, protein=100, fat=70, carb=240, n=3)
series[4].update(kcal=1500, protein=0, fat=0, carb=0, n=1)
sh = charts.macro_shares(series)
check("доли БЖУ считаются в калориях и дают около 100%", sh and 98 <= sum(sh.values()) <= 102 and sh["carb"] > sh["protein"], str(sh))
check("нет записей — долей нет", charts.macro_shares(accounts.diary_series("nobody", 7)) is None)
segs = charts._segments({"kcal": 1000, "protein": 50, "fat": 20, "carb": 100})
check("столбик: сумма частей равна калориям", abs(sum(v for _, v in segs) - 1000) < 1, str(segs))
segs = charts._segments({"kcal": 1000, "protein": 10, "fat": 0, "carb": 10})
check("столбик: часть без БЖУ отдельным цветом", segs[-1][0] == charts.NO_MACRO and abs(sum(v for _, v in segs) - 1000) < 1, str(segs))
check("столбик: совсем без БЖУ", charts._segments({"kcal": 500, "protein": 0, "fat": 0, "carb": 0}) == [(charts.NO_MACRO, 500)])
w = [{"day": series[i]["day"], "kg": 80 - i * .1} for i in range(0, 30, 5)]
for args in ((series, 2300, w), (series, None, []), (accounts.diary_series("nobody", 7), None, None), (accounts.diary_series("nobody", 90), 2000, w)):
    check("график рисуется", charts.diary_chart(*args)[:4] == b"\x89PNG")

# ---------- сайт: дневник ----------
check("без входа дневник закрыт", cl.get("/api/diary", headers=H).status_code == 403)
accounts.claim_login_code(accounts.new_login_code(TG), DID)
check("дневник выключен: тоже закрыт", cl.get("/api/diary", headers=H).status_code == 403 and cl.get("/api/foods?q=борщ", headers=H).status_code == 403)
accounts.consent_set(TG)
accounts.goals_set(TG, feats=["diary"])
me = cl.get("/api/me", headers=H).json()
check("в /api/me видно, что дневник включён", me.get("diary_on") is True, str(me)[:200])
dj = cl.get("/api/diary?days=7", headers=H).json()
check("дневник: пустой день и 7 дней графика", dj["today"]["kcal"] == 0 and len(dj["series"]) == 7 and dj["target"] is None and dj["shares"] is None)
check("чужой период превращается в 30 дней", len(cl.get("/api/diary?days=5000", headers=H).json()["series"]) == 30)
f = cl.get("/api/foods?q=борщ", headers=H).json()["items"]
check("поиск в справочнике", f and "порщ" not in f[0]["name"] and f[0]["portion_kcal"] > 0, str(f)[:200])
r = cl.post("/api/diary", json={"food": f[0]["id"], "grams": 300}, headers=H)
check("запись из справочника", r.status_code == 200 and r.json()["kcal"] > 0, r.text)
check("плохой вес и чужой продукт отклоняются", cl.post("/api/diary", json={"food": f[0]["id"], "grams": 99999}, headers=H).status_code == 400
      and cl.post("/api/diary", json={"food": "nope", "grams": 100}, headers=H).status_code == 400)
check("запись вручную «пицца 450»", cl.post("/api/diary", json={"title": "пицца", "kcal": "450"}, headers=H).json().get("kcal") == 450)
check("неверные калории отклоняются", cl.post("/api/diary", json={"title": "x", "kcal": "99999"}, headers=H).status_code == 400 and cl.post("/api/diary", json={"title": "", "kcal": "100"}, headers=H).status_code == 400)
rt = """Плов

Продукты:
- рис — 300 г

Калорийность: 500 ккал, белки 20 г, жиры 15 г, углеводы 60 г
"""
import cards
pr = cards.parse("Плов", rt)
r = cl.post("/api/diary/recipe", json={"title": "Плов", "text": rt}, headers=H)
check("«Съел порцию» с сайта", r.status_code == 200 if pr["kcal"] else r.status_code == 400, r.text + str(pr))
check("рецепт без калорий: понятный отказ", cl.post("/api/diary/recipe", json={"title": "Суп", "text": "просто текст"}, headers=H).status_code == 400)
dj = cl.get("/api/diary?days=7", headers=H).json()
check("итог дня и доли БЖУ на сайте", dj["today"]["kcal"] >= 450 and len(dj["today"]["items"]) >= 2 and dj["shares"], str(dj["today"])[:200])
check("частые продукты для быстрой записи", dj["frequent"] and dj["frequent"][0]["id"] == f[0]["id"], str(dj["frequent"])[:200])
eid = dj["today"]["items"][-1]["id"]
check("удаление записи", cl.delete(f"/api/diary/{eid}", headers=H).json()["ok"] is True and cl.delete(f"/api/diary/{eid}", headers=H).json()["ok"] is False)
other = cl.post("/api/device").json()["device"]
accounts_other = web_db.device_id(other)
check("чужую запись через другое устройство не прочитать", cl.get("/api/diary", headers={"X-Device": other}).status_code == 403)
e = cl.post("/api/energy", json={"sex": "m", "age": 38, "height": 175, "weight": 82, "activity": "sport"}, headers=H)
check("расчёт расхода на сайте", e.status_code == 200 and 2000 < e.json()["tdee"] < 3500, e.text)
check("расчёт: неверные данные", cl.post("/api/energy", json={"sex": "m", "age": 3, "height": 175, "weight": 82, "activity": "sport"}, headers=H).status_code == 400)
check("после расчёта цель показывается как расход, цель важнее", cl.get("/api/diary", headers=H).json()["target"]["title"] == "расход")
accounts.goals_set(TG, kcal=2100)
check("своя цель важнее расхода", cl.get("/api/diary", headers=H).json()["target"] == {"kcal": 2100, "title": "цель"})
fake_llm.answer = json.dumps({"food": True, "name": "Хинкали", "grams": 250, "label": "5 шт", "kcal100": 230, "protein100": 10, "fat100": 9, "carb100": 26})
a = cl.get("/api/foods?ai=1&q=" + "хинкали с бараниной из печи", headers=H).json()
check("сайт: нет в справочнике — отвечает ИИ", a["items"] and a["items"][0]["ai"] and a["items"][0]["id"].startswith("a"), str(a))
check("запись оценки ИИ", cl.post("/api/diary", json={"food": a["items"][0]["id"], "grams": 250}, headers=H).status_code == 200)
b = cl.get("/api/foods?ai=2&q=борщ", headers=H).json()
check("«Нет нужного»: ИИ спрашивается даже при находках", b["items"] and b["items"][0]["ai"], str(b))

# ---------- сайт: разметка ----------
html = open("web/index.html", encoding="utf-8").read()
check("есть вкладка настроек, дневник и функция дневника", "settings:viewSettings" in html and 'data-feat="diary"' in html and "function diaryCards" in html)
vp = html[html.index("function viewProfile("):html.index("function viewSettings(")]
vs = html[html.index("function viewSettings("):html.index("// код входа создаём заранее")]
check("оплата и функции не в профиле, а в настройках", "data-pay" not in vp and "data-feat" not in vp and "data-unlink" not in vp and "data-pay" in vs and "data-unlink" in vs and "data-pf-save" not in vp, "")
check("полка раскрывающаяся и закрыта по умолчанию", 'accordion("🏠 Моя полка"' in vp and "</div>',false);" in vp)
check("цели в настройках", "function weightCard" in html and 'data-pf-save="wgoal"' in html[html.index("function settingsCards"):html.index("// ---- дневник питания: наверху")])
check("в магазинах появился «Список»", 'data-store-pick="__list"' in html and "data-list-share" in html)
check("«Съел порцию» только при включённом дневнике", "ME.diary_on&&!noSave" in html)

# ---------- страница оплаты и возврата ----------
config.SELLER_NAME, config.SELLER_INN, config.SELLER_PHONE, config.SELLER_EMAIL = "Иванов <b>", "123456789012", "", "a@b.ru"
pg = cl.get("/oplata")
check("страница оплаты открыта без входа и отвечает на HEAD", pg.status_code == 200 and cl.head("/oplata").status_code == 200)
check("на ней цены в рублях, реквизиты, отказ и возврат, без адреса", "₽" in pg.text and "123456789012" in pg.text and "a@b.ru" in pg.text and "Автопродления нет" in pg.text and "в любой момент" in pg.text and "PayAnyWay" not in pg.text and "Продамус" not in pg.text and "Адрес" not in pg.text)
check("реквизиты экранированы, пустой телефон не показан", "Иванов &lt;b&gt;" in pg.text and "<th>Телефон" not in pg.text)
check("служебный тариф за 10 ₽ в ценах не показан", "Проверка оплаты" not in pg.text)
check("реквизиты в профиле", cl.get("/api/me", headers=H).json()["seller"]["inn"] == "123456789012")
check("ссылка на страницу на всех экранах и в настройках", html.count('href="/oplata"') >= 2)

# ---------- фото на сайте ----------
import io
import vision
plans_feature = plans.feature_on
plans.feature_on = lambda u, n: False
check("фото: функция выключена — 403", cl.post("/api/photo", files={"image": ("a.jpg", b"123", "image/jpeg")}, headers=H).status_code == 403)
plans.feature_on = lambda u, n: True
check("фото: не картинка — 400", cl.post("/api/photo", files={"image": ("a.txt", b"123", "text/plain")}, headers=H).status_code == 400)


async def _ok_vision(images):
    return ["курица", "рис"]


async def _empty_vision(images):
    return []


async def _bad_vision(images):
    raise vision.VisionError("x")


vision.photo_items = _ok_vision
r = cl.post("/api/photo", files={"image": ("a.jpg", b"123", "image/jpeg")}, headers=H)
check("фото: продукты вернулись", r.status_code == 200 and r.json()["items"] == ["курица", "рис"], r.text)
vision.photo_items = _empty_vision
check("фото: нет продуктов — понятный отказ", cl.post("/api/photo", files={"image": ("a.jpg", b"123", "image/jpeg")}, headers=H).status_code == 422)
vision.photo_items = _bad_vision
check("фото: сбой — понятный отказ без падения", cl.post("/api/photo", files={"image": ("a.jpg", b"123", "image/jpeg")}, headers=H).status_code == 502)
plans.feature_on = plans_feature
check("кнопка фото на сайте и ровные ряды", "data-photo-pick" in html and "chips[data-name=cuisine]" in html)
check("один график: вес на графике калорий", "function dyChart(series,target,weights)" in html and "dyChart(d.series,tg,ws)" in html)

# ---------- вход через ВК переходом ----------
import urllib.parse as _up


class _R:
    def __init__(self, d):
        self._d = d

    def json(self):
        return self._d


vk_calls = []


def _vk_post(url, data=None, timeout=None):
    vk_calls.append((url, data))
    if url.endswith("/oauth2/auth"):
        return _R({"access_token": "токен-" + "x" * 20, "state": data["state"]})
    return _R({"user": {"user_id": "777001"}})


_real_post = web_api.httpx.post
web_api.httpx.post = _vk_post
sc = TestClient(web_api.app, base_url="https://testserver", follow_redirects=False)
d2 = sc.post("/api/device").json()["device"]
H2 = {"X-Device": d2}
st = sc.post("/api/vk/start", headers=H2)
q = _up.parse_qs(_up.urlparse(st.json()["url"]).query)
check("ссылка на ВК с защитой PKCE и адресом возврата", q["code_challenge_method"] == ["s256"] and q["redirect_uri"][0].endswith("/api/vk/callback") and q["state"] == [st.json()["state"]], str(q))
check("секретное значение не уходит в браузер", "code_verifier" not in st.text and accounts.sess_get("vkst:" + st.json()["state"])["v"] not in st.text)
check("чужой или неизвестный код не проходит", "устарела" in sc.get("/api/vk/callback?code=c&state=nope&device_id=d").text)
r = sc.get(f"/api/vk/callback?code=c1&state={st.json()['state']}&device_id=d1")
check("тот же браузер: вход сразу и переход на сайт", r.status_code == 303 and r.headers["location"] == "/?vk=ok" and web_db.device_id(d2) and accounts.tg_of_device(web_db.device_id(d2)) == accounts.VK_OFFSET + 777001, r.text[:200])
ex = [c for c in vk_calls if c[0].endswith("/oauth2/auth")][-1][1]
check("обмен кода с проверочным значением", ex["code"] == "c1" and ex["device_id"] == "d1" and len(ex["code_verifier"]) > 40 and ex["grant_type"] == "authorization_code")
check("вкладка узнаёт, что вход завершён", sc.get("/api/vk/status?state=" + st.json()["state"], headers=H2).json()["linked"] is True)
n = len(vk_calls)
check("тот же код второй раз не работает", "устарела" in sc.get(f"/api/vk/callback?code=c1&state={st.json()['state']}&device_id=d1").text and len(vk_calls) == n)
d3 = cl.post("/api/device").json()["device"]
H3 = {"X-Device": d3}
st3 = cl.post("/api/vk/start", headers=H3).json()["state"]
other = TestClient(web_api.app, base_url="https://testserver", follow_redirects=False)
r = other.get(f"/api/vk/callback?code=c2&state={st3}&device_id=d2")
check("другое окно или чужая ссылка: нужно подтверждение, вход ещё не выполнен", "Подтвердите вход" in r.text and not accounts.tg_of_device(web_db.device_id(d3)), r.text[:200])
check("статус чужому устройству не виден", other.get("/api/vk/status?state=" + st3, headers=H2).json()["linked"] is False)
r = other.post("/api/vk/confirm", data={"state": st3})
check("после подтверждения вход выполнен, вкладка узнаёт", "Вход выполнен" in r.text and accounts.tg_of_device(web_db.device_id(d3)) and cl.get("/api/vk/status?state=" + st3, headers=H3).json()["linked"] is True, r.text[:200])
check("отказ ВК: понятная страница", "не выполнен" in sc.get("/api/vk/callback?error=access_denied&state=x").text)


def _vk_bad(url, data=None, timeout=None):
    return _R({"error": "invalid"})


web_api.httpx.post = _vk_bad
st4 = sc.post("/api/vk/start", headers=H3).json()["state"]
check("ВК не выдал токен: понятная страница, не падение", "не выполнен" in sc.get(f"/api/vk/callback?code=c&state={st4}&device_id=d").text)
web_api.httpx.post = _real_post
check("«По фото» в типах питания, цель без «меняются редко»", 'chip alt" data-photo-pick' in html and "меняются редко" not in html)

# ---------- свой ориентир БЖУ ----------
accounts.goals_set(TG, protein=120, fat=70, carb=230)
check("цель в граммах приходит в дневник", cl.get("/api/diary", headers=H).json()["goal_grams"] == {"protein": 120, "fat": 70, "carb": 230})
check("ориентир: общий, пока свой не задан", "AMDR" in cl.get("/api/diary", headers=H).json()["reference"])
check("ориентир: сумма не 100 отклоняется", cl.post("/api/macro-ref", json={"protein": 50, "fat": 50, "carb": 50}, headers=H).status_code == 400)
check("ориентир: неполный и не числа отклоняются", cl.post("/api/macro-ref", json={"protein": 50}, headers=H).status_code == 400 and cl.post("/api/macro-ref", json={"protein": "а", "fat": 1, "carb": 1}, headers=H).status_code == 400)
check("ориентир: сохраняется и показывается вместо общего", cl.post("/api/macro-ref", json={"protein": "25", "fat": "25,5", "carb": "49.5"}, headers=H).status_code == 200 and "Ваш ориентир: белки 25%, жиры 25.5%" in cl.get("/api/diary", headers=H).json()["reference"])
check("ориентир: есть в настройках и строка для спортсменов", cl.get("/api/profile", headers=H).json()["my_ref"]["protein"] == 25 and "Спортсменам" in cl.get("/api/diary", headers=H).json()["athlete_note"])
check("график рисуется со своим ориентиром", charts.diary_chart(series, 2000, [], {"protein": 25, "fat": 25, "carb": 50})[:4] == b"\x89PNG")
check("ориентир: пустые поля убирают", cl.post("/api/macro-ref", json={"protein": "", "fat": "", "carb": ""}, headers=H).json()["my_ref"] is None and "AMDR" in cl.get("/api/diary", headers=H).json()["reference"])
check("ориентир стирается вместе с данными", accounts.macro_ref_set("u9", 25, 25, 50) and accounts.delete_my_data("u9")["macro_ref"] == 1)
import config as _c
check("вход ВК идёт через id.vk.ru", _c.VK_ID_API_URL == "https://id.vk.ru" or os.getenv("VK_ID_API_URL"))

# ---------- вошёл через ВК: привязать Telegram ----------
me2 = sc.get("/api/me", headers=H2).json()
check("вошёл через ВК: сайт знает способ входа", me2["via"] == "vk" and me2["linked"] is True, str(me2)[:200])
lk = sc.post("/api/link/start", headers=H2)
check("вошёл через ВК: ссылка привязки Telegram создаётся", lk.status_code == 200 and "t.me/" in lk.json()["url"], lk.text[:200])
vs2 = html[html.index("function viewSettings("):html.index("// код входа создаём заранее")]
check("в настройках есть кнопка «Привязать Telegram»", "Привязать Telegram" in vs2 and 'm.via==="vk"?linkCode()' in vs2)
check("только ВК: ещё не объединён", me2["both_linked"] is False)
accounts.claim_login_code(accounts.new_login_code(7002), web_db.device_id(d2))
me3 = sc.get("/api/me", headers=H2).json()
check("привязал Telegram: профиль общий, сайт это знает", me3["via"] == "tg" and me3["both_linked"] is True, str(me3)[:200])
d5 = sc.post("/api/device").json()["device"]
accounts.link_device_vk(web_db.device_id(d5), 777001)
me5 = sc.get("/api/me", headers={"X-Device": d5}).json()
check("снова вход через ВК: тот же профиль и пометка, что привязаны оба", me5["both_linked"] is True and accounts.tg_of_device(web_db.device_id(d5)) == 7002, str(me5)[:200])
check("в настройках написано, что оба привязаны", "уже привязаны: профиль общий" in html)
check("кнопка «уже есть» вместо «есть дома» и только при полке", "🏠 уже есть" in html and "ME.shelf_on?'<button class=\"chip tiny\" data-home" in html)

# ---------- магазины ----------
from stores import prefs as _sp
_sp.PREFS_FILE = __import__("pathlib").Path("/tmp/ct/store_prefs_test.json")
if _sp.PREFS_FILE.exists():
    _sp.PREFS_FILE.unlink()
ms = cl.get("/api/me", headers=H).json()["stores"]
check("магазины: по умолчанию все", len(ms) == 5 and all(x["on"] for x in ms), str(ms))
r = cl.post("/api/stores", json={"key": "lavka"}, headers=H).json()["stores"]
check("магазины: можно убрать", [x["on"] for x in r if x["key"] == "lavka"] == [False])
check("магазины: чужой ключ отклоняется", cl.post("/api/stores", json={"key": "nope"}, headers=H).status_code == 400)
for k in ("fiveka", "vkusvill", "perekrestok", "kuper"):
    r = cl.post("/api/stores", json={"key": k}, headers=H).json()["stores"]
check("магазины: последний остаётся", sum(x["on"] for x in r) == 1, str(r))
check("магазины: без входа нельзя", cl.post("/api/stores", json={"key": "lavka"}, headers={"X-Device": cl.post("/api/device").json()["device"]}).status_code == 403)
import bot_shop
kb_s = bot_shop._store_kb("r", None, TG)
check("магазины: в боте только выбранные", sum(1 for row in kb_s.inline_keyboard for b in row if b.callback_data.startswith("sl:s:")) == 1)
_sp.forget(TG)
check("магазины: стираются вместе с данными", len(_sp.get_enabled(TG)) == 5)
check("магазины на сайте: настройка и фильтр", "data-store-toggle" in html and "offStores" in html)

# ---------- копии базы ----------
import backup as _bk
import datetime as _dt
import pathlib as _pl
import shutil as _sh
import sqlite3 as _sq
_d = _pl.Path("/tmp/ct/bk_test")
_sh.rmtree(_d, ignore_errors=True)
_d.mkdir()
_src = _d / "a.db"
_c = _sq.connect(_src)
_c.execute("create table t(x)")
_c.execute("insert into t values(42)")
_c.commit()
_c.close()
_bk.SOURCES = [_src]
_f = _bk.run(_d / "out", _dt.date(2026, 11, 20))
check("копия: файл создан и читается", _sq.connect(_f / "a.db").execute("select x from t").fetchone()[0] == 42)
(_d / "out" / "2026-11-01").mkdir()
(_d / "out" / "2026-11-10").mkdir()
(_d / "out" / "notes").mkdir()
_bk.prune(_d / "out", 14, _dt.date(2026, 11, 20))
check("копия: старше 14 дней удаляется, остальное цело", not (_d / "out" / "2026-11-01").exists() and (_d / "out" / "2026-11-10").exists() and (_d / "out" / "notes").exists())
_sh.rmtree(_d, ignore_errors=True)

# ---------- посты о функциях и тексты ----------
import feature_posts as _fp
import social as _so
check("посты о функциях: 6 штук, с текстом и хештегами", len(_fp.POSTS) == 6 and all(len(p[2]) > 80 and p[3].startswith("#") for p in _fp.POSTS))
check("пост о фото помечен функцией", _fp.BY_KEY["photo"][4] == "photo_recipe")
import re as _re
_bad = _re.compile(r"(?<![А-Яа-яЁё])(выбери|напиши|нажми|попробуй|открой|укажи|отметь|твой|твоё|хочешь)(?![А-Яа-яЁё])", _re.I)
for _f2 in ("bot.py", "bot_profile.py", "bot_shop.py", "bot_diary.py", "bot_vision.py", "usage.py", "web/index.html"):
    _m = _bad.search(open(_f2, encoding="utf-8").read())
    check(f"тексты {_f2}: обращение на «вы»", _m is None, _m.group(0) if _m else "")
check("людям не показываются технические подробности", "Техническая причина" not in open("bot.py", encoding="utf-8").read() and ".env и попробуй" not in open("ai.py", encoding="utf-8").read())

# ---------- посты о функциях по расписанию ----------
_posted = []


async def _fake_post(text, attachment=None):
    _posted.append((text, attachment))
    return 100 + len(_posted)


async def _fake_photo():
    return "photo-1_2"


_so.post_vk, _so.function_photo = _fake_post, _fake_photo
_so.configured_vk = lambda: True
_real_open = _so.feature_open
accounts.sess_set(_so.FEATURE_DONE_KEY, {})
accounts.sess_set(_so.FEATURE_WEEK_KEY, {})
config.SOCIAL_AUTOPOST = False
asyncio.run(_so.weekly_feature_job(None))
check("посты по расписанию: выключено — ничего не выходит", not _posted)
config.SOCIAL_AUTOPOST = True
_rollout = plans.rollout
plans.rollout = lambda: {"photo_recipe": "testers"}
asyncio.run(_so.weekly_feature_job(None))
check("посты по расписанию: первый пост с картинкой", len(_posted) == 1 and _posted[0][1] == "photo-1_2" and "Не знаете, что приготовить" in _posted[0][0], str(_posted)[:200])
asyncio.run(_so.weekly_feature_job(None))
check("посты по расписанию: за неделю один", len(_posted) == 1)
_order = []
for _ in range(6):
    _k = _so.feature_next()
    _order.append(_k)
    asyncio.run(_so.publish_feature(_k))
check("посты: закрытая функция пропускается, потом по кругу", "photo" not in _order and _order[:4] == ["list", "menu", "diary", "book"] and _order[4] == "recipe", str(_order))
plans.rollout = lambda: {"photo_recipe": "all"}
check("посты: функцию открыли — пост выходит", _so.feature_next() == "photo")
plans.rollout = _rollout
config.SOCIAL_AUTOPOST = False

import helpdata
check("в справке есть дневник и отправка списка", any(k == "diary" for k, _, _ in helpdata.SECTIONS) and any("ссылкой на страницу" in t for _, _, t in helpdata.SECTIONS))
print()
print("ИТОГ:", "ВСЁ ОК" if not fails else f"ОШИБКИ: {fails}")
sys.exit(1 if fails else 0)
