"""Фото холодильника → рецепт, фото полки, голосовой ввод, тестировщики и включение функций, счётчик «осталось»."""
import asyncio
import io
import os
import sys
from datetime import datetime

PROJ = "/tmp/ct"
os.chdir(PROJ)
sys.path.insert(0, PROJ)
for f in ("usage_vis.db", "accounts_vis.db"):
    if os.path.exists(f):
        os.remove(f)
os.environ["USAGE_DB"] = PROJ + "/usage_vis.db"
os.environ["ACCOUNTS_DB"] = PROJ + "/accounts_vis.db"
os.environ["WEB_DATA_DIR"] = "web_data_vis"

from aiogram.client.bot import Bot
from aiogram.client.session.base import BaseSession
from aiogram.types import Update, CallbackQuery, Message, Chat, User, PhotoSize, Voice

import config
config.ADMIN_PASSWORD, config.PARTNER_PASSWORD, config.WEB_SECRET = "owner-pass-1", "partner-pass-1", "secret-x"
import accounts
import plans
import usage
import vision
import bot as botmod
import bot_vision
from states import RecipeForm, ProfileForm

fails = []


def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{x}]"))
    if not c:
        fails.append(n)


CHAT = 777000202


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
            return Message(message_id=7000 + self.n, date=datetime.now(), chat=Chat(id=CHAT, type="private"),
                           text=getattr(method, "text", "") or "").as_(bot)
        return True

    async def stream_content(self, *a, **k):
        raise NotImplementedError


class FakeBot(Bot):
    async def download(self, file, destination=None, **kw):
        return io.BytesIO(b"\xff\xd8fake")


user = User(id=CHAT, is_bot=False, first_name="T")
chat = Chat(id=CHAT, type="private")
seq = [0]


def upd_msg(**kw):
    seq[0] += 1
    return Update(update_id=seq[0], message=Message(message_id=100 + seq[0], date=datetime.now(), chat=chat, from_user=user, **kw))


def photo(group=None):
    return upd_msg(photo=[PhotoSize(file_id="p1", file_unique_id="u1", width=800, height=600)], media_group_id=group)


def voice():
    return upd_msg(voice=Voice(file_id="v1", file_unique_id="uv1", duration=4))


def cb(data, msg_id):
    seq[0] += 1
    m = Message(message_id=msg_id, date=datetime.now(), chat=chat, text="x")
    return Update(update_id=seq[0], callback_query=CallbackQuery(id=str(seq[0]), from_user=user, chat_instance="x", message=m, data=data))


def texts(s):
    return [p.get("text", "") for n, p in s.calls if n in ("SendMessage", "EditMessageText")]


def last(s, name):
    return next((p for n, p in reversed(s.calls) if n == name), None)


def btns(p):
    return [b["text"] for r in (p or {}).get("reply_markup", {}).get("inline_keyboard", []) for b in r]


SEEN = ["молоко", "яйца", "помидоры", "сыр"]


ALBUM = []


async def fake_items(images):
    ALBUM.append(len(images))
    return list(SEEN)
TALK = ["хочу плов"]


async def fake_stt(audio, filename="voice.ogg"):
    return TALK[0]
vision.photo_items, vision.transcribe = fake_items, fake_stt


