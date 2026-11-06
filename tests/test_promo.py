"""Недельные задания по продвижению: только для владельца, отметки сохраняются, тексты влезают в Telegram, рассылка раз в неделю."""
import asyncio
import os
import sys
from datetime import datetime as dt

PROJ = "/tmp/ct"
os.chdir(PROJ)
sys.path.insert(0, PROJ)
for f in ("usage_pr.db", "accounts_pr.db", "accounts_pr.db-wal", "accounts_pr.db-shm"):
    if os.path.exists(f):
        os.remove(f)
os.environ["USAGE_DB"] = PROJ + "/usage_pr.db"
os.environ["ACCOUNTS_DB"] = PROJ + "/accounts_pr.db"
os.environ["WEB_DATA_DIR"] = "web_data_pr"

from aiogram.client.bot import Bot
from aiogram.client.session.base import BaseSession
from aiogram.types import Update, CallbackQuery, Message, Chat, User

import config
import accounts
import promo_plan as plan
import bot as botmod
import bot_promo

fails = []


def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{x}]"))
    if not c:
        fails.append(n)


ADMIN, STRANGER = 770001, 770002
config.ADMIN_TG_IDS[:] = [ADMIN]

check("в плане есть недели, у всех заданий шаги и короткий id", len(plan.TASKS) >= 10 and all(t["steps"] and len(t["id"]) <= 8 for t in plan.TASKS))
check("идентификаторы уникальны", len({t["id"] for t in plan.TASKS}) == len(plan.TASKS))
check("платных заданий не больше чем на 5000 ₽ за цикл", plan.BUDGET <= 5000, str(plan.BUDGET))
check("есть бесплатные и платные задания", any(t["cost"] == 0 for t in plan.TASKS) and any(t["cost"] > 0 for t in plan.TASKS))
check("каждая карточка влезает в сообщение Telegram и без разметки не ломается", all(len(bot_promo.task_text(t, {"marks": {}})) < 4000 for t in plan.TASKS))
check("платные задания напоминают про маркировку рекламы", all("маркир" in (t["text"] + " ".join(t["steps"])).lower() for t in plan.TASKS if t["cost"] and "erid" in (t["text"] + " ".join(t["steps"]))) )
check("все картинки из плана существуют в проекте", all(os.path.isfile(os.path.join(PROJ, p)) for t in plan.TASKS for p in t["image"]), str([p for t in plan.TASKS for p in t["image"] if not os.path.isfile(os.path.join(PROJ, p))]))
check("кнопки заданий не длиннее 64 байт", all(len(f"pr:t:{t['id']}".encode()) <= 64 for t in plan.TASKS))
check("в готовых текстах нет чужих и пустых ссылок", all("t.me/Cookingvkusno_bot" in t["text"] or "menu.ymayaka.ru" in t["text"] for t in plan.TASKS if "Бот" in t["text"] or "Попробовать" in t["text"]))


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls, self.n = [], 0

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        name = type(method).__name__
        self.calls.append((name, method.model_dump(exclude_none=True)))
        if name in ("SendMessage", "EditMessageText", "SendDocument"):
            self.n += 1
            return Message(message_id=9000 + self.n, date=dt.now(), chat=Chat(id=ADMIN, type="private"), text=getattr(method, "text", "") or "x").as_(bot)
        return True

    async def stream_content(self, *a, **k):
        raise NotImplementedError


seq = [0]


def mk(uid):
    user, chat = User(id=uid, is_bot=False, first_name="T"), Chat(id=uid, type="private")

    def cb(data):
        seq[0] += 1
        m = Message(message_id=500, date=dt.now(), chat=chat, text="x")
        return Update(update_id=seq[0], callback_query=CallbackQuery(id=str(seq[0]), from_user=user, chat_instance="x", message=m, data=data))

    def msg(text):
        seq[0] += 1
        return Update(update_id=seq[0], message=Message(message_id=100 + seq[0], date=dt.now(), chat=chat, from_user=user, text=text,
                                                        entities=[{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}] if text.startswith("/") else None))
    return cb, msg


def btns(p):
    return [x["text"] for r in (p or {}).get("reply_markup", {}).get("inline_keyboard", []) for x in r]


