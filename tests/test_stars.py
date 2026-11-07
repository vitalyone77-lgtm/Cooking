"""Цена в звёздах от цены в рублях: расчёт, порог, сбой курса, админка, старые счета."""
import asyncio
import os
import sys
import time
from datetime import datetime as dt

PROJ = "/tmp/ct"
os.chdir(PROJ)
sys.path.insert(0, PROJ)
for f in ("usage_st.db", "accounts_st.db", "accounts_st.db-wal", "accounts_st.db-shm", "user_data.json"):
    if os.path.exists(f):
        os.remove(f)
os.environ["USAGE_DB"] = PROJ + "/usage_st.db"
os.environ["ACCOUNTS_DB"] = PROJ + "/accounts_st.db"
os.environ["WEB_DATA_DIR"] = "web_data_st"

from aiogram.client.bot import Bot
from aiogram.client.session.base import BaseSession
from aiogram.types import Chat, Message, PreCheckoutQuery, Update, User
import httpx

import tg_safety
tg_safety.FloodGuard.__init__.__defaults__ = (100000, 10)
import accounts
import bot as botmod
import config
import plans
import stars_rate as sr

fails = []


def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{x}]"))
    if not c:
        fails.append(n)


config.PREMIUM_PLANS = "month=30:290:130,year=365:2900:1300"
config.STAR_USD = 0.013
check("расчёт: 290 ₽ при 82,14 ₽ за доллар и 1,3 цента за звезду", sr.stars_for(290, 82.14) == 275, str(sr.stars_for(290, 82.14)))
check("при выводе получается не меньше цены в рублях", sr.stars_for(290, 82.14) * 82.14 * 0.013 >= 290)
check("расчёт на год", sr.stars_for(2900, 82.14) == 2720, str(sr.stars_for(2900, 82.14)))
check("доллар дороже: звёзд меньше", sr.stars_for(290, 100) < sr.stars_for(290, 82.14))
check("ноль звёзд остаётся нулём: тариф звёздами не продаётся", sr.reprice("a=30:290:0,b=365:2900:100", 82.14) == "a=30:290:0,b=365:2900:2720")
check("порог: 3% не меняем", not sr.drifted("m=30:290:275", "m=30:290:285"))
check("порог: больше 5% меняем", sr.drifted("m=30:290:130", "m=30:290:275"))


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, uid, text, parse_mode=None):
        self.sent.append((uid, text))


async def rate_seq(values):
    it = iter(values)

    async def fake():
        return next(it)
    sr.fetch_usd = fake


async def run():
    config.ADMIN_TG_IDS = [111]
    fb = FakeBot()
    await rate_seq([82.14, 83.0, None, 100.0, 90.0])
    r = await sr.update(fb)
    offers = {o["key"]: o["stars"] for o in plans.offers()}
    check("первое обновление: цены подтянулись к рублям", r["changed"] and offers == {"month": 275, "year": 2720}, str(offers))
    check("рубли и сроки не тронуты", {o["key"]: (o["days"], o["rub"]) for o in plans.offers()} == {"month": (30, 290), "year": (365, 2900)})
    check("владелец предупреждён", any("275 ⭐" in t and "82.14" in t for _, t in fb.sent), str(fb.sent))
    n = len(fb.sent)
    r = await sr.update(fb)
    check("курс почти тот же: цена и сообщения не меняются", not r["changed"] and len(fb.sent) == n and {o["key"]: o["stars"] for o in plans.offers()}["month"] == 275)
    r = await sr.update(fb)
    check("сбой курса: цены прежние", r["usd"] is None and {o["key"]: o["stars"] for o in plans.offers()}["month"] == 275)
    check("свежий сбой без тревоги", len(fb.sent) == n)
    accounts.settings_set({"STARS_AUTO": False}, "test")
    plans.reload()
    r = await sr.update(fb)
    check("автоматика выключена: цену введённую вручную не трогаем, курс запоминаем", not r["changed"] and {o["key"]: o["stars"] for o in plans.offers()}["month"] == 275 and sr.last_rate()["usd"] == 100.0)
    accounts.settings_set({"STARS_AUTO": True}, "test")
    plans.reload()
    accounts.sess_set(sr.RATE_KEY, {"usd": 82.14, "ts": time.time() - 5 * 86400})
    await rate_seq([None])
    await sr.update(fb)
    check("курс долго не обновляется: владелец узнаёт", any("не обновлялся" in t for _, t in fb.sent))
    accounts.sess_set(sr.RATE_KEY, {"usd": 82.14, "ts": time.time()})
    check("смена цены в рублях сразу меняет звёзды", sr.on_admin_save("month=30:390:275") == "month=30:390:" + str(sr.stars_for(390, 82.14)))

    # разбор настоящих форматов
    js = httpx.Response(200, json={"Valute": {"USD": {"Value": 82.1386}}})
    check("формат JSON", sr._parse("https://x/daily_json.js", js) == 82.1386)
    xml = httpx.Response(200, content='<Valute ID="R"><NumCode>840</NumCode><CharCode>USD</CharCode><Nominal>1</Nominal><Value>82,1386</Value></Valute>'.encode("windows-1251"))
    check("формат XML ЦБ", sr._parse("https://x/XML_daily.asp", xml) == 82.1386)

    # старый счёт
    C = 795001
    user, chat = User(id=C, is_bot=False, first_name="T"), Chat(id=C, type="private")

    class FS(BaseSession):
        def __init__(self):
            super().__init__()
            self.calls = []

        async def close(self):
            pass

        async def make_request(self, bot, method, timeout=None):
            self.calls.append((type(method).__name__, method.model_dump(exclude_none=True)))
            return True

        async def stream_content(self, *a, **k):
            raise NotImplementedError
    s = FS()
    b = Bot(token="42:TEST", session=s)
    accounts.settings_set({"PREMIUM_PLANS": "month=30:290:275,year=365:2900:2720"}, "test")
    plans.reload()

    async def pay(i, amount, currency="XTR", key="month"):
        q = PreCheckoutQuery(id=str(i), from_user=user, currency=currency, total_amount=amount, invoice_payload=f"sub:{key}:{C}")
        await botmod.dp.feed_update(b, Update(update_id=5000 + i, pre_checkout_query=q))
        return s.calls[-1][1]
    p = await pay(1, 275)
    check("текущая цена принимается", p["ok"] is True, str(p))
    p = await pay(2, 265)
    check("чуть старая цена принимается: человек не виноват в смене курса", p["ok"] is True, str(p))
    p = await pay(3, 130)
    check("счёт по старой низкой цене отклонён с пояснением", p["ok"] is False and "Цена" in p["error_message"], str(p))
    p = await pay(4, 29000, "RUB")
    check("оплата в рублях этой проверкой не затронута", p["ok"] is True, str(p))
    text, markup = botmod.bot_profile._sub_view(C)
    check("в подписке показана новая цена", "275 звёзд" in str(markup), str(markup)[:300])


asyncio.run(run())
idx = open("web/admin.html", encoding="utf-8").read()
check("в админке есть переключатель", "s-stars-auto" in idx)
print("ИТОГО: упало", len(fails))
sys.exit(1 if fails else 0)