async def main():
    s = FakeSession()
    b = FakeBot(token="42:TEST", session=s)
    st = botmod.dp.fsm.get_context(b, CHAT, CHAT)

    # --- выключено для обычного человека (по умолчанию — только тестировщикам) ---
    check("по умолчанию новые функции только тестировщикам", plans.rollout() == {"voice": "testers", "photo_recipe": "testers", "photo_shelf": "testers"})
    await botmod.dp.feed_update(b, photo())
    check("не тестировщик: фото без ответа, ИИ не вызывается", not s.calls, str(s.calls[:2]))
    check("не тестировщик: в меню нет подсказки про фото", "фото" not in botmod._input_hints(CHAT))

    accounts.settings_set({"TESTER_IDS": [str(CHAT)]}, "test")
    plans.reload()
    config.ADMIN_TG_IDS = [555000111]
    check("администраторы — тоже тестировщики", plans.is_tester(555000111))
    check("тестировщик: функции включены", plans.is_tester(CHAT) and plans.feature_on(CHAT, "photo_recipe") and plans.feature_on(CHAT, "voice"))
    check("тестировщик: подсказки в меню", "фото холодильника" in botmod._input_hints(CHAT) and "голосом" in botmod._input_hints(CHAT))

    # --- фото холодильника вне анкеты ---
    await botmod.dp.feed_update(b, photo())
    p = last(s, "EditMessageText")
    check("фото → список с галочками", p and "Вот что я вижу" in p["text"] and "✅ молоко" in btns(p) and "🍳 Подобрать рецепт (4)" in btns(p), str(p)[:300])
    pick_id = 7000 + s.n - 1          # сообщение «Смотрю…», которое отредактировали
    check("фото: запомнили список", (CHAT, pick_id) in bot_vision._picks, str(list(bot_vision._picks)))
    await botmod.dp.feed_update(b, cb("vp:t:3", pick_id))
    p = last(s, "EditMessageReplyMarkup")
    check("снял галочку с сыра", p and "▫️ сыр" in btns(p) and "🍳 Подобрать рецепт (3)" in btns(p), str(btns(p)))
    await botmod.dp.feed_update(b, cb("vp:go", pick_id))
    check("подобрать → анкета с продуктами с фото", any("🧺 Продукты: молоко, яйца, помидоры" in t for t in texts(s)), str(texts(s)[-2:]))
    data = await st.get_data()
    check("анкета: продукты в данных, шаг — выбор кухни", data.get("preferred") == "молоко, яйца, помидоры" and await st.get_state() == RecipeForm.cuisine.state, str(data))
    await botmod.dp.feed_update(b, cb("cuisine:classic", 7000 + s.n))
    check("после кухни вопрос о продуктах пропущен — сразу «исключить»", "исключить" in texts(s)[-1] and await st.get_state() == RecipeForm.excluded.state, texts(s)[-1][:80])

    # --- голос на шаге анкеты ---
    TALK[0] = "грибы и лук"
    await botmod.dp.feed_update(b, voice())
    data = await st.get_data()
    check("голос на шаге «исключить» = текст", data.get("excluded") == "грибы и лук" and any("Распознал: «грибы и лук»" in t for t in texts(s)), str(data.get("excluded")))
    check("голос учтён в статистике", usage.events_today("voice", CHAT) == 1)

    # --- голос вне анкеты → сразу к подбору ---
    await st.clear()
    TALK[0] = "хочу плов"
    await botmod.dp.feed_update(b, voice())
    data = await st.get_data()
    check("голос вне анкеты: «хочу плов» → анкета рецепта", data.get("preferred") == "хочу плов" and await st.get_state() == RecipeForm.cuisine.state, str(data))

    # --- фото на шаге «что использовать» + «Дописать» ---
    await st.clear()
    await st.set_state(RecipeForm.preferred)
    await st.update_data(cuisine="classic")
    await botmod.dp.feed_update(b, photo())
    pick_id = 7000 + s.n - 1
    await botmod.dp.feed_update(b, cb("vp:add", pick_id))
    check("дописать: ждём текст", "Напишите, что добавить" in texts(s)[-1])
    await botmod.dp.feed_update(b, upd_msg(text="сметана, укроп"))
    new_id = 7000 + s.n
    check("дописанное — в новом списке с галочками", (CHAT, new_id) in bot_vision._picks and "сметана" in bot_vision._picks[(CHAT, new_id)]["items"], str(list(bot_vision._picks)))
    check("вернулись в анкету с её ответами", await st.get_state() == RecipeForm.preferred.state and (await st.get_data()).get("cuisine") == "classic")
    await botmod.dp.feed_update(b, cb("vp:go", new_id))
    data = await st.get_data()
    check("из анкеты: продукты подставлены и дальше «исключить»", "сметана" in (data.get("preferred") or "") and await st.get_state() == RecipeForm.excluded.state, str(data))

    # --- сразу после меню (состояние «выбор кухни»): фото и голос не теряются ---
    await botmod.show_main_menu(Message(message_id=1, date=datetime.now(), chat=chat, text="x").as_(b), st)
    check("после меню — шаг выбора кухни", await st.get_state() == RecipeForm.cuisine.state)
    await botmod.dp.feed_update(b, photo())
    check("после меню: фото → список с галочками", "Вот что я вижу" in (last(s, "EditMessageText") or {}).get("text", ""))
    await botmod.show_main_menu(Message(message_id=1, date=datetime.now(), chat=chat, text="x").as_(b), st)
    TALK[0] = "хочу борщ"
    await botmod.dp.feed_update(b, voice())
    data = await st.get_data()
    check("после меню: голос «хочу борщ» → анкета с этим пожеланием", data.get("preferred") == "хочу борщ" and data.get("preset_preferred")
          and await st.get_state() == RecipeForm.cuisine.state, str(data))
    await st.set_state(RecipeForm.confirm)
    TALK[0] = "хочу сырники"
    await botmod.dp.feed_update(b, voice())
    check("после готового рецепта: голос тоже запускает подбор", (await st.get_data()).get("preferred") == "хочу сырники")
    pdf = upd_msg(document=__import__("aiogram").types.Document(file_id="d1", file_unique_id="du1", mime_type="application/pdf"))
    n0 = usage.used(CHAT, "photo")
    await botmod.dp.feed_update(b, pdf)
    check("PDF — не фото: попытка не тратится", usage.used(CHAT, "photo") == n0)

    # --- альбом: два фото одним отправлением ---
    await st.clear()
    ALBUM.clear()
    n0, k0 = usage.used(CHAT, "photo"), len(bot_vision._picks)
    await asyncio.gather(botmod.dp.feed_update(b, photo("alb1")), botmod.dp.feed_update(b, photo("alb1")))
    check("альбом: одна попытка, один вызов ИИ с двумя фото", usage.used(CHAT, "photo") == n0 + 1 and ALBUM == [2], f"{usage.used(CHAT, 'photo') - n0} {ALBUM}")

    # --- полка по фото ---
    await st.clear()
    await st.set_state(ProfileForm.shelf_add)
    SEEN[:] = ["оливковое масло", "гречка", "куркума"]
    await botmod.dp.feed_update(b, photo())
    pick_id = 7000 + s.n - 1
    check("полка: кнопка «Добавить на полку»", "🏠 Добавить на полку (3)" in btns(last(s, "EditMessageText")))
    await botmod.dp.feed_update(b, cb("vp:t:1", pick_id))
    await botmod.dp.feed_update(b, cb("vp:go", pick_id))
    shelf = [x["item"] for x in accounts.shelf_list(CHAT)]
    check("полка: добавлены отмеченные", set(shelf) == {"оливковое масло", "куркума"}, str(shelf))
    check("устаревший список — понятный ответ", (await botmod.dp.feed_update(b, cb("vp:go", 1)) or True) and
          any("устарело" in (p.get("text") or "") for n, p in s.calls if n == "AnswerCallbackQuery"))

    # --- ошибка распознавания — попытка возвращается ---
    async def broken(*a, **k):
        raise vision.VisionError("down")
    vision.photo_items = broken
    await st.clear()
    usage._q("DELETE FROM gens WHERE kind='photo'")      # дневной лимит фото уже выбран тестами выше
    before = usage.used(CHAT, "photo")
    await botmod.dp.feed_update(b, photo())
    check("сбой фото: понятное сообщение и попытка не списана", "Не получилось разобрать фото" in texts(s)[-1] and usage.used(CHAT, "photo") == before)
    vision.photo_items = fake_items

    # --- счётчик «осталось сегодня» ---
    t = usage.left_text(CHAT, "recipe")
    check("счётчик: «Осталось сегодня: N из M»", t.startswith("Осталось сегодня: ") and " из " in t, t)
    await st.clear()
    await botmod.show_main_menu(Message(message_id=1, date=datetime.now(), chat=chat, text="x").as_(b), st)
    check("счётчик в меню «Создать»", "🔢 Осталось сегодня" in texts(s)[-1], texts(s)[-1][-80:])

    # --- лимит фото при подписке: бесплатно 1 в день, тестировщик — как подписка ---
    config.PAYWALL_ENABLED, config.TRIAL_DAYS = True, 0
    other = 777000303
    usage.log("bot", other, "photo")
    check("бесплатный: второе фото за день нельзя", usage.try_spend(other, "photo", "bot")[0] is None)
    check("тестировщик при подписке = полный доступ", plans.plan_of(CHAT) == "premium")
    accounts.settings_set({"FEATURE_ROLLOUT": {"voice": "premium", "photo_recipe": "all", "photo_shelf": "off"}}, "test")
    plans.reload()
    check("режимы: premium — не бесплатному, all — всем, off — никому кроме…", not plans.feature_on(other, "voice")
          and plans.feature_on(other, "photo_recipe") and not plans.feature_on(other, "photo_shelf") and not plans.feature_on(CHAT, "photo_shelf"))
    check("сайт без входа: «подписка» не включается", not plans.feature_on("w55", "voice"))
    config.PAYWALL_ENABLED = False
    check("до включения оплаты «подписка» = все вошедшие", plans.feature_on(other, "voice"))
    accounts.settings_set({"FEATURE_ROLLOUT": {"voice": "testers", "photo_recipe": "testers", "photo_shelf": "testers"}}, "test")
    plans.reload()

