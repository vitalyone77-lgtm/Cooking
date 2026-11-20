"""Картинка Telegram-канала генерируется отдельно (free), при сбое — обычная; шаблоны ВК меняются по дням."""
import asyncio
import os
import sys

PROJ = "/tmp/ct"
os.chdir(PROJ)
sys.path.insert(0, PROJ)
os.environ["USAGE_DB"] = PROJ + "/usage_tgi.db"
os.environ["ACCOUNTS_DB"] = PROJ + "/accounts_tgi.db"
import config
import images
import social

fails = []


def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{x}]"))
    if not c:
        fails.append(n)


config.TG_CHANNEL_ID = "@x"
config.VK_GROUP_ID = ""
config.VK_GROUP_TOKEN = ""
sent = []


class Bot:
    async def send_photo(self, chat, photo, **k):
        sent.append(photo.data)

    async def send_message(self, chat, text, **k):
        class M: message_id = 5
        return M()


async def fake_make(title, text, provider=None):
    return b"GEN" if provider == "free" else None

images.make_dish_image = fake_make
config.TG_IMAGE_PROVIDER = "free"
asyncio.run(social.publish(Bot(), "Т", "текст", b"CARD", "тело"))
check("в канал уходит сгенерированная картинка", sent[-1] == b"GEN", str(sent))


async def fail_make(title, text, provider=None):
    return None

images.make_dish_image = fail_make
asyncio.run(social.publish(Bot(), "Т", "текст", b"CARD", "тело"))
check("не вышло — обычная карточка", sent[-1] == b"CARD", str(sent))
config.TG_IMAGE_PROVIDER = "card"
images.make_dish_image = fake_make
asyncio.run(social.publish(Bot(), "Т", "текст", b"CARD", "тело"))
check("card — генерация не вызывается", sent[-1] == b"CARD")

config.VK_SECTION_PHOTOS = "obed=11|22|33"
got = social.section_photo("обед", 5)
check("несколько шаблонов раздела: берётся один из них", got in ("photo-5_11", "photo-5_22", "photo-5_33"), str(got))
config.VK_SECTION_PHOTOS = "obed=11"
check("один шаблон работает как раньше", social.section_photo("обед", 5) == "photo-5_11")
print("ИТОГ:", "ВСЁ ОК" if not fails else f"упало {len(fails)}")
sys.exit(1 if fails else 0)
