"""Рецепт дня: разделы и хештеги, карточка всегда есть, повтор при отказе ВК в превью ссылки."""
import asyncio
import datetime
import os
import sys

PROJ = "/tmp/ct"
os.chdir(PROJ)
sys.path.insert(0, PROJ)
for f in ("usage_s2.db", "accounts_s2.db", "accounts_s2.db-wal", "accounts_s2.db-shm", "user_data.json"):
    if os.path.exists(f):
        os.remove(f)
os.environ["USAGE_DB"] = PROJ + "/usage_s2.db"
os.environ["ACCOUNTS_DB"] = PROJ + "/accounts_s2.db"
os.environ["WEB_DATA_DIR"] = "web_data_s2"

import config
config.VK_GROUP_ID, config.VK_GROUP_TOKEN = 123456, "vk1.a.СЕКРЕТ"
config.TG_CHANNEL_ID = ""
config.VK_IMAGE_MODE = "link"
import accounts
import social

fails = []


def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{x}]"))
    if not c:
        fails.append(n)


RECIPE = ("🍽 Сырники\n\n⏱ Время: 20 мин | 👥 Порций: 2\n\nКалорийность: 300 ккал | Б: 20 г | Ж: 10 г | У: 30 г\n\n*Продукты:*\n- Творог\n\nПРОДУКТЫ_СПИСОК: творог")
hints = []


async def fake_search(data):
    return "q", []


async def fake_gen(data, q, r):
    hints.append(data["variety_hint"])
    return RECIPE


social.search_recipes, social.generate_recipe = fake_search, fake_gen
calls = []
QUEUE = []


class R:
    def __init__(self, d):
        self.d = d

    def json(self):
        return self.d


class FakeClient:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, data=None, **k):
        calls.append((url.rsplit("/", 1)[-1], dict(data or {})))
        if url.endswith("wall.post"):
            return R(QUEUE.pop(0) if QUEUE else {"response": {"post_id": 5}})
        return R({"response": 1})


social.httpx.AsyncClient = FakeClient
social.RETRY_PAUSE = 0
LINK_ERR = {"error": {"error_code": 100, "error_msg": "One of the parameters specified was missing or invalid: Violated: link_photo_sizing_rule. No photo given"}}


async def main():
    days = [datetime.date(2026, 11, 1) + datetime.timedelta(days=i) for i in range(22)]
    names = [social.section_for(0, d)[0] for d in days]
    check("разделы чередуются: за 11 дней встречаются все", set(names[:11]) == {x[0] for x in social.SECTIONS} and len(social.SECTIONS) == 11, str(names))
    check("«Другой рецепт» сдвигает раздел", social.section_for(1, days[0])[0] != social.section_for(0, days[0])[0])
    ok = True
    for _, _, tags in social.SECTIONS:
        ok = ok and "#ужин" not in tags if tags != social.SECTIONS[2][2] else ok
    check("хештеги раздела свои: у завтрака нет «#ужин»", "#ужин" not in social.SECTIONS[0][2] and "#завтрак" in social.SECTIONS[0][2] and ok)
    d = await social.build_post_full()
    sec = d["section"]
    check("пост: раздел в заголовке, общие и свои хештеги", f"Рецепт дня ({sec})" in d["text"] and "#рецептдня" in d["text"]
          and social.section_for()[2].split()[0] in d["text"], d["text"][-200:])
    check("в запросе к ИИ подсказка раздела", hints[-1] == social.section_for()[1])

    # без картинки карточка рисуется сама и страница получает og:image
    res = await social.publish(None, d["title"], d["text"], None, d["body"])
    wall = [c for c in calls if c[0] == "wall.post"][-1][1]
    check("публикация без картинки: прикреплена ссылка на страницу рецепта", res["vk"] == 5 and wall.get("attachments", "").startswith("https://menu.ymayaka.ru/r/") and not res["errors"], str(res))
    slug = wall["attachments"].rsplit("/", 1)[-1]
    check("у страницы есть картинка-карточка", len(accounts.pub_recipe_image(slug) or b"") > 5000)

    # постоянная картинка раздела из альбома
    calls.clear()
    config.VK_SECTION_PHOTOS = "zavtrak=111,desert=222"
    res = await social.publish(None, d["title"], d["text"], None, d["body"], "десерт")
    wall = [c for c in calls if c[0] == "wall.post"][-1][1]
    check("картинка раздела из альбома прикреплена, загрузки нет", wall.get("attachments", "").endswith("_222") and wall["attachments"].startswith("photo-") and not any("photos." in c[0] for c in calls), str(wall)[:200])
    calls.clear()
    res = await social.publish(None, d["title"], d["text"], None, d["body"], "ужин")
    wall = [c for c in calls if c[0] == "wall.post"][-1][1]
    check("нет номера для раздела: прежний путь, запись выходит", res["vk"] == 5 and not wall.get("attachments", "").endswith("_222"), str(res))
    config.VK_SECTION_PHOTOS = ""
    check("все разделы имеют картинку и тип питания известен", all(s[0] in social.SECTION_PHOTO for s in social.SECTIONS))
    import keyboards as _K
    check("типы питания разделов существуют", all(v in _K.CUISINE_BUTTONS for v in social.SECTION_CUISINE.values()))
    _g = config.VK_GROUP_ID
    config.VK_GROUP_ID = "kbzum"
    check("хештег для поиска внутри сообщества", "#завтрак@kbzum" in social.hashtags(social.SECTIONS[0][2]))
    config.VK_GROUP_ID = _g

    # отказ ВК в превью: запись всё равно выходит, без вложения и со ссылкой в тексте
    calls.clear()
    QUEUE[:] = [LINK_ERR]
    res = await social.publish(None, d["title"], d["text"], None, d["body"])
    names2 = [c[0] for c in calls]
    posts2 = [c[1] for c in calls if c[0] == "wall.post"]
    check("отказ ВК (код 100, link_photo): запись выходит без вложения, ссылка в тексте",
          res["vk"] == 5 and "pages.clearCache" not in names2 and len(posts2) == 2 and "attachments" not in posts2[1] and "/r/" in posts2[1]["message"] and not res["errors"], str(names2) + str(res))
    check("токен не попал в запросы кроме служебных полей и не в ошибки", "СЕКРЕТ" not in str(res))
    calls.clear()
    QUEUE[:] = [LINK_ERR, LINK_ERR]
    res = await social.publish(None, d["title"], d["text"], None, d["body"])
    check("если и второй раз отказ — ошибка понятная, без ключа, одна повторная попытка", res["vk"] is None and any("link_photo" in e for e in res["errors"])
          and [c[0] for c in calls].count("wall.post") == 2 and "СЕКРЕТ" not in str(res), str(res))
    calls.clear()
    QUEUE[:] = [{"error": {"error_code": 214, "error_msg": "Access to adding post denied"}}]
    res = await social.publish(None, d["title"], d["text"], None, d["body"])
    check("другие ошибки ВК не повторяются", [c[0] for c in calls].count("wall.post") == 1 and res["vk"] is None)

asyncio.run(main())
print("\nИТОГ:", "ВСЁ ОК" if not fails else "ПРОВАЛЕНО: " + ", ".join(fails))
