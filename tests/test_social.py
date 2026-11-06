"""«Рецепт дня»: текст поста, публикация в ВК (запросы подменены), защита от повторов, ручной режим /postvk."""
import asyncio
import os
import sys
from datetime import datetime

PROJ = "/tmp/ct"
os.chdir(PROJ)
sys.path.insert(0, PROJ)
for f in ("usage_so.db", "accounts_so.db", "accounts_so.db-wal", "accounts_so.db-shm", "user_data.json"):
    if os.path.exists(f):
        os.remove(f)
os.environ["USAGE_DB"] = PROJ + "/usage_so.db"
os.environ["ACCOUNTS_DB"] = PROJ + "/accounts_so.db"
os.environ["WEB_DATA_DIR"] = "web_data_so"

from aiogram.client.bot import Bot
from aiogram.client.session.base import BaseSession
from aiogram.types import Update, CallbackQuery, Message, Chat, User

import config
SECRET = "vk1.a.СЕКРЕТНЫЙ_КЛЮЧ_123"
config.VK_GROUP_ID, config.VK_GROUP_TOKEN = 123456, SECRET
config.TG_CHANNEL_ID = ""
config.ADMIN_TG_IDS = [880099]
config.SOCIAL_AUTOPOST = False
config.SOCIAL_IMAGES = False
config.VK_SECTION_PHOTOS = ""       # тест не зависит от .env сервера
import accounts
import social
import bot as botmod

fails = []


def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{x}]"))
    if not c:
        fails.append(n)


RECIPE = ("🍽 Куриный суп с лапшой\n\n⏱ Время: 30 мин | 👥 Порций: 2 | 🔥 Способ: Плита\n\n"
          "*КБЖУ на порцию:* 320 ккал, Б 24 г, Ж 9 г, У 36 г\n\n*Продукты:*\n- Куриное филе — 200 г\n- Лапша — 80 г\n\n"
          "*Инструкция:*\n1. Свари бульон.\n2. Добавьте лапшу.\n\nПРОДУКТЫ_СПИСОК: курица, лапша")
gen_calls = []


async def fake_search(data):
    return "q", []


async def fake_gen(data, q, r):
    gen_calls.append(data)
    return RECIPE


social.search_recipes, social.generate_recipe = fake_search, fake_gen

vk_calls = []
VK_REPLY = [{"response": {"post_id": 77}}]
RESOLVE_REPLY = [{"response": {"groups": [{"id": 424242}]}}]


class FakeResp:
    def __init__(self, data):
        self._d = data

    def json(self):
        return self._d


class FakeClient:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, data=None, **k):
        if "/method/photos." in url:                # загрузка фото отдельно проверена в test_social_images: тут ключ её «не умеет»
            return FakeResp({"error": {"error_code": 27, "error_msg": "Group authorization failed"}})
        vk_calls.append((url, dict(data or {})))
        return FakeResp(RESOLVE_REPLY[0] if url.endswith("groups.getById") else VK_REPLY[0])


social.httpx.AsyncClient = FakeClient


