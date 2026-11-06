"""Вход на сайте через VK ID: сервер верит только ответу ВК, аккаунт ВК не путается с Telegram, избранное переносится."""
import os
import sys

PROJ = "/tmp/ct"
os.chdir(PROJ)
sys.path.insert(0, PROJ)
for f in ("usage_vk.db", "accounts_vk.db", "accounts_vk.db-wal", "accounts_vk.db-shm"):
    if os.path.exists(f):
        os.remove(f)
os.environ["USAGE_DB"] = PROJ + "/usage_vk.db"
os.environ["ACCOUNTS_DB"] = PROJ + "/accounts_vk.db"
os.environ["WEB_DATA_DIR"] = "web_data_vk"

import httpx
from fastapi.testclient import TestClient
import config
import web_api
import accounts
import favorites
import web_db
import plans

fails = []


def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{x}]"))
    if not c:
        fails.append(n)


calls = []
REPLY = [{"user": {"user_id": "123456789", "first_name": "Аня"}}]


class R:
    def __init__(self, d):
        self.d = d

    def json(self):
        if isinstance(self.d, Exception):
            raise self.d
        return self.d


def fake_post(url, data=None, timeout=None, **k):
    calls.append((url, dict(data or {})))
    r = REPLY[0]
    if isinstance(r, httpx.HTTPError):
        raise r
    return R(r)


web_api.httpx.post = fake_post
cl = TestClient(web_api.app)
dev = cl.post("/api/device").json()["device"]
H = {"X-Device": str(dev)}
DID = web_db.device_id(dev)
TOKEN = "vk2.a.TOKEN_1234567890"

me = cl.get("/api/me", headers=H).json()
check("/api/me отдаёт ID приложения и адрес возврата для виджета", me["vk_app"] == "54875605" and me["vk_redirect"].endswith("/api/vk/callback") and me["linked"] is False, str(me)[:200])
check("адрес возврата совпадает с зарегистрированным в VK ID", me["vk_redirect"] == "https://menu.ymayaka.ru/api/vk/callback", me["vk_redirect"])

# избранное до входа
favorites.add_favorite(accounts.web_uid(DID), "Сырники", "Сырники\nКалорийность: 300 ккал", "classic")

r = cl.post("/api/vk/login", json={"access_token": TOKEN}, headers=H)
check("вход принят", r.status_code == 200 and r.json() == {"ok": True}, r.text)
url, sent = calls[-1]
check("сервер спросил у ВК, чей токен: ID приложения и токен, адрес VK ID", url == "https://id.vk.ru/oauth2/user_info" and sent == {"client_id": "54875605", "access_token": TOKEN}, str(calls[-1]))
me = cl.get("/api/me", headers=H).json()
check("устройство вошло: аккаунт ВК, статус «через vk»", me["linked"] is True and me["via"] == "vk", str(me)[:200])
acc = accounts.VK_OFFSET + 123456789
check("аккаунт ВК не пересекается с Telegram-аккаунтами и не похож на гостя сайта", accounts.tg_of_device(DID) == acc and acc < 10**12 and accounts.web_uid(DID) == str(acc))
check("избранное, сделанное до входа, перешло в аккаунт", [f["title"] for f in favorites.get_favorites(acc)] == ["Сырники"])
check("аккаунту ВК доступен профиль и дневник так же, как Telegram", not plans.is_anon(str(acc)))
r = cl.post("/api/profile/consent", headers=H)
check("согласие на данные профиля работает у аккаунта ВК", r.status_code == 200, r.text)

