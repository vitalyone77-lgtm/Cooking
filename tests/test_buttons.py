"""Обход всех кнопок бота: с главных экранов нажимаем каждую кнопку, до которой можно дойти, и ищем кнопки без обработчика и падения.
На сайте: у каждой кнопки с data-атрибутом есть обработчик, и общий обработчик «чипов» не перехватывает чужие кнопки."""
import asyncio
import logging
import os
import re
import sys
from datetime import datetime as dt

PROJ = "/tmp/ct"
os.chdir(PROJ)
sys.path.insert(0, PROJ)
for f in ("usage_bt.db", "accounts_bt.db", "accounts_bt.db-wal", "accounts_bt.db-shm", "user_data.json"):
    if os.path.exists(f):
        os.remove(f)
os.environ["USAGE_DB"] = PROJ + "/usage_bt.db"
os.environ["ACCOUNTS_DB"] = PROJ + "/accounts_bt.db"
os.environ["WEB_DATA_DIR"] = "web_data_bt"

from aiogram.client.bot import Bot
from aiogram.client.session.base import BaseSession
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.types import CallbackQuery, Chat, InlineKeyboardMarkup, Message, Update, User

import tg_safety
tg_safety.FloodGuard.__init__.__defaults__ = (100000, 10)
import accounts
import book
import bot as botmod
import config
import favorites
from last_recipe import set_last_recipe

fails = []


def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{x}]"))
    if not c:
        fails.append(n)


C = 797001
user, chat = User(id=C, is_bot=False, first_name="T"), Chat(id=C, type="private")
config.ADMIN_TG_IDS = [1]
config.PAYWALL_ENABLED = True
accounts.settings_set({"FEATURE_ROLLOUT": {"voice": "all", "photo_recipe": "all", "photo_shelf": "all"}}, "test")
R = ("🍽 Куриный суп\n\n⏱ Время: 40 мин | 👥 Порций: 2 | 🔥 Способ: Плита\n\n*Пищевая ценность (на 1 порцию):*\nКалорийность: 350 ккал | Б: 30 г | Ж: 9 г | У: 30 г\n\n"
     "*Продукты:*\n- Курица — 300 г\n- Рис — 100 г\n- Масло растительное — 1 ст. л.\n\n*Инструкция:*\n1. Сварите курицу 30 минут.\n2. Добавьте рис, варите 15 минут.")
D = {"cuisine": "classic", "macro_goal": "", "preferred": "", "excluded": "", "time": "30 мин", "servings": 2, "portion": "normal", "appliance": ["stove"], "appliance_labels": ["Плита"]}
for i in range(3):
    book.add({**D}, f"Суп №{i}", R.replace("Куриный суп", f"Суп №{i}"), ["курица"])
accounts.consent_set(C)
accounts.goals_set(C, feats=["diary", "shelf", "weight", "never"])
accounts.grant_days(C, 30, "promo:T")
accounts.shelf_add(C, "рис")
accounts.weight_set(C, 80)
accounts.diary_add(C, "Яйцо варёное (2 шт, 110 г)", 173, 14, 12.6, 0.8, "list")
favorites.add_favorite(C, "Куриный суп", R, "classic")
favorites.add_favorite(C, "Суп №1", R.replace("Куриный суп", "Суп №1"), "classic")
set_last_recipe(C, "Куриный суп", R, "classic")

# кнопки шагов анкеты: после сброса анкеты им положено «устареть» (тест сбрасывает состояние после каждого нажатия)
FORM = ("skip:", "servings:", "time:", "appliance:", "portion:", "meals:", "reuse:", "confirm:", "dm_", "wm_")
# тяжёлое (ИИ, публикация), необратимое и внешнее не нажимаем
SKIP = ("confirm:go", "pf:delyes", "pf:unall", "rf:", "soc:", "sf:go", "pf:pw:", "pf:buy:", "dy:calc", "fridge:", "vp:", "miss:go", "sl:rp",
        "shop:cart", "daymenu:start", "weekmenu:start", "weekmenu:cook", "dm:", "wm:", "reminder:cook", "reminder:next", "pdf:", "ck:t:", "tmr:", "dy:ask")
