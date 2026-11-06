"""Картинки к рецепту дня: Leonardo (запросы подменены), загрузка в ВК, канал, кнопки владельца, автокартинка."""
import asyncio
import os
import sys
from datetime import datetime

PROJ = "/tmp/ct"
os.chdir(PROJ)
sys.path.insert(0, PROJ)
for f in ("usage_si.db", "accounts_si.db", "accounts_si.db-wal", "accounts_si.db-shm", "user_data.json"):
    if os.path.exists(f):
        os.remove(f)
os.environ["USAGE_DB"] = PROJ + "/usage_si.db"
os.environ["ACCOUNTS_DB"] = PROJ + "/accounts_si.db"
os.environ["WEB_DATA_DIR"] = "web_data_si"

import httpx
from aiogram.client.bot import Bot
from aiogram.client.session.base import BaseSession
from aiogram.types import Update, CallbackQuery, Message, Chat, User

import config
config.VK_SECTION_PHOTOS = ""       # тест не зависит от .env сервера
VK_SECRET, LEO_SECRET = "vk1.a.СЕКРЕТ_ВК", "leo-СЕКРЕТ"
config.VK_GROUP_ID, config.VK_GROUP_TOKEN = "123456", VK_SECRET
config.LEONARDO_API_KEY = LEO_SECRET
config.TG_CHANNEL_ID = ""
config.ADMIN_TG_IDS = [880098]
config.IMAGE_PROVIDER = "leonardo"
config.VK_IMAGE_MODE = "upload"      # загрузка фото в ВК проверяем в этом режиме, режим «ссылка» — в конце
config.POLLINATIONS_KEY = ""
config.SOCIAL_AUTOPOST, config.SOCIAL_IMAGES = False, False
import accounts
import images
import profile_ctx
import social
import bot as botmod

fails = []


def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{x}]"))
    if not c:
        fails.append(n)


RECIPE = ("🍽 Куриный суп с лапшой\n\n⏱ Время: 30 мин\n\n*Продукты:*\n- Куриное филе — 200 г\n- Лапша — 80 г\n- Морковь — 1 шт\n\n"
          "*Инструкция:*\n1. Свари.\n\nПРОДУКТЫ_СПИСОК: курица")


async def fake_search(data):
    return "q", []


async def fake_gen(data, q, r):
    return RECIPE


social.search_recipes, social.generate_recipe = fake_search, fake_gen


poll = {"calls": [], "mode": "ok", "limit": 0}


class Resp:
    def __init__(self, data=None, content=b"", status=200, ctype="application/json", text=""):
        self._d, self.content, self.status_code, self.text = data, content, status, text
        self.headers = {"content-type": ctype}

    def json(self):
        return self._d

    def raise_for_status(self):
        pass


# ---------- Leonardo ----------
leo = {"calls": [], "statuses": ["PENDING", "COMPLETE"], "fail": None}


class FakeLeo:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, headers=None, **k):
        if leo["fail"]:
            raise leo["fail"]
        leo["calls"].append(("POST", url, json, headers))
        return Resp({"sdGenerationJob": {"generationId": "g1"}})

    async def get(self, url, headers=None, **k):
        leo["calls"].append(("GET", url))
        if url.endswith("/g1"):
            st = leo["statuses"].pop(0) if len(leo["statuses"]) > 1 else leo["statuses"][0]
            gen = {"status": st}
            if st == "COMPLETE":
                gen["generated_images"] = [{"url": "https://cdn.test/dish.png"}]
            return Resp({"generations_by_pk": gen})
        return Resp(content=b"PNGDATA")


async def no_sleep(s):
    pass


async def fake_llm(messages, max_tokens=3000):
    return '"Golden chicken noodle soup with carrots and fresh dill in a white bowl."'


images._sleep = no_sleep
images.call_llm_with_fallback = fake_llm

# ---------- ВК ----------
vk = {"calls": [], "upload_error": False}


class FakeVK:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, data=None, files=None, **k):
        vk["calls"].append((url, dict(data or {}), files))
        if url.endswith("photos.getWallUploadServer"):
            if vk["upload_error"]:
                return Resp({"error": {"error_code": 15, "error_msg": "Access denied"}})
            return Resp({"response": {"upload_url": "https://upload.test/u"}})
        if url == "https://upload.test/u":
            return Resp({"server": 11, "photo": "[{\"x\":1}]", "hash": "hh"})
        if url.endswith("photos.saveWallPhoto"):
            return Resp({"response": [{"id": 5, "owner_id": -123456}]})
        if url.endswith("wall.post"):
            return Resp({"response": {"post_id": 91}})
        return Resp({"response": {"groups": [{"id": 123456}]}})