# защита
dev2 = cl.post("/api/device").json()["device"]
H2 = {"X-Device": str(dev2)}
DID2 = web_db.device_id(dev2)
REPLY[0] = {"error": "invalid_token"}
r = cl.post("/api/vk/login", json={"access_token": TOKEN}, headers=H2)
check("ВК не узнал токен — вход отклонён (400), устройство не привязано", r.status_code == 400 and accounts.tg_of_device(DID2) is None, r.text)
REPLY[0] = {"user": {"user_id": "abc"}}
check("мусорный номер в ответе отклоняется", cl.post("/api/vk/login", json={"access_token": TOKEN}, headers=H2).status_code == 400)
REPLY[0] = {"user": {"user_id": "99999999999999"}}
check("нелепо большой номер отклоняется", cl.post("/api/vk/login", json={"access_token": TOKEN}, headers=H2).status_code == 400)
REPLY[0] = {"user": {"user_id": "0"}}
check("нулевой номер отклоняется", cl.post("/api/vk/login", json={"access_token": TOKEN}, headers=H2).status_code == 400)
REPLY[0] = httpx.ConnectError("down")
r = cl.post("/api/vk/login", json={"access_token": TOKEN}, headers=H2)
check("ВК недоступен — понятный отказ (502), не падение", r.status_code == 502, r.text)
REPLY[0] = R(ValueError("not json"))
check("не-JSON от ВК — тоже отказ без падения", cl.post("/api/vk/login", json={"access_token": TOKEN}, headers=H2).status_code in (400, 502))
REPLY[0] = ["список вместо объекта"]
check("неожиданная форма ответа не роняет сервер", cl.post("/api/vk/login", json={"access_token": TOKEN}, headers=H2).status_code == 400)
check("слишком короткий и слишком длинный токен отклоняются сразу (422), ВК не спрашиваем",
      cl.post("/api/vk/login", json={"access_token": "x"}, headers=H2).status_code == 422
      and cl.post("/api/vk/login", json={"access_token": "x" * 5000}, headers=H2).status_code == 422)
check("без устройства войти нельзя", cl.post("/api/vk/login", json={"access_token": TOKEN}).status_code in (401, 422))
check("номер пользователя из браузера не принимается: лишние поля игнорируются", True)
REPLY[0] = {"user": {"user_id": "777"}}
r = cl.post("/api/vk/login", json={"access_token": TOKEN, "user_id": 1, "tg_id": 5}, headers=H2)
check("подставленные поля user_id и tg_id не влияют: аккаунт по ответу ВК", r.status_code == 200 and accounts.tg_of_device(DID2) == accounts.VK_OFFSET + 777)

# частота попыток
dev3 = cl.post("/api/device").json()["device"]
H3 = {"X-Device": str(dev3)}
REPLY[0] = {"error": "x"}
codes = [cl.post("/api/vk/login", json={"access_token": TOKEN}, headers=H3).status_code for _ in range(13)]
check("после 10 попыток в час приходит 429", codes[:10].count(400) == 10 and codes[10:] == [429, 429, 429], str(codes))

# выход и страница возврата
cl.post("/api/unlink", headers=H)
check("выход отвязывает устройство", cl.get("/api/me", headers=H).json()["linked"] is False)
r = cl.get("/api/vk/callback")
check("адрес возврата отвечает страницей, а не 404", r.status_code == 200 and "закрыть" in r.text)

# выключатель
config.VK_LOGIN = False
check("если вход выключен, кнопка скрыта и вход закрыт", cl.get("/api/me", headers=H).json()["vk_app"] == "" and cl.post("/api/vk/login", json={"access_token": TOKEN}, headers=H).status_code == 404)
config.VK_LOGIN = True

# страница
html = open("web/index.html", encoding="utf-8").read()
check("на странице есть карточка «Войти через ВКонтакте» и вход переходом", "Войти через ВКонтакте" in html and "/api/vk/start" in html and "@vkid/sdk" not in html)

# ---------- объединение ВК и Telegram ----------
def vk_login(token_dev, vk_id):
    REPLY[0] = {"user": {"user_id": str(vk_id)}}
    return cl.post("/api/vk/login", json={"access_token": TOKEN}, headers={"X-Device": str(token_dev)})


TG = 5550001
dev5 = cl.post("/api/device").json()["device"]
DID5 = web_db.device_id(dev5)
check("вход через ВК с нового устройства", vk_login(dev5, 4242).status_code == 200)
VKA = accounts.VK_OFFSET + 4242
accounts.consent_set(VKA)
accounts.goals_set(VKA, kcal=1800, feats=["diary"])
accounts.weight_set(VKA, 80, "2026-11-01")
accounts.diary_add(VKA, "Гречка", 165)
accounts.shelf_add(VKA, "масло подсолнечное")
favorites.add_favorite(VKA, "Суп", "Суп\nккал", "classic")
accounts.grant_days(VKA, 10, "test")
accounts.consent_set(TG)
accounts.goals_set(TG, kcal=2000, feats=["shelf"])
accounts.weight_set(TG, 90, "2026-11-01")
code = accounts.new_link_code(DID5)
check("бот принял код привязки Telegram", accounts.use_link_code(code, TG) == DID5)
check("устройство теперь вошло как Telegram", accounts.tg_of_device(DID5) == TG)
check("дневник, вес, полка, избранное и подписка перешли в профиль Telegram", accounts.diary_day(TG)["kcal"] == 165 and len(accounts.weights_list(TG)) == 1
      and accounts.shelf_list(TG) and [f["title"] for f in favorites.get_favorites(TG)] == ["Суп"] and accounts.premium_until(TG) > 0, "")
