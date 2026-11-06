"""«Нет каких-то продуктов»: рецепт, меню на день, план (начатый и нет) — на настоящих обработчиках bot.py, ИИ подменён."""
import asyncio
import json
import os
import sys
from datetime import datetime

PROJ = "/tmp/ct"
os.chdir(PROJ)
sys.path.insert(0, PROJ)

from aiogram.client.bot import Bot
from aiogram.client.session.base import BaseSession
from aiogram.types import Update, CallbackQuery, Message, Chat, User

import bot as botmod
import day_menu
import week_menu
import missing

CHAT = 777000003
fails = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        self.calls.append((type(method).__name__, method.model_dump(exclude_none=True)))
        if type(method).__name__ in ("SendMessage", "EditMessageText"):
            return Message(message_id=900 + len(self.calls), date=datetime.now(), chat=Chat(id=CHAT, type="private"),
                           text=getattr(method, "text", "") or "").as_(bot)
        return True

    async def stream_content(self, *a, **k):
        raise NotImplementedError


user = User(id=CHAT, is_bot=False, first_name="T")
chat = Chat(id=CHAT, type="private")
RECIPE = ("🍽 Куриное филе с рисом\n\nПищевая ценность (на 1 порцию):\nКалорийность: 585 ккал\n\nПродукты:\n"
          "- Куриное филе — 300 г\n- Кабачок — 200 г\n- Рис — 140 г\n\nИнструкция:\n1. Варить.")
NEW_RECIPE = ("🍽 Куриное филе с рисом и морковью\n\nПродукты:\n- Куриное филе — 300 г\n- Морковь — 200 г\n- Рис — 140 г\n\n"
              "Инструкция:\n1. Варить.\nПРОДУКТЫ_СПИСОК: куриное филе, морковь, рис")
uid = [0]


def cb(data, text="…", reply_text=None, reply_id=555, msg_id=600):
    uid[0] += 1
    reply = Message(message_id=reply_id, date=datetime.now(), chat=chat, text=reply_text, from_user=user) if reply_text is not None else None
    msg = Message(message_id=msg_id, date=datetime.now(), chat=chat, text=text, reply_to_message=reply, from_user=user)
    return Update(update_id=uid[0], callback_query=CallbackQuery(id=str(uid[0]), from_user=user, chat_instance="x", message=msg, data=data))


def text_update(text, reply_id, reply_text):
    uid[0] += 1
    reply = Message(message_id=reply_id, date=datetime.now(), chat=chat, text=reply_text, from_user=user)
    return Update(update_id=uid[0], message=Message(message_id=700 + uid[0], date=datetime.now(), chat=chat, text=text,
                                                    reply_to_message=reply, from_user=user))


async def run(s, update):
    s.calls.clear()
    b = Bot(token="123456:TEST-TOKEN-NOT-REAL-1234567890abcdef", session=s)
    await botmod.dp.feed_update(b, update)
    return list(s.calls)


def buttons(p):
    return [x["text"] for row in (p.get("reply_markup") or {}).get("inline_keyboard", []) for x in row]


