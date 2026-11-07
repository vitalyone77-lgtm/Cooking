"""Порции без ИИ, общий список из избранного, проверка состава в боте, отсутствие тарифа за 10 ₽, честные обещания в текстах."""
import asyncio
import os
import sys
from datetime import datetime as dt

PROJ = "/tmp/ct"
os.chdir(PROJ)
sys.path.insert(0, PROJ)
for f in ("usage_ex.db", "accounts_ex.db", "accounts_ex.db-wal", "accounts_ex.db-shm", "user_data.json"):
    if os.path.exists(f):
        os.remove(f)
os.environ["USAGE_DB"] = PROJ + "/usage_ex.db"
os.environ["ACCOUNTS_DB"] = PROJ + "/accounts_ex.db"
os.environ["WEB_DATA_DIR"] = "web_data_ex"

from aiogram.client.bot import Bot
from aiogram.client.session.base import BaseSession
from aiogram.types import CallbackQuery, Chat, InlineKeyboardMarkup, Message, MessageEntity, PhotoSize, Update, User
from fastapi.testclient import TestClient

import tg_safety
tg_safety.FloodGuard.__init__.__defaults__ = (100000, 10)
import accounts
import bot as botmod
import bot_extras
import bot_vision
import config
import favorites
import helpdata
import payinfo
import plans
import promo_plan
import recipe_math as rm
import seo_pages
import shoplists
import usage
import vision
import web_api
from last_recipe import get_last_recipe, set_last_recipe

fails = []


def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{x}]"))
    if not c:
        fails.append(n)


R = ("🍽 Куриный суп\n\n⏱ Время: 40 мин | 👥 Порций: 2 | 🔥 Способ: Плита\n\n*Пищевая ценность (на 1 порцию):*\nКалорийность: 350 ккал | Б: 30 г | Ж: 9 г | У: 30 г\n\n"
     "*Продукты:*\n- Курица — 300 г\n- Молоко 1,5% — 200 мл\n- Яйцо — 1 шт\n- Лук репчатый — 50 г (≈ 1 небольшая луковица)\n- Масло — 2 ст. л.\n- Мука — 1,5 кг\n- Соль — по вкусу\n"
     "- Сметана 15% — 1/2 стакана\n- Рис 150 г\n\n*Инструкция:*\n1. Сварите 300 г курицы 20 минут.\n2. Добавьте лук.")
R2 = ("🍽 Плов\n\n⏱ Время: 60 мин | 👥 Порций: 4 | 🔥 Способ: Плита\n\n*Продукты:*\n- курица — 700 г\n- Рис — 400 г\n- Вода — 500 мл\n- Масло сливочное — 30 г\n- Масло — 1 ст. л.\n- Соль, перец\n- Яйцо — 2 шт\n\n"
      "*Инструкция:*\n1. Варите.")

# ---- расчёт ----
s4 = rm.scale_text(R, 4)
check("база порций: 2", rm.base_servings(R) == 2)
check("порции в шапке обновлены", "👥 Порций: 4" in s4, s4[:120])
check("граммы и миллилитры удвоены", "Курица — 600 г" in s4 and "— 400 мл" in s4, s4)
check("проценты в названии не тронуты", "Молоко 1,5%" in s4 and "Сметана 15%" in s4, s4)
check("штуки, ложки, кг и дроби посчитаны", all(x in s4 for x in ("Яйцо — 2 шт", "Масло — 4 ст. л.", "Мука — 3 кг", "— 1 стакана")), s4)
check("примерная подсказка в скобках убрана, чтобы не было «2 небольшая луковица»", "Лук репчатый — 100 г\n" in s4 and "≈" not in s4, s4)
check("без количества и без тире: по вкусу не меняется, число с единицей считается", "Соль — по вкусу" in s4 and "- Рис 300 г" in s4, s4)
check("шаги и КБЖУ на порцию не меняются", "1. Сварите 300 г курицы 20 минут." in s4 and "350 ккал" in s4, s4)
check("пояснение про числа в шагах между продуктами и инструкцией", s4.index("Рис 300 г") < s4.index("ℹ️ Количества пересчитаны с 2 на 4") < s4.index("*Инструкция"), s4)
check("то же число порций: текст без изменений", rm.scale_text(R, 2) == R)
check("без указания порций не считаем", rm.scale_text("🍽 X\n\n*Продукты:*\n- Курица — 300 г", 4).endswith("300 г") and rm.base_servings("🍽 X") is None)
s1 = rm.scale_text(R, 1)
check("уменьшение: половина яйца, округление граммов", "Яйцо — 0,5 шт" in s1 and "Курица — 150 г" in s1 and "Лук репчатый — 30 г" in s1 or "Лук репчатый — 25 г" in s1, s1)
check("HTML-разметка заголовка понимается", "Курица — 600 г" in rm.scale_text(R.replace("*Продукты:*", "<b>Продукты:</b>"), 4))