SKIP += tuple(getattr(tg_safety, "HEAVY_PREFIXES", ()))


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls, self.n = [], 0

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        name = type(method).__name__
        d = method.model_dump(exclude_none=True)
        self.calls.append((name, d))
        if name in ("SendMessage", "SendPhoto", "SendDocument"):
            self.n += 1
            return Message(message_id=20000 + self.n, date=dt.now(), chat=chat, text=d.get("text") or d.get("caption") or "x").as_(bot)
        if name in ("EditMessageText", "EditMessageCaption", "EditMessageMedia"):
            return Message(message_id=d.get("message_id", 1), date=dt.now(), chat=chat, text=d.get("text") or "x").as_(bot)
        return True

    async def stream_content(self, *a, **k):
        raise NotImplementedError


async def main():
    logging.disable(logging.CRITICAL)
    s = FakeSession()
    b = Bot(token="42:TEST", session=s)
    msgs: dict[int, dict] = {}        # message_id -> {"text", "markup"}
    seq = [0]

    def harvest(start: int):
        for name, d in s.calls[start:]:
            if name in ("SendMessage", "SendPhoto", "SendDocument"):
                seq_id = 20000 + len([1 for n, _ in s.calls[:s.calls.index((name, d)) + 1] if n in ("SendMessage", "SendPhoto", "SendDocument")])
                msgs[seq_id] = {"text": d.get("text") or d.get("caption") or "", "markup": d.get("reply_markup")}
            elif name in ("EditMessageText", "EditMessageCaption") and d.get("message_id"):
                m = msgs.setdefault(d["message_id"], {"text": "", "markup": None})
                m["text"] = d.get("text") or d.get("caption") or m["text"]
                if "reply_markup" in d:
                    m["markup"] = d.get("reply_markup")
            elif name == "EditMessageReplyMarkup" and d.get("message_id"):
                msgs.setdefault(d["message_id"], {"text": "", "markup": None})["markup"] = d.get("reply_markup")

    async def press(mid: int, data: str):
        seq[0] += 1
        m = msgs.get(mid) or {}
        mk = InlineKeyboardMarkup.model_validate(m["markup"]) if isinstance(m.get("markup"), dict) and m["markup"].get("inline_keyboard") is not None else None
        msg = Message(message_id=mid, date=dt.now(), chat=chat, text=m.get("text") or "x", reply_markup=mk)
        upd = Update(update_id=seq[0], callback_query=CallbackQuery(id=str(seq[0]), from_user=user, chat_instance="x", message=msg, data=data))
        return await botmod.dp.feed_update(b, upd)

    async def command(text: str):
        seq[0] += 1
        start = len(s.calls)
        await botmod.dp.feed_update(b, Update(update_id=seq[0], message=Message(message_id=500 + seq[0], date=dt.now(), chat=chat, from_user=user, text=text)))
        harvest(start)

    # входы: команды и рецепт с кнопками
    for cmd in ("/menu", "/profile", "/help", "/ready", "/label"):
        await command(cmd)
    s.n += 1
    msgs[20000 + s.n] = {"text": R, "markup": botmod.recipe_kb(R, C).model_dump(exclude_none=True)}
    st = botmod.dp.fsm.get_context(b, C, C)
    await st.clear()

    seen, unhandled, crashed, pressed, stale_form, silent = set(), [], [], 0, [], []
    queue = [(mid, r["callback_data"]) for mid, m in list(msgs.items()) for row in ((m.get("markup") or {}).get("inline_keyboard") or []) for r in row if r.get("callback_data")]
    while queue and pressed < 600:
        mid, data = queue.pop(0)
        key = data if not re.search(r":\d+$", data) else re.sub(r"\d+$", "#", data)      # одинаковые кнопки с разными номерами нажимаем один раз
        if key in seen or data.startswith(SKIP) or data in ("bk:noop", "sl:n"):
            continue
        seen.add(key)
        start = len(s.calls)
        try:
            res = await press(mid, data)
            pressed += 1
            if not any(n == "AnswerCallbackQuery" and d.get("callback_query_id") == str(seq[0]) for n, d in s.calls[start:]):
                silent.append(data)       # Telegram не получил ответ — на кнопке крутятся «часики»
            stale = any(n == "AnswerCallbackQuery" and d.get("text") == botmod.STALE_TEXT for n, d in s.calls[start:])
            if res is UNHANDLED:
                unhandled.append(data)
            elif stale and not data.startswith(FORM):
                unhandled.append(data + " (устарела)")
            elif stale:
                stale_form.append(data)
        except Exception as e:  # noqa: BLE001
            crashed.append(f"{data}: {type(e).__name__}: {str(e)[:120]}")
        await st.clear()
        harvest(start)
        for nmid in {d.get("message_id") for n, d in s.calls[start:] if d.get("message_id")} | {20000 + k for k in range(s.n - 5, s.n + 1)}:
            for row in ((msgs.get(nmid) or {}).get("markup") or {}).get("inline_keyboard") or []:
                for r in row:
                    if r.get("callback_data"):
                        queue.append((nmid, r["callback_data"]))
    print(f"нажато кнопок: {pressed}, разных: {len(seen)}; шаги анкеты после сброса честно говорят «устарела»: {stale_form}")
    s0 = len(s.calls)
    await press(next(iter(msgs)), "zz:старая-кнопка")
    check("устаревшая кнопка: понятное сообщение и меню, а не тишина", any(n == "AnswerCallbackQuery" and d.get("show_alert") for n, d in s.calls[s0:])
          and any(n in ("SendMessage", "EditMessageText") for n, d in s.calls[s0:]))
    check("обход дошёл до большинства экранов", pressed > 60, str(pressed))
    check("нет кнопок без обработчика", not unhandled, "; ".join(unhandled[:20]))
    check("ни одна кнопка не падает", not crashed, " | ".join(crashed[:10]))
    check("на каждое нажатие Telegram получает ответ (нет «вечных часиков»)", not silent, "; ".join(silent[:30]))
    for must in ("dy:open", "pf:set", "pf:sub", "pf:fr", "ps:o", "sl:o:r", "bk:h", "favorites:open", "cb:o", "pf:shelf", "pf:shph", "lb:x"):
        check(f"кнопка «{must}» нажималась", any(k == must or k.startswith(must) for k in seen), must)
    periods = [k for k in seen if k.startswith("pf:pd") or k.startswith("dy:ch") or "pf:per" in k]
    print("кнопки периодов:", periods)