async def main():
    s = FakeSession()
    llm = {"prompts": []}

    async def fake_llm(messages, **kw):
        llm["prompts"].append(messages[-1]["content"])
        return NEW_RECIPE
    missing.call_llm_with_fallback = fake_llm

    # ---- рецепт ----
    calls = await run(s, cb("miss:open:r", text=RECIPE, reply_text=None, msg_id=600))
    send = next((p for n, p in calls if n == "SendMessage"), None)
    check("рецепт: открылся выбор продуктов", send is not None and "Каких продуктов нет" in send["text"], str(calls)[:300])
    b = buttons(send) if send else []
    check("кнопки: 3 продукта + «Заменить»", len(b) == 4 and b[0].endswith("Куриное филе") and b[-1].startswith("✅ Заменить"), str(b))
    picker = next(iter(botmod._missing_pick))[1]

    # нажатие «Заменить» без выбора
    calls = await run(s, cb("miss:go:r", text="выбор", reply_text=RECIPE, reply_id=600, msg_id=picker))
    ans = next((p for n, p in calls if n == "AnswerCallbackQuery"), None)
    check("без выбора — подсказка", ans is not None and ans.get("show_alert") is True and not llm["prompts"])

    calls = await run(s, cb("miss:t:r:1", text="выбор", reply_text=RECIPE, reply_id=600, msg_id=picker))
    edit = next((p for n, p in calls if n == "EditMessageReplyMarkup"), None)
    check("переключатель отмечает продукт ❌", edit is not None and any(x.startswith("❌") and "Кабачок" in x for x in buttons(edit)), str(calls)[:300])
    calls = await run(s, text_update("баклажаны, цветная капуста", picker, "выбор"))
    t = next((p["text"] for n, p in calls if n == "SendMessage"), "")
    check("ответ текстом добавляет свои продукты", "баклажаны" in t and "цветная капуста" in t, t)

    calls = await run(s, cb("miss:go:r", text="выбор", reply_text=RECIPE, reply_id=600, msg_id=picker))
    sends = [p for n, p in calls if n == "SendMessage"]
    check("в ИИ ушли отмеченные и написанные продукты", llm["prompts"] and "Кабачок" in llm["prompts"][0] and "баклажаны" in llm["prompts"][0]
          and "ЭТИХ ПРОДУКТОВ НЕТ" in llm["prompts"][0], llm["prompts"][:1] and llm["prompts"][0][:200])
    check("в ИИ ушёл исходный рецепт", llm["prompts"] and "Куриное филе — 300 г" in llm["prompts"][0])
    last = sends[-1] if sends else {}
    check("пришёл новый рецепт с кнопками", "морковью" in last.get("text", "") and "ПРОДУКТЫ_СПИСОК" not in last.get("text", "")
          and "🛒 Продукты и магазины" in buttons(last) and "👨‍🍳 Готовить по шагам" in buttons(last), str(sends)[:300])
    check("выбор очищен", (CHAT, picker) not in botmod._missing_pick)

    # ---- меню на день ----
    base = {"cuisine": "classic", "excluded": "орехи", "time": "30", "servings": 1, "meals": ["lunch"]}
    day_menu._save_menu(CHAT, {"order": ["lunch"], "meals": {"lunch": {"title": "Т", "text": RECIPE, "ok": True}},
                               "shopping_terms": ["куриное филе", "кабачок", "рис"], "base_data": base})
    got = {}

    async def fake_gen(chat_id, meals, data):
        got["meals"], got["data"] = meals, data
        return {"meals": [], "shopping_message": ""}
    day_menu.generate_day_menu = fake_gen
    calls = await run(s, cb("miss:open:d", text="Меню", msg_id=610))
    picker_d = [k for k in botmod._missing_pick if k[0] == CHAT][-1][1]
    await run(s, cb("miss:t:d:1", text="выбор", msg_id=picker_d))
    calls = await run(s, cb("miss:go:d", text="выбор", msg_id=picker_d))
    entry = day_menu._load_menu(CHAT)
    check("меню на день: правится только блюдо с этим продуктом, а не всё меню", not got and llm["prompts"]
          and "кабачок" in llm["prompts"][-1].lower() and "ТО ЖЕ САМОЕ БЛЮДО" in llm["prompts"][-1], str(got)[:100])
    check("меню на день: продукт в исключениях на следующий раз", entry["base_data"]["excluded"] == "орехи, кабачок", str(entry.get("base_data")))
    check("меню на день: из списка покупок ушёл кабачок", "кабачок" not in entry["shopping_terms"], str(entry["shopping_terms"]))

    # ---- план: ещё не начат ----
    def plan(days):
        return {"base_data": {"cuisine": "classic", "excluded": ""}, "meal_keys": ["lunch"], "days_total": 3,
                "inventory": {"курица": 900, "кабачки": 500}, "inventory_initial": {"курица": 900, "кабачки": 500},
                "basket_text": "Корзина", "history_titles": [], "days": days, "current_day": 1 if not days else 2, "progress_index": 1 if days else 0}
    week_menu._save(CHAT, plan({}))
    rebuilt = {}

    async def fake_start(chat_id, meal_keys, days_total, data):
        rebuilt["data"] = data
        return "Новая корзина", ""
    week_menu.start_week_plan = fake_start
    mode = await missing.adjust_plan(CHAT, ["кабачки"])
    check("план без дней: корзина пересобирается без продукта", mode == "rebuild" and rebuilt["data"]["excluded"] == "кабачки", str(rebuilt))

    # ---- план: уже начат ----
    week_menu._save(CHAT, plan({"1": {"order": ["lunch"], "meals": {"lunch": {"title": "Т", "text": "т"}}, "shopping_message": "", "shopping_terms": []}}))
    mode = await missing.adjust_plan(CHAT, ["кабачки"])
    st = week_menu._load(CHAT)
    check("начатый план: день и прогресс сохранены", mode == "future" and "1" in st["days"] and st["progress_index"] == 1 and st["current_day"] == 2)
    check("начатый план: продукт убран из остатков и корзины", "кабачки" not in st["inventory"] and "кабачки" not in st["inventory_initial"] and "курица" in st["inventory"])
    check("начатый план: продукт в исключениях будущих дней", st["base_data"]["excluded"] == "кабачки")

    # ---- тексты-кнопки в нужных местах ----
    check("в сводке меню на день — список покупок (там замена)", any("Продукты и магазин" in x.text for r in day_menu.build_summary_view(CHAT)[1].inline_keyboard for x in r) if day_menu.build_summary_view(CHAT) else False)
    import keyboards as K
    check("под корзиной плана — список покупок (там замена)", "🛒 Продукты и магазины" in [x.text for r in K.shopping_btn_kb("w").inline_keyboard for x in r])
    check("кнопки нет у «докупить к дню»", "❌ Нет каких-то продуктов" not in [x.text for r in K.shopping_btn_kb("wd2").inline_keyboard for x in r])
    check("кнопка есть при выборе магазина", "❌ Нет каких-то продуктов" in [x.text for r in K.store_pick_kb("r", ["fiveka"]).inline_keyboard for x in r])

    # очистка
    check("clean_missing: повторы и пустые", missing.clean_missing([" Кабачки ", "кабачки", "", "Лук."]) == ["Кабачки", "Лук"])


asyncio.run(main())
for fname in ("day_menu.json", "week_menu.json", "store_prefs.json"):
    pth = os.path.join(PROJ, fname)
    if os.path.exists(pth):
        data = json.load(open(pth, encoding="utf-8"))
        data.pop(str(CHAT), None)
        json.dump(data, open(pth, "w", encoding="utf-8"), ensure_ascii=False)
print("\nИТОГ:", "ВСЁ ОК" if not fails else "ПРОВАЛЕНО: " + ", ".join(fails))
sys.exit(1 if fails else 0)