async def main():
    title, text = await social.build_post()
    check("название блюда достаётся из рецепта", "Куриный суп" in title, title)
    check("текст поста без разметки Telegram и служебных строк", "*" not in text and "ПРОДУКТЫ_СПИСОК" not in text, text[:200])
    check("в посте ссылка на бота с меткой ref_vk, ссылка на сайт и хештеги",
          f"t.me/{config.BOT_USERNAME}?start=ref_vk" in text and "utm_source=vk" in text and "#рецептдня" in text, text[-300:])
    check("в запросе к ИИ тема дня и список прошлых блюд", gen_calls[-1]["variety_hint"] in [h for _, h, _ in social.SECTIONS] and gen_calls[-1]["avoid_titles"] == [])

    bot = None
    res = await social.publish(bot, title, text)
    url, data = vk_calls[-1]
    check("запись уходит в сообщество (минус перед id) от имени сообщества", url.endswith("/method/wall.post") and data["owner_id"] == -123456 and data["from_group"] == 1, str(data)[:80])
    check("текст и ключ переданы, версия API указана", data["message"] == text and data["access_token"] == SECRET and data["v"] == config.VK_API_VERSION)
    check("результат: номер записи", res["vk"] == 77 and not res["errors"], str(res))
    check("название запомнено, чтобы не повторять", social._history() == [title], str(social._history()))
    await social.build_post()
    check("в следующий раз ИИ просят не повторять прошлые блюда", gen_calls[-1]["avoid_titles"] == [title])

    # короткий адрес сообщества вместо номера
    posts = lambda: [c for c in vk_calls if c[0].endswith("wall.post")]
    lookups = lambda: [c for c in vk_calls if c[0].endswith("groups.getById")]
    check("разбор записи сообщества: номер, club, ссылка, короткий адрес",
          [social.normalize_group(x) for x in ("123", "-123", "club123", "https://vk.com/club123", "https://vk.com/kbzum?from=x", " kbzum ")]
          == ["123", "123", "123", "123", "kbzum", "kbzum"])
    social._gid_cache.clear()
    config.VK_GROUP_ID = "kbzum"
    res = await social.publish(bot, "Блюдо с коротким адресом", text)
    check("короткий адрес: номер узнан у ВК, запись ушла в это сообщество", res["vk"] == 77 and posts()[-1][1]["owner_id"] == -424242, str(res))
    check("в запросе номера нет ключа в тексте ошибки, а имя передано", lookups()[-1][1]["group_ids"] == "kbzum")
    await social.publish(bot, "Ещё блюдо", text)
    check("номер запоминается: второй запрос к ВК за номером не нужен", len(lookups()) == 1, str(len(lookups())))
    config.VK_GROUP_ID = "https://vk.com/club555"
    await social.publish(bot, "Ссылкой", text)
    check("ссылка с club: номер берётся из неё без запроса", posts()[-1][1]["owner_id"] == -555 and len(lookups()) == 1)
    social._gid_cache.clear()
    config.VK_GROUP_ID = "ghost"
    RESOLVE_REPLY[0] = {"error": {"error_code": 100, "error_msg": "group_ids is undefined"}}
    res = await social.publish(bot, "Не найдено", text)
    check("не удалось узнать номер: понятный совет, ключа в тексте нет", res["vk"] is None and "Впишите номер" in res["errors"][0] and SECRET not in res["errors"][0], str(res))
    social._gid_cache.clear()
    config.VK_GROUP_ID = "old"
    RESOLVE_REPLY[0] = {"response": [{"id": 9}]}
    res = await social.publish(bot, "Старый формат", text)
    check("старый формат ответа ВК тоже понимается", res["vk"] == 77 and posts()[-1][1]["owner_id"] == -9)
    RESOLVE_REPLY[0] = {"response": {"groups": [{"id": 424242}]}}
    config.VK_GROUP_ID = 123456

    # ошибка ВК: понятный текст без ключа
    VK_REPLY[0] = {"error": {"error_code": 214, "error_msg": "Access to adding post denied"}}
    res = await social.publish(bot, "Другое блюдо", text)
    check("отказ ВК: понятная ошибка без ключа", res["vk"] is None and res["errors"] and "214" in res["errors"][0] and SECRET not in res["errors"][0], str(res))
    check("при отказе блюдо не запоминается", "Другое блюдо" not in social._history())
    VK_REPLY[0] = {"response": {"post_id": 78}}

    # без настройки
    config.VK_GROUP_TOKEN = ""
    check("без ключа публиковать некуда, ошибок нет", (await social.publish(bot, "x", "y")) == {"vk": None, "tg": None, "errors": []})
    try:
        await social.post_vk("y")
        ok = False
    except RuntimeError as e:
        ok = "не настроен" in str(e)
    check("прямой вызов без ключа — понятная ошибка", ok)
    config.VK_GROUP_TOKEN = SECRET

    # расписание: автопубликация выключена / включена / раз в день
    accounts.sess_set(social.LAST_KEY, {})
    n0 = len(vk_calls)
    await social.daily_job(bot)
    check("автопубликация выключена — ничего не уходит", len(vk_calls) == n0)
    config.SOCIAL_AUTOPOST = True
    await social.daily_job(bot)
    check("автопубликация включена — одна запись", len(vk_calls) == n0 + 1)
    await social.daily_job(bot)
    check("второй раз за день не публикует", len(vk_calls) == n0 + 1)
    config.SOCIAL_AUTOPOST = False

    # ручной режим /postvk
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
                return Message(message_id=9500 + self.n, date=datetime.now(), chat=Chat(id=A, type="private"),
                               text=getattr(method, "text", "") or "").as_(bot)
            return True

        async def stream_content(self, *a, **k):
            raise NotImplementedError

    A = 880099
    s = FakeSession()
    b = Bot(token="42:TEST", session=s)
    seq = [0]

    def msg(uid, text):
        seq[0] += 1
        return Update(update_id=seq[0], message=Message(message_id=seq[0] + 10, date=datetime.now(), chat=Chat(id=uid, type="private"),
                                                        from_user=User(id=uid, is_bot=False, first_name="T"), text=text))

    def cb(uid, data):
        seq[0] += 1
        m = Message(message_id=700, date=datetime.now(), chat=Chat(id=uid, type="private"), text="x")
        return Update(update_id=seq[0], callback_query=CallbackQuery(id=str(seq[0]), from_user=User(id=uid, is_bot=False, first_name="T"),
                                                                     chat_instance="x", message=m, data=data))

    def last(name):
        return next((p for n, p in reversed(s.calls) if n == name), None)

    def btns(p):
        return [x["text"] for r in (p or {}).get("reply_markup", {}).get("inline_keyboard", []) for x in r]

    n0 = len(vk_calls)
    await botmod.dp.feed_update(b, msg(555, "/postvk"))
    check("чужому /postvk не отвечает", not s.calls)
    await botmod.dp.feed_update(b, msg(A, "/postvk"))
    m = [p for n, p in s.calls if n == "SendMessage" and "Черновик" in p.get("text", "")]
    check("владелец получает черновик с кнопками", m and {"✅ Опубликовать", "🔄 Другой рецепт", "Отмена"} <= set(btns(m[-1])) and "Куриный суп" in m[-1]["text"], str(s.calls[-2:])[:200])
    check("до нажатия ничего не опубликовано", len(vk_calls) == n0)
    await botmod.dp.feed_update(b, cb(555, "soc:go"))
    check("чужое нажатие ничего не публикует", len(vk_calls) == n0)
    await botmod.dp.feed_update(b, cb(A, "soc:go"))
    check("«Опубликовать» — запись ушла, владелец получил номер", len(vk_calls) == n0 + 1 and "запись №78" in last("SendMessage")["text"], last("SendMessage")["text"])
    await botmod.dp.feed_update(b, cb(A, "soc:go"))
    check("повторное нажатие не публикует второй раз", len(vk_calls) == n0 + 1)
    await botmod.dp.feed_update(b, cb(A, "soc:next"))
    check("«Другой рецепт» присылает новый черновик", "Черновик" in last("SendMessage")["text"])

asyncio.run(main())
print("\nИТОГ:", "ВСЁ ОК" if not fails else "ПРОВАЛЕНО: " + ", ".join(fails))