asyncio.run(main())

# ---- сайт: каждая кнопка доходит до своего обработчика ----
html = open("web/index.html", encoding="utf-8").read()
start = html.index("// ---- единый обработчик кликов ----")
handler = html[start:html.index("});", html.index("if(b=t.closest(\"[data-cook]\")", start))]
order = [m.group(1) for m in re.finditer(r'closest\("(\[data-[a-z0-9-]+\]|\.[a-z-]+)"\)', handler)]
chip_at = order.index(".chip")
other = html[:start] + html[html.index("});", html.index("if(b=t.closest(\"[data-cook]\")", start)):]
buttons = set()
for m in re.finditer(r"<button([^>]*)>", html):
    attrs = m.group(1)
    for a in re.findall(r"(data-[a-z0-9-]+)=", attrs):
        buttons.add((a, "chip" in attrs))
handled = {o[1:-1] for o in order if o.startswith("[")}
inner = {"data-key", "data-sub", "data-name", "data-multi"}                       # разбираются внутри обработчика «чипов»
passive = {"data-g", "data-q", "data-k", "data-min", "data-max", "data-label", "data-i", "data-n", "data-v", "data-o", "data-p", "data-rm"}
dead, stolen = [], []
for a, is_chip in sorted(buttons):
    if a in inner or a in passive:
        continue
    if a not in handled and f'"{a}"' not in other and f"[{a}]" not in other and f"'{a}'" not in other:
        dead.append(a)
    elif is_chip and f"[{a}]" in order and order.index(f"[{a}]") > chip_at:
        stolen.append(a)
check("сайт: у каждой кнопки есть обработчик", not dead, ", ".join(dead))
check("сайт: общий обработчик «чипов» не перехватывает чужие кнопки", not stolen, ", ".join(stolen))
check("сайт: кнопки периода дневника обрабатываются раньше «чипов»", order.index("[data-dy-days]") < chip_at)

print("ИТОГО: упало", len(fails))
sys.exit(1 if fails else 0)