async def main():
    s = FakeSession()
    b = Bot(token="42:TEST", session=s)
    cb, msg = mk(ADMIN)
    last = lambda *names: next((p for n, p in reversed(s.calls) if n in names), None)

    await botmod.dp.feed_update(b, msg("/promo"))
    m = last("SendMessage")
    check("/promo показывает неделю 1 с заданиями и бюджетом", "неделя 1" in m["text"] and "Оформить сообщество" in m["text"] and "Потрачено: 0" in m["text"], str(m)[:300])
    check("у каждого задания своя кнопка и есть переход на следующую неделю", len([x for x in btns(m) if x.startswith("⬜")]) == 3 and any("Неделя 2" in x for x in btns(m)), str(btns(m)))

    await botmod.dp.feed_update(b, cb("pr:t:w1a"))
    m = last("EditMessageText")
    check("карточка: шаги, готовый текст со ссылкой на бота, кнопки", "Шаги:" in m["text"] and "ref_vk" in m["text"] and {"✅ Сделано", "⏭ Пропустить", "🖼 Получить картинку"} <= set(btns(m)), str(btns(m)))

    n_docs = len([1 for n, _ in s.calls if n == "SendDocument"])
    await botmod.dp.feed_update(b, cb("pr:i:w1a"))
    check("кнопка «Получить картинку» присылает файлы (аватар и обложка)", len([1 for n, _ in s.calls if n == "SendDocument"]) == n_docs + 2)

    await botmod.dp.feed_update(b, cb("pr:d:w1a"))
    m = last("EditMessageText")
    check("«Сделано» отмечает задание", "✅ Оформить сообщество" in m["text"] and bot_promo._state()["marks"]["w1a"] == "done")
    await botmod.dp.feed_update(b, cb("pr:s:w1b"))
    check("«Пропустить» отмечает значком ⏭", bot_promo._state()["marks"]["w1b"] == "skip" and "⏭" in last("EditMessageText")["text"])
    check("отметки не сгорают через 3 дня", accounts._q("SELECT ts FROM sess WHERE key='promo:state'", one=True)[0] > 3e12)

    # прогресс не теряется между сообщениями и неделя переключается сама
    await botmod.dp.feed_update(b, cb("pr:d:w1c"))
    check("неделя 1 пройдена — текущей становится 2", bot_promo.current_week() == 2)
    await botmod.dp.feed_update(b, msg("/promo"))
    check("/promo теперь открывает неделю 2", "неделя 2" in last("SendMessage")["text"])
    await botmod.dp.feed_update(b, cb("pr:w:1"))
    check("можно вернуться к прошлой неделе, отметки на месте", "✅ Оформить сообщество" in last("EditMessageText")["text"])

    # плохие данные в кнопках
    for bad in ("pr:t:нет", "pr:w:99", "pr:w:abc", "pr:d:нет", "pr:i:нет", "pr:t:", "pr:d:"):
        await botmod.dp.feed_update(b, cb(bad))
    check("испорченные кнопки не роняют бота и не портят отметки", len(bot_promo._state()["marks"]) == 3)

    # посторонний
    s2 = FakeSession()
    b2 = Bot(token="42:TEST", session=s2)
    cb2, msg2 = mk(STRANGER)
    await botmod.dp.feed_update(b2, msg2("/promo"))
    await botmod.dp.feed_update(b2, cb2("pr:d:w2a"))
    await botmod.dp.feed_update(b2, cb2("pr:t:w2a"))
    await botmod.dp.feed_update(b2, msg2("/promo_reset"))
    check("посторонним ничего не отвечает и не показывает", not [1 for n, p in s2.calls if n in ("SendMessage", "EditMessageText", "SendDocument") and ("Продвижение" in p.get("text", "") or "Шаги" in p.get("text", ""))])
    check("посторонний не может отметить или сбросить", "w2a" not in bot_promo._state()["marks"] and len(bot_promo._state()["marks"]) == 3)

    # недельная рассылка
    s3 = FakeSession()
    b3 = Bot(token="42:TEST", session=s3)
    await bot_promo.weekly_job(b3)
    sent = [p for n, p in s3.calls if n == "SendMessage"]
    check("в понедельник владельцу приходит неделя 2 с кнопками", len(sent) == 1 and sent[0]["chat_id"] == ADMIN and "неделя 2" in sent[0]["text"])
    await bot_promo.weekly_job(b3)
    check("повторный запуск в ту же неделю ничего не шлёт", len([1 for n, _ in s3.calls if n == "SendMessage"]) == 1)
    st = bot_promo._state()
    st["sent"] = "1999-1"
    bot_promo._save(st)

    class Boom(FakeSession):
        async def make_request(self, bot, method, timeout=None):
            raise RuntimeError("telegram down")
    await bot_promo.weekly_job(Bot(token="42:TEST", session=Boom()))
    check("сбой Telegram при рассылке не роняет планировщик", True)

    # всё пройдено
    st = bot_promo._state()
    for t in plan.TASKS:
        st["marks"][t["id"]] = "done"
    bot_promo._save(st)
    s4 = FakeSession()
    b4 = Bot(token="42:TEST", session=s4)
    await bot_promo.weekly_job(b4)
    check("всё пройдено — рассылки нет", not [1 for n, _ in s4.calls if n == "SendMessage"])
    await botmod.dp.feed_update(b4, msg("/promo"))
    check("/promo при всех выполненных предлагает итоги", "Все задания пройдены" in last_text(s4) if (last_text := lambda ss: next((p.get("text", "") for n, p in reversed(ss.calls) if n == "SendMessage"), "")) else False)
    await botmod.dp.feed_update(b4, msg("/promo_reset"))
    check("сброс начинает с первой недели", bot_promo.current_week() == 1)

asyncio.run(main())
print("\nИТОГ:", "ВСЁ ОК" if not fails else "ПРОВАЛЕНО: " + ", ".join(fails))