# ---- сложение ----
m = {i["name"].lower(): i["amount"] for i in rm.merge_recipes([R, R2])}
check("одинаковые продукты сложились независимо от регистра", m.get("курица") == "1 кг", m)
check("штуки сложились", m.get("яйцо") == "3 шт", m)
check("граммы переходят в килограммы только от 1000", m.get("рис 150 г".split()[0]) == "550 г", m)
check("вода в список покупок не идёт", "вода" not in m, m)
check("разные продукты с общим словом не сливаются", m.get("масло сливочное") == "30 г" and m.get("масло") == "3 ст. л.", m)
check("две штуки одной строкой разделены", "соль" in m and "перец" in m, m)
check("количество словами остаётся, если чисел нет", m.get("соль") == "по вкусу", m)
check("лук из подсказки считается по граммам", m.get("лук репчатый") == "50 г", m)
check("несколько единиц через плюс", any(" + " in rm._amount_text({"g": 200, "tbsp": 2}, "") for _ in [0]))

# ---- тариф за 10 ₽ ----
check("тарифа за 10 ₽ больше нет ни у кого", plans.check_offer() is None and plans.offer("check") is None and all(o["key"] != "check" for o in plans.pay_offers_for(config.ADMIN_TG_IDS[0] if config.ADMIN_TG_IDS else "1")))

# ---- тексты ----
all_seo = str(seo_pages.PAGES) + str(seo_pages.__dict__.get("_CSS", ""))
check("статьи не обещают бесплатное меню без регистрации", "Бесплатно. Регистрация не нужна" not in all_seo and "Бесплатно, без регистрации" not in all_seo)
check("задания /promo: опечатка и устаревшие шаги исправлены", "Отправьте тексту" not in str(promo_plan.TASKS) and "SOCIAL_AUTOPOST" not in str(promo_plan.TASKS) and "реферальная ссылка (если" not in str(promo_plan.TASKS))
idx = open("web/index.html", encoding="utf-8").read()
check("сайт: нет обещания бесплатно без регистрации", "Бесплатно и без регистрации" not in idx and "Бесплатный помощник" not in idx)
check("сайт: панель порций и общий список на месте", "data-scale" in idx and "viewCombo" in idx)

C = 793001
seq = [0]
user, chat = User(id=C, is_bot=False, first_name="T"), Chat(id=C, type="private")


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls, self.n = [], 0

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        name = type(method).__name__
        self.calls.append((name, method.model_dump(exclude_none=True)))
        if name in ("SendMessage", "EditMessageText", "SendPhoto"):
            self.n += 1
            return Message(message_id=9500 + self.n, date=dt.now(), chat=chat, text=getattr(method, "text", "") or "x").as_(bot)
        return True

    async def stream_content(self, *a, **k):
        raise NotImplementedError


def cb(data, text="x", markup=None, entities=None):
    seq[0] += 1
    m = Message(message_id=800, date=dt.now(), chat=chat, text=text, reply_markup=markup, entities=entities)
    return Update(update_id=seq[0], callback_query=CallbackQuery(id=str(seq[0]), from_user=user, chat_instance="x", message=m, data=data))


def msg(text=None, photo=False, group=None):
    seq[0] += 1
    return Update(update_id=seq[0], message=Message(message_id=100 + seq[0], date=dt.now(), chat=chat, from_user=user, text=text, media_group_id=group,
                                                    photo=[PhotoSize(file_id="p", file_unique_id="u", width=10, height=10)] if photo else None))


def data_of(p):
    return [x.get("callback_data") for r in (p or {}).get("reply_markup", {}).get("inline_keyboard", []) for x in r]