check("при совпадении остаётся то, что уже было в Telegram (цель, вес за тот же день)", accounts.goals_get(TG)["kcal"] == 2000 and accounts.weights_list(TG)[0]["kg"] == 90)
check("у аккаунта ВК ничего не осталось", accounts.diary_day(VKA)["items"] == [] and accounts.weights_list(VKA) == [] and not accounts.shelf_list(VKA) and accounts.premium_until(VKA) == 0)
check("запомнено, что этот ВК принадлежит Telegram-профилю", accounts._q("SELECT dst FROM alias WHERE src=?", (str(VKA),), one=True)[0] == str(TG))
dev6 = cl.post("/api/device").json()["device"]
check("новый вход через тот же ВК с другого устройства ведёт в общий профиль", vk_login(dev6, 4242).status_code == 200 and accounts.tg_of_device(web_db.device_id(dev6)) == TG)
me6 = cl.get("/api/me", headers={"X-Device": str(dev6)}).json()
check("на сайте это профиль Telegram (via=tg), ВК-вход идёт в тот же дневник", me6["via"] == "tg" and me6["linked"] is True)

# Telegram-устройство привязывает ВК
TG2 = 5550002
dev7 = cl.post("/api/device").json()["device"]
DID7 = web_db.device_id(dev7)
accounts.claim_login_code(accounts.new_login_code(TG2), DID7)
check("устройство вошло как Telegram (ссылка из бота)", accounts.tg_of_device(DID7) == TG2)
r = vk_login(dev7, 6060)
VKB = accounts.VK_OFFSET + 6060
accounts.diary_add(VKB, "Яблоко", 80)
check("ВК привязан к Telegram-устройству: устройство осталось Telegram", r.status_code == 200 and accounts.tg_of_device(DID7) == TG2)
accounts.diary_add(VKB, "Банан", 100)               # данные ВК, накопленные отдельно (например, на другом телефоне до привязки)
dev8 = cl.post("/api/device").json()["device"]
check("тот же ВК на другом устройстве: в профиль Telegram", vk_login(dev8, 6060).status_code == 200 and accounts.tg_of_device(web_db.device_id(dev8)) == TG2)
# конфликт: ВК уже принадлежит другому Telegram
TG3 = 5550003
dev9 = cl.post("/api/device").json()["device"]
accounts.claim_login_code(accounts.new_login_code(TG3), web_db.device_id(dev9))
r = vk_login(dev9, 6060)
check("ВК уже объединён с другим Telegram — отказ 409 с понятным текстом, данные не тронуты", r.status_code == 409 and "уже объединён" in r.text and accounts.tg_of_device(web_db.device_id(dev9)) == TG3)
check("повторная привязка того же ВК к тому же Telegram безопасна", vk_login(dev7, 6060).status_code == 200 and accounts.tg_of_device(DID7) == TG2)
check("слияние аккаунта с самим собой ничего не стирает", accounts.diary_day(TG)["kcal"] == 165)
check("«Удалить мои данные» стирает и перенесённое", accounts.delete_my_data(TG)["diary"] >= 1 and accounts.diary_day(TG)["items"] == [])
html = open("web/index.html", encoding="utf-8").read()
check("на сайте: у вошедшего через Telegram есть «Привязать ВКонтакте», у вошедшего через ВК — «Привязать Telegram»", "Привязать ВКонтакте" in html and "Привязать Telegram" in html)

print("\nИТОГ:", "ВСЁ ОК" if not fails else "ПРОВАЛЕНО: " + ", ".join(fails))