class Fake:
    """Один подменённый клиент на весь модуль httpx: Leonardo и ВК различаем по адресу."""
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, **kw):
        if "leonardo.ai" in url:
            return await FakeLeo.post(self, url, **kw)
        return await FakeVK.post(self, url, **kw)

    async def get(self, url, **kw):
        if "pollinations.ai" in url:
            poll["calls"].append((url, kw.get("headers") or {}))
            if poll["mode"] == "down":
                raise httpx.ConnectError("нет сети")
            if poll["mode"] == "html":
                return Resp(content=b"<html>", ctype="text/html")
            if poll["limit"] > 0:
                poll["limit"] -= 1
                return Resp(status=402, text='{"error":{"message":"Free limit reached: 1 image every 45 seconds per IP. Retry after 3s"}}')
            return Resp(content=b"FREEJPEG", ctype="image/jpeg")
        return await FakeLeo.get(self, url, **kw)


httpx.AsyncClient = Fake


def posts():
    return [c for c in vk["calls"] if c[0].endswith("wall.post")]


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
            return Message(message_id=9700 + self.n, date=datetime.now(), chat=Chat(id=A, type="private"),
                           text=getattr(method, "text", "") or "").as_(bot)
        return True

    async def stream_content(self, *a, **k):
        raise NotImplementedError


A = 880098
s = FakeSession()
b = Bot(token="42:TEST", session=s)
seq = [0]


def msg(uid, text):
    seq[0] += 1
    return Update(update_id=seq[0], message=Message(message_id=seq[0] + 10, date=datetime.now(), chat=Chat(id=uid, type="private"),
                                                    from_user=User(id=uid, is_bot=False, first_name="T"), text=text))


def cb(uid, data):
    seq[0] += 1
    m = Message(message_id=800, date=datetime.now(), chat=Chat(id=uid, type="private"), text="x")
    return Update(update_id=seq[0], callback_query=CallbackQuery(id=str(seq[0]), from_user=User(id=uid, is_bot=False, first_name="T"),
                                                                 chat_instance="x", message=m, data=data))


def sent(name):
    return [p for n, p in s.calls if n == name]


def btns(p):
    return [x["text"] for r in (p or {}).get("reply_markup", {}).get("inline_keyboard", []) for x in r]