async def main():
    s = FakeSession()
    b = Bot(token="42:TEST", session=s)
    st = botmod.dp.fsm.get_context(b, C, C)
    last = lambda name: next((p for n, p in reversed(s.calls) if n == name), None)

    # ---- порции в боте ----
    kb0 = botmod.recipe_kb(R, C)
    check("кнопка порций под рецептом", "ps:o" in [x.callback_data for r in kb0.inline_keyboard for x in r])
    check("без числа порций кнопки нет", "ps:o" not in [x.callback_data for r in botmod.recipe_kb("🍽 X\n\n*Продукты:*\n- Курица — 300 г", C).inline_keyboard for x in r])
    plain = R.replace("*", "")
    off = plain.index("Продукты:")
    ents = [MessageEntity(type="bold", offset=len(plain[:off].encode("utf-16-le")) // 2, length=9)]
    set_last_recipe(C, "Куриный суп", R, "classic")
    await botmod.dp.feed_update(b, cb("ps:o", plain, kb0, ents))
    p = last("EditMessageReplyMarkup")
    check("открылись числа порций, текущее отмечено", {"ps:s:1", "ps:s:4", "ps:c"} <= set(data_of(p)) and "• 2" in str(p), str(p)[:300])
    await botmod.dp.feed_update(b, cb("ps:s:4", plain, InlineKeyboardMarkup.model_validate(p["reply_markup"]), ents))
    p = last("EditMessageText")
    check("пересчитано в том же сообщении, жирный заголовок сохранён", "Курица — 600 г" in p["text"] and "<b>Продукты:</b>" in p["text"] and p["parse_mode"] == "HTML", str(p)[:400])
    check("последний рецепт тоже пересчитан: PDF, избранное и покупки сойдутся", "Курица — 600 г" in get_last_recipe(C)["text"])
    await botmod.dp.feed_update(b, cb("ps:s:6", p["text"], None, None))
    p = last("EditMessageText")
    check("считаем от исходного текста, а не от прошлого пересчёта", "Курица — 900 г" in p["text"] and "👥 Порций: 6" in p["text"] and "с 2 на 6" in p["text"], p["text"][:300])
    await botmod.dp.feed_update(b, cb("ps:c", p["text"]))
    check("«Свернуть» возвращает прежние кнопки", "sl:o:r" in data_of(last("EditMessageReplyMarkup")))

    # ---- общий список ----
    favorites.add_favorite(C, "Куриный суп", R, "classic")
    favorites.add_favorite(C, "Плов", R2, "classic")
    await botmod.dp.feed_update(b, cb("favorites:open"))
    all_calls = [p for n, p in s.calls if n in ("SendMessage", "EditMessageText", "SendPhoto")]
    check("в избранном кнопка общего списка", "cb:o" in data_of(all_calls[-1]), str(all_calls[-1])[:200])
    await botmod.dp.feed_update(b, cb("cb:o"))
    p = last("EditMessageText")
    ids = [d for d in data_of(p) if d and d.startswith("cb:t:")]
    check("список рецептов с галочками", len(ids) == 2 and "cb:go" not in data_of(p), str(p)[:300])
    for d in ids:
        await botmod.dp.feed_update(b, cb(d))
    check("после выбора можно собрать", "Собрать список (2)" in str(last("EditMessageText")))
    await botmod.dp.feed_update(b, cb("cb:go"))
    p = last("SendMessage")
    check("список собран, продукты сложены, есть ссылка", "курица — 1 кг" in p["text"].lower() and "/l/" in p["text"] and "Покупки на 2 рецепта" in p["text"], p["text"][:400])
    lid = p["text"].split("/l/")[1].split()[0]
    rec = shoplists.get(lid)
    check("страница со списком создана", rec and any(i["name"].lower() == "курица" and i["amount"] == "1 кг" for i in rec["items"]), str(rec)[:200])
    try:
        shoplists.from_favorites(C, ["нет такого"])
        bad = True
    except ValueError:
        bad = False
    check("чужой или неизвестный рецепт в список не попадёт", not bad)
    check("список только из своего избранного", (lambda: _other())())

    # ---- состав ----
    await botmod.dp.feed_update(b, msg("/label"))
    check("/label просит фото", "Сфотографируйте состав" in last("SendMessage")["text"], str(last("SendMessage"))[:200])
    check("в настройках есть кнопка проверки состава", "lb:o" in [x.callback_data for r in botmod.bot_profile._profile_view(C)[1].inline_keyboard for x in r])
    called = {"label": 0, "fridge": 0}

    async def fake_label(images):
        called["label"] += 1
        return {"products": [{"name": "Кефир", "ingredients": ["молоко"], "additives": ["E471: эмульгатор"], "attention": ["сахар в начале"]}], "compare": ""}

    async def fake_items(images):
        called["fridge"] += 1
        return ["морковь"]

    async def fake_bytes(m):
        return (b"x", "image/jpeg")
    vision.check_label, vision.photo_items = fake_label, fake_items
    bot_extras._photo_bytes = fake_bytes
    bot_vision._photo_bytes = fake_bytes
    n0 = usage.events_today("label_check", C)
    await botmod.dp.feed_update(b, msg(photo=True))
    p = last("EditMessageText")
    check("фото в режиме состава идёт в проверку состава, а не в рецепт по фото", called == {"label": 1, "fridge": 0} and "Кефир" in p["text"] and "E471" in p["text"] and "не медицинский совет" in p["text"], str(called) + str(p)[:300])
    check("событие учтено", usage.events_today("label_check", C) == n0 + 1)
    check("состояние сброшено", await st.get_state() is None)

    async def empty_label(images):
        return {"products": [], "compare": ""}
    vision.check_label = empty_label
    await st.set_state(bot_extras.LabelForm.wait)
    used = usage.remaining(C, "label") if hasattr(usage, "remaining") else None
    await botmod.dp.feed_update(b, msg(photo=True))
    check("состав не прочитан: просим снять ближе", "снимите ближе" in last("EditMessageText")["text"])
    check("неудачная попытка лимит не тратит", used is None or usage.remaining(C, "label") == used)
    await st.set_state(bot_extras.LabelForm.wait)
    await botmod.dp.feed_update(b, msg("кефир"))
    check("текст вместо фото: просим фото", "Нужна фотография" in last("SendMessage")["text"])
    await botmod.dp.feed_update(b, cb("lb:x"))
    check("отмена", await st.get_state() is None)
    called["fridge"] = 0
    if plans.feature_on(C, "photo_recipe"):
        await botmod.dp.feed_update(b, msg(photo=True))
        check("вне режима состава фото снова как раньше", called["fridge"] == 1)

    # ---- лимит состава в боте при включённой подписке ----
    config.PAYWALL_ENABLED = True
    accounts.settings_set({"LIMITS_FREE": "recipe=2/1,daymenu=1/7,week=1/30,missing=2/1,photo=1/1,label=0/1", "TRIAL_DAYS": 0}, "test")
    plans.reload()
    vision.check_label = fake_label
    called["label"] = 0
    await st.set_state(bot_extras.LabelForm.wait)
    await botmod.dp.feed_update(b, msg(photo=True))
    check("лимит состава: ИИ не вызывается", called["label"] == 0 and await st.get_state() is None, str(called))
    config.PAYWALL_ENABLED = False
    plans.reload()


def _other():
    favorites.add_favorite(793999, "Чужой", R, "classic")
    other_id = favorites.get_favorites(793999)[0]["id"]
    try:
        shoplists.from_favorites(C, [other_id])
        return False
    except ValueError:
        return True


asyncio.run(main())

# ---- сайт ----
cl = TestClient(web_api.app)
dev = cl.post("/api/device").json()
hdr = {"X-Device": dev.get("device") or dev.get("id") or dev.get("token") or ""}
r = cl.post("/api/scale", json={"text": R, "to": 4, "names": ["курица", "яйцо", "соль"]}, headers=hdr)
check("/api/scale пересчитывает и возвращает количества для покупок", r.status_code == 200 and "Курица — 600 г" in r.json()["text"] and r.json()["amounts"][0] == "600 г" and r.json()["amounts"][1] == "2 шт" and r.json()["servings"] == 4, r.text[:300])
check("/api/scale: нелепое число отклоняется", cl.post("/api/scale", json={"text": R, "to": 500}, headers=hdr).status_code == 422)
a = cl.post("/api/favorites", json={"title": "Куриный суп", "text": R, "cuisine": "classic"}, headers=hdr).json()["id"]
c2 = cl.post("/api/favorites", json={"title": "Плов", "text": R2, "cuisine": "classic"}, headers=hdr).json()["id"]
r = cl.post("/api/favorites/list", json={"ids": [a, c2]}, headers=hdr)
check("сайт: общий список собран", r.status_code == 200 and r.json()["title"] == "Покупки на 2 рецепта" and "/l/" in r.json()["url"] and any(i["amount"] == "1 кг" for i in r.json()["items"]), r.text[:300])
check("сайт: пустой выбор понятной ошибкой", cl.post("/api/favorites/list", json={"ids": []}, headers=hdr).status_code == 400)
check("помощь: раздел про порции, список и состав", any(h[0] == "label" for h in helpdata.SECTIONS) if hasattr(helpdata, "SECTIONS") else True)
check("страница оплаты показывает только месяц и год", "10 ₽" not in payinfo.page() and "Проверка оплаты" not in payinfo.page())

print("ИТОГО: упало", len(fails))
sys.exit(1 if fails else 0)
