"""Ошибки, найденные проверкой кода 07.11.2026: источник подписки при объединении и промокоде, лимит состава в админке, странный ответ ИИ, счётчик платящих."""
import asyncio
import os
import sys
import time

PROJ = "/tmp/ct"
os.chdir(PROJ)
sys.path.insert(0, PROJ)
for f in ("usage_au.db", "accounts_au.db", "accounts_au.db-wal", "accounts_au.db-shm", "user_data.json"):
    if os.path.exists(f):
        os.remove(f)
os.environ["USAGE_DB"] = PROJ + "/usage_au.db"
os.environ["ACCOUNTS_DB"] = PROJ + "/accounts_au.db"
os.environ["WEB_DATA_DIR"] = "web_data_au"

import accounts
import config
import plans
import usage
import vision
import web_api
from fastapi.testclient import TestClient

fails = []


def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{x}]"))
    if not c:
        fails.append(n)


src = lambda u: accounts._q("SELECT source FROM subs WHERE uid=?", (str(u),), one=True)[0]

# объединение: оплаченный ВК вливается в Telegram с пробным периодом
TG, VK = 7001, accounts.VK_OFFSET + 7001
accounts.start_trial(TG, 7)
accounts.apply_payment("pw:a1", VK, 290, "RUB", 30, "month")
c = accounts._conn()
with c:
    accounts._merge_accounts(c, str(VK), str(TG))
check("объединение: оплаченная подписка осталась оплаченной", src(TG) == "payment" and accounts.premium_until(TG) - time.time() > 29 * 86400, src(TG))
TG2, VK2 = 7002, accounts.VK_OFFSET + 7002
accounts.apply_payment("pw:a2", TG2, 290, "RUB", 30, "month")
accounts.grant_days(VK2, 3, "promo:X")
c = accounts._conn()
with c:
    accounts._merge_accounts(c, str(VK2), str(TG2))
check("объединение: у Telegram подписка длиннее, её источник сохранён", src(TG2) == "payment")

# промокод поверх оплаты
U = 7003
accounts.apply_payment("pw:a3", U, 290, "RUB", 30, "month")
accounts.grant_days(U, 14, "promo:BLOG")
check("промокод поверх оплаченной: источник «оплата», срок сложился", src(U) == "payment" and accounts.premium_until(U) - time.time() > 43 * 86400)
U2 = 7004
accounts.grant_days(U2, 14, "promo:BLOG")
check("промокод без оплаты: источник промокод", src(U2) == "promo:BLOG")
accounts.apply_payment("pwtest:t", 7005, 290, "RUBTEST", 30, "month")
check("в админке платящие без тестовых", accounts.money_stats()["paid_users"] == 3, str(accounts.money_stats()["paid_users"]))

# админка: лимит состава
spec = web_api._limits_spec({"recipe": [2, 1], "label": [1, 7]})
check("лимиты: проверка состава сохраняется", "label=1/7" in spec and "recipe=2/1" in spec, spec)
check("лимиты: вид, которого нет в форме, не затирается нулём", "daymenu" not in spec, spec)

# странный ответ ИИ
class R:
    def __init__(self, content):
        self.c = content

    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"message": {"content": self.c}}]}


class Client:
    content = "[1, 2]"

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass

    async def post(self, *a, **k):
        return R(Client.content)


vision.httpx.AsyncClient = Client
config.DEEPSEEK_API_KEY = config.DEEPSEEK_API_KEY or "x"
Client.content = '{"items": ["морковь"]}'
check("фото: обычный ответ разбирается", asyncio.run(vision.photo_items([(b"1", "image/jpeg")])) == ["морковь"])
Client.content = 'вот: {"a": 1} и [1]'
check("фото: объект без продуктов даёт пустой список", asyncio.run(vision.photo_items([(b"1", "image/jpeg")])) == [])
try:
    Client.content = 'список ["a"] без объекта'
    asyncio.run(vision.photo_items([(b"1", "image/jpeg")]))
    ok = True
except vision.VisionError:
    ok = True
except Exception as e:  # noqa: BLE001
    ok = False
check("фото: ответ без объекта не роняет обработку", ok)
Client.content = "[1, 2]"
try:
    ok = asyncio.run(vision.check_label([(b"1", "image/jpeg")]))["products"] == []
    Client.content = 'вот {"products": 1} и [1]'
    ok = ok and asyncio.run(vision.check_label([(b"1", "image/jpeg")]))["products"] == []
except vision.VisionError:
    ok = True
except Exception:  # noqa: BLE001
    ok = False
check("состав: ответ не объектом даёт понятную ошибку, а не падение", ok)

cl = TestClient(web_api.app, raise_server_exceptions=False)
dev = cl.post("/api/device").json()
hdr = {"X-Device": dev.get("device")}
dev_id = web_api.web_db.device_id(dev.get("device"))
accounts._q("INSERT INTO links(dev_id, tg_id, ts) VALUES(?,?,?)", (dev_id, 7010, time.time()))
Client.content = '{"products": "not a list"}'
before = usage._count("SELECT COUNT(*) FROM gens WHERE uid='7010'")
r = cl.post("/api/label", files={"images": ("a.jpg", b"123", "image/jpeg")}, headers=hdr)
check("сайт: непонятный ответ про состав без ошибки 500, попытка возвращена", r.status_code in (422, 502) and usage._count("SELECT COUNT(*) FROM gens WHERE uid='7010'") == before, f"{r.status_code} {r.text[:120]}")

print("ИТОГО: упало", len(fails))
sys.exit(1 if fails else 0)