async def main():
    # --- описание сцены и состав ---
    check("состав для описания берётся из рецепта", images._ingredients(RECIPE) == "Куриное филе, Лапша, Морковь", images._ingredients(RECIPE))
    profile_ctx.base.set("кинза")
    scene = await images.dish_scene("Куриный суп", RECIPE)
    check("описание сцены: одна английская фраза без кавычек, исключения человека не мешают", scene.startswith("Golden chicken") and not scene.startswith('"') and profile_ctx.current.get() is None, scene)

    async def broken_llm(*a, **k):
        raise RuntimeError("down")
    images.call_llm_with_fallback = broken_llm
    check("ИИ недоступен — описание по названию", await images.dish_scene("Куриный суп", RECIPE) == "Куриный суп, served on a plate")
    images.call_llm_with_fallback = fake_llm

    # --- Leonardo ---
    img = await images.make_dish_image("Куриный суп", RECIPE)
    post = next(c for c in leo["calls"] if c[0] == "POST")
    check("картинка получена после ожидания готовности", img == b"PNGDATA", str(img))
    check("запрос: размер, модель, одна картинка, описание и стиль еды",
          post[2]["width"] == post[2]["height"] == 768 and post[2]["num_images"] == 1 and post[2]["modelId"] == config.LEONARDO_MODEL_ID
          and "Golden chicken" in post[2]["prompt"] and "food photography" in post[2]["prompt"], str(post[2])[:200])
    check("ключ Leonardo идёт только в заголовке авторизации", post[3]["Authorization"] == f"Bearer {LEO_SECRET}" and LEO_SECRET not in post[2]["prompt"])
    n_poll = len(poll["calls"])
    check("при Leonardo бесплатный сервис не трогается, пока Leonardo справляется", n_poll == 0)
    leo["statuses"] = ["FAILED"]
    is_png = lambda b: bool(b) and b[:8] == b"\x89PNG\r\n\x1a\n"
    check("Leonardo не справился — берётся фирменная карточка", is_png(await images.make_dish_image("x", RECIPE)))
    leo["statuses"] = ["PENDING", "COMPLETE"]
    leo["fail"] = httpx.ConnectError("нет сети")
    check("Leonardo недоступен — тоже карточка", is_png(await images.make_dish_image("x", RECIPE)))
    poll["mode"] = "down"
    check("нет сети совсем — карточка всё равно рисуется", is_png(await images.make_dish_image("x", RECIPE)))
    poll["mode"], leo["fail"] = "ok", None
    config.LEONARDO_API_KEY = ""
    check("нет ключа Leonardo при IMAGE_PROVIDER=leonardo — карточка", is_png(await images.make_dish_image("x", RECIPE)))
    config.LEONARDO_API_KEY = LEO_SECRET

    # --- бесплатный по умолчанию ---
    config.IMAGE_PROVIDER = "free"
    leo_posts = len([c for c in leo["calls"] if c[0] == "POST"])
    poll["calls"].clear()
    check("free: картинка от бесплатного сервиса", await images.make_dish_image("Суп", RECIPE) == b"FREEJPEG")
    url, hdr = poll["calls"][-1]
    check("free: в адресе запрос, размер, без логотипа, модель, запреты; ключей нет",
          "image.pollinations.ai/prompt/" in url and "width=768" in url and "nologo=true" in url and "model=flux" in url
          and "negative=" in url and "Golden%20chicken" in url and LEO_SECRET not in url and not hdr, url[:160])
    check("free: платный Leonardo не вызывается, даже если ключ есть", len([c for c in leo["calls"] if c[0] == "POST"]) == leo_posts)
    poll["mode"] = "html"
    check("free: сервис вернул не картинку — берётся карточка", is_png(await images.make_dish_image("Суп", RECIPE)))
    poll["mode"], poll["limit"] = "ok", 1
    poll["calls"].clear()
    check("free: лимит «Retry after 3s» — подождали и получили со второго раза", await images.make_dish_image("Суп", RECIPE) == b"FREEJPEG" and len(poll["calls"]) == 2)
    poll["limit"] = 5
    check("free: лимит не снимается — карточка, без зависания", is_png(await images.make_dish_image("Суп", RECIPE)))
    config.IMAGE_PROVIDER = "card"
    n_before = len(poll["calls"]) + len([c for c in leo["calls"] if c[0] == "POST"])
    card = await images.make_dish_image("Куриный суп", RECIPE)
    from PIL import Image as _I
    import io as _io
    check("card: карточка 1200×630 без обращений к сети и платным сервисам",
          is_png(card) and _I.open(_io.BytesIO(card)).size == (1200, 630) and len(poll["calls"]) + len([c for c in leo["calls"] if c[0] == "POST"]) == n_before)
    config.IMAGE_PROVIDER = "free"
    poll["limit"] = 0
    config.POLLINATIONS_KEY = "poll-СЕКРЕТ"
    poll["calls"].clear()
    await images.make_dish_image("Суп", RECIPE)
    check("ключ pollinations (если задан) уходит только в заголовок", poll["calls"][-1][1].get("Authorization") == "Bearer poll-СЕКРЕТ" and "СЕКРЕТ" not in poll["calls"][-1][0])
    config.POLLINATIONS_KEY = ""
    config.IMAGE_PROVIDER = "off"
    check("off: картинки выключены", not images.configured() and await images.make_dish_image("Суп", RECIPE) is None)
    config.IMAGE_PROVIDER = "leonardo"

    # --- публикация с картинкой ---
    res = await social.publish(None, "Суп", "Текст поста", b"PNGDATA")
    names = [c[0].rsplit("/", 1)[-1] for c in vk["calls"]]
    check("картинка загружена в ВК тремя шагами, потом запись", names[-4:] == ["photos.getWallUploadServer", "u", "photos.saveWallPhoto", "wall.post"], str(names[-4:]))
    check("загрузка идёт от имени сообщества, файл передан", vk["calls"][-4][1]["group_id"] == 123456 and vk["calls"][-3][2]["photo"][1] == b"PNGDATA")
    check("к записи прикреплена картинка", posts()[-1][1]["attachments"] == "photo-123456_5" and res["vk"] == 91 and not res["errors"], str(res))

    n = len(posts())
    vk["upload_error"] = True
    res = await social.publish(None, "Суп 2", "Текст без картинки", b"PNGDATA")
    check("ВК не принял картинку: запись всё равно вышла, без вложения, с понятным советом",
          len(posts()) == n + 1 and "attachments" not in posts()[-1][1] and res["vk"] == 91 and "Фотографии" in res["errors"][0]
          and "без картинки" in res["errors"][0] and VK_SECRET not in res["errors"][0], str(res))
    vk["upload_error"] = False
    await social.publish(None, "Суп 3", "Без картинки вообще")
    check("без картинки загрузка не вызывается", "attachments" not in posts()[-1][1])

    # --- канал ---
    config.TG_CHANNEL_ID = "@kbzum_test"
    s2 = FakeSession()
    b2 = Bot(token="42:TEST", session=s2)
    await social.publish(b2, "Суп 4", "Текст для канала", b"PNGDATA")
    kinds = [(n, p.get("chat_id")) for n, p in s2.calls if n in ("SendPhoto", "SendMessage")]
    check("канал: сначала фото, потом текст", kinds == [("SendPhoto", "@kbzum_test"), ("SendMessage", "@kbzum_test")], str(kinds))
    config.TG_CHANNEL_ID = ""

    # --- расписание ---
    async def fake_build_image(title, text):
        return b"AUTOIMG"
    social.build_image = fake_build_image
    for flag, expect in ((False, False), (True, True)):
        config.SOCIAL_AUTOPOST, config.SOCIAL_IMAGES = True, flag
        accounts.sess_set(social.LAST_KEY, {})
        n = len(posts())
        await social.daily_job(None)
        check(f"автопубликация при SOCIAL_IMAGES={flag}: {'с картинкой' if expect else 'без картинки'}",
              len(posts()) == n + 1 and (("attachments" in posts()[-1][1]) == expect), str(posts()[-1][1].keys()))
    config.SOCIAL_AUTOPOST, config.SOCIAL_IMAGES = False, False

    # --- кнопки владельца ---
    await botmod.dp.feed_update(b, msg(A, "/postvk"))
    m = [p for p in sent("SendMessage") if "Черновик" in p.get("text", "")][-1]
    check("в черновике есть «С картинкой», когда ключ Leonardo задан", "🖼 С картинкой" in btns(m) and "✅ Опубликовать" in btns(m), str(btns(m)))
    n = len(posts())
    await botmod.dp.feed_update(b, cb(A, "soc:img"))
    ph = sent("SendPhoto")
    check("«С картинкой»: картинка показана, кнопки «с картинкой» и «другая»", ph and "✅ Опубликовать с картинкой" in btns(ph[-1]) and "🔄 Другая картинка" in btns(ph[-1]), str(ph)[:200])
    check("до подтверждения ничего не опубликовано", len(posts()) == n)
    await botmod.dp.feed_update(b, cb(555, "soc:goimg"))
    check("чужое нажатие ничего не публикует", len(posts()) == n)
    await botmod.dp.feed_update(b, cb(A, "soc:goimg"))
    check("«Опубликовать с картинкой»: запись с вложением", len(posts()) == n + 1 and "attachments" in posts()[-1][1])
    await botmod.dp.feed_update(b, cb(A, "soc:goimg"))
    check("повторное нажатие ничего не публикует", len(posts()) == n + 1)

    await botmod.dp.feed_update(b, msg(A, "/postvk"))
    await botmod.dp.feed_update(b, cb(A, "soc:img"))
    n = len(posts())
    await botmod.dp.feed_update(b, cb(A, "soc:go"))
    check("«Опубликовать» после картинки: только текст", len(posts()) == n + 1 and "attachments" not in posts()[-1][1])

    async def no_image(title, text):
        return None
    social.build_image = no_image
    await botmod.dp.feed_update(b, msg(A, "/postvk"))
    await botmod.dp.feed_update(b, cb(A, "soc:img"))
    check("картинка не получилась: понятное сообщение, публикация возможна",
          any("Картинка не получилась" in p.get("text", "") for p in sent("SendMessage")))
    config.LEONARDO_API_KEY = ""
    config.IMAGE_PROVIDER = "free"
    await botmod.dp.feed_update(b, msg(A, "/postvk"))
    m = [p for p in sent("SendMessage") if "Черновик" in p.get("text", "")][-1]
    check("при бесплатном сервисе кнопка «С картинкой» есть и без ключа", "🖼 С картинкой" in btns(m))
    config.IMAGE_PROVIDER = "off"
    await botmod.dp.feed_update(b, msg(A, "/postvk"))
    m = [p for p in sent("SendMessage") if "Черновик" in p.get("text", "")][-1]
    check("при IMAGE_PROVIDER=off кнопки «С картинкой» нет", "🖼 С картинкой" not in btns(m))

    # --- режим «ссылка»: картинка идёт страницей рецепта (ключ сообщества фото не загружает, код 27) ---
    config.VK_IMAGE_MODE = "link"
    config.WEB_APP_URL = "https://menu.test"
    config.IMAGE_PROVIDER = "card"
    vk["calls"].clear()
    res = await social.publish(None, "Куриный суп", "Текст поста", b"CARDPNG", "Тело рецепта")
    att = posts()[-1][1]["attachments"]
    slug = att.rsplit("/", 1)[-1]
    check("ссылка: к записи прикреплена страница рецепта на нашем сайте", att.startswith("https://menu.test/r/kurinyy-sup-") and res["vk"] == 91 and not res["errors"], att)
    check("ссылка: фото в ВК не загружается", not any("WallUpload" in c[0] or "saveWallPhoto" in c[0] for c in vk["calls"]))
    rec = accounts.pub_recipe_get(slug)
    check("ссылка: страница сохранена с текстом и картинкой", rec and rec["body"] == "Тело рецепта" and accounts.pub_recipe_image(slug) == b"CARDPNG", str(rec))
    n = len(posts())
    await social.publish(None, "Без картинки", "Текст")
    check("ссылка: без картинки карточка рисуется сама и приложена ссылка на страницу (иначе ВК не примет превью)", len(posts()) == n + 1 and posts()[-1][1].get("attachments", "").startswith("https://menu.test/r/"), str(posts()[-1][1].get("attachments")))

    # --- автоматическая картинка в черновике ---
    async def real_build_image(title, text):
        return await images.make_dish_image(title, text)
    social.build_image = real_build_image
    config.SOCIAL_IMAGES = True
    n_draft = len([p for p in sent("SendMessage") if "Черновик" in p.get("text", "")])
    await botmod.dp.feed_update(b, msg(A, "/postvk"))
    drafts = [p for p in sent("SendMessage") if "Черновик" in p.get("text", "")]
    photo = sent("SendPhoto")[-1]
    check("авто: черновик приходит без кнопок, сразу следом карточка с кнопками",
          len(drafts) == n_draft + 1 and not drafts[-1].get("reply_markup") and {"✅ Опубликовать с картинкой", "Без картинки", "🔄 Другая картинка"} <= set(btns(photo)), str(btns(photo)))
    n = len(posts())
    await botmod.dp.feed_update(b, cb(A, "soc:goimg"))
    check("авто: «Опубликовать с картинкой» — запись со ссылкой на страницу рецепта", len(posts()) == n + 1 and posts()[-1][1]["attachments"].startswith("https://menu.test/r/"), str(posts()[-1][1].get("attachments")))
    await botmod.dp.feed_update(b, msg(A, "/postvk"))
    n = len(posts())
    await botmod.dp.feed_update(b, cb(A, "soc:go"))
    check("авто: «Без картинки» — карточка и ссылка на страницу всё равно приложены", len(posts()) == n + 1 and posts()[-1][1].get("attachments", "").startswith("https://menu.test/r/"), str(posts()[-1][1]))
    social.build_image = no_image
    await botmod.dp.feed_update(b, msg(A, "/postvk"))
    last_msgs = [p for p in sent("SendMessage") if "Картинка не получилась" in p.get("text", "")]
    check("авто: картинка не вышла — понятное сообщение с кнопкой «Опубликовать»", last_msgs and "✅ Опубликовать" in btns(last_msgs[-1]), str(last_msgs[-1:]))
    config.SOCIAL_IMAGES = False

asyncio.run(main())
print("\nИТОГ:", "ВСЁ ОК" if not fails else "ПРОВАЛЕНО: " + ", ".join(fails))