asyncio.run(main())

# --- админка: включение функций и тестировщики (партнёр может) ---
from fastapi.testclient import TestClient
import web_api
with TestClient(web_api.app) as cl:
    cl.post("/admin/login", json={"password": "owner-pass-1"})
    st = cl.get("/admin/api/stats").json()["settings"]
    check("админка: видны новые функции и тестировщики", set(st["new_features"]) == {"voice", "photo_recipe", "photo_shelf"}
          and str(CHAT) in st["testers"] and "photo" in st["limits"]["free"], str(st.get("testers")))
    r = cl.post("/admin/api/settings", json={"rollout": {"voice": "all", "photo_recipe": "premium", "bad": "all", "photo_shelf": "zzz"},
                                             "testers": "123456789\n987654321, abc"})
    plans.reload()
    check("владелец включает функции и меняет тестировщиков", r.status_code == 200 and plans.rollout() == {"voice": "all", "photo_recipe": "premium", "photo_shelf": "off"}
          and plans.testers() - {"555000111"} == {"123456789", "987654321"}, r.text[:300])
    r = cl.post("/admin/api/settings", json={"limits": {"free": {"recipe": [3, 1], "daymenu": [1, 7], "week": [0, 3], "missing": [2, 1], "photo": [2, 1]}}})
    plans.reload()
    config.PAYWALL_ENABLED = True
    check("лимит фото меняется из админки", plans.limits_of("free")["photo"] == (2, 1))
    config.PAYWALL_ENABLED = False
    check("страница админки: блок новых функций", "Новые функции" in cl.get("/admin").text)
    dev = cl.post("/api/device").json()["device"]
    me = cl.get("/api/me", headers={"X-Device": dev}).json()
    check("сайт: /api/me отдаёт остаток подборов", "recipe" in me.get("left", {}), str(me)[:200])

# --- настоящий вызов распознавания фото ---
import importlib
importlib.reload(vision)
if os.path.exists("/tmp/fridge.jpg"):
    items = asyncio.run(vision.photo_items([(open("/tmp/fridge.jpg", "rb").read(), "image/jpeg")]))
    print("   на фото:", items)
    check("настоящее фото холодильника: нашёл продукты", len(items) >= 3)

print("\nИТОГ:", "ВСЁ ОК" if not fails else "ПРОВАЛЕНО: " + ", ".join(fails))
