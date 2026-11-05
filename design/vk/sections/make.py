"""
Постоянные картинки направлений для постов ВК (ключ сообщества не может загружать новые фото, поэтому картинки лежат в альбоме).
Запуск: python make.py <папка с PNG 512 из noto-emoji>. Значки: Noto Emoji (Google), свободная лицензия.
"""
import os
import sys

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
W, H = 1200, 800
BG, INK, MUTED, GREEN = "#FBF7F2", "#2B2622", "#6B625A", "#2F6B4F"
BOLD, REG = r"C:\Windows\Fonts\segoeuib.ttf", r"C:\Windows\Fonts\segoeui.ttf"

# файл, заголовок, подпись, цвет круга, значки
SETS = [
    ("01_zavtrak", "Завтрак", "Сытное начало дня", "#FBE3C4", ["1f373", "2615", "1f95e"]),
    ("02_obed", "Обед", "Сытно и по-домашнему", "#E6F0EA", ["1f372", "1f35a", "1f957"]),
    ("03_uzhin", "Ужин", "Лёгкий и быстрый вечер", "#DCE6F5", ["1f37d", "1f957", "1f41f"]),
    ("04_perekus", "Перекус", "Когда до еды ещё далеко", "#F6E7B8", ["1f96a", "1f34e", "1f95c"]),
    ("05_desert", "Десерт", "Сладкое с подсчётом калорий", "#F8DCE4", ["1f370", "1f36a", "1f353"]),
    ("06_supy", "Супы", "Горячее на каждый день", "#FBE3C4", ["1f35c", "1f955", "1f954"]),
    ("07_kbzhu", "По КБЖУ", "Блюдо под вашу цель", "#E6F0EA", ["1f3af", "2696", "1f951"]),
    ("08_zabeg", "Подготовка к забегу", "Энергия для тренировок", "#DCE6F5", ["1f45f", "1f3c3", "1f34c"]),
    ("09_massa", "Набор массы", "Больше белка и калорий", "#F8DCE4", ["1f4aa", "1f357", "1f95a"]),
    ("10_balans", "Сбалансированно", "Просто и без крайностей", "#E6F0EA", ["1f957", "1f966", "1f34a"]),
    ("11_ayurveda", "Аюрведа", "Специи и тёплая еда", "#F6E7B8", ["1f35b", "1f33f", "1fad6"]),
    ("12_funkcii", "Возможности помощника", "Рецепты, покупки, дневник", "#DCE6F5", ["1f916", "1f6d2", "1f4d2"]),
]


def icon(folder, code, size):
    return Image.open(os.path.join(folder, f"emoji_u{code}.png")).convert("RGBA").resize((size, size), Image.LANCZOS)


def draw_card(folder, name, title, sub, color, icons):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    cx, cy, r = 850, 400, 300
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color)
    for code, size, x, y in zip(icons, (320, 170, 170), (cx - 160, cx + 40, cx - 280), (cy - 190, cy + 70, cy + 80)):
        ic = icon(folder, code, size)
        img.paste(ic, (x, y), ic)
    fs = 92
    words, lines = title.split(), []
    font = ImageFont.truetype(BOLD, fs)
    cur = ""
    for w in words:
        t = f"{cur} {w}".strip()
        if d.textlength(t, font=font) <= 460 or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    while max(d.textlength(x, font=font) for x in lines) > 465:
        fs -= 4
        font = ImageFont.truetype(BOLD, fs)
    y = 400 - (len(lines) * int(fs * 1.15) + 70) // 2
    for ln in lines:
        d.text((70, y), ln, font=font, fill=INK)
        y += int(fs * 1.15)
    sf = ImageFont.truetype(REG, 36)
    cur, sl = "", []
    for w in sub.split():
        t = f"{cur} {w}".strip()
        if d.textlength(t, font=sf) <= 480 or not cur:
            cur = t
        else:
            sl.append(cur)
            cur = w
    sl.append(cur)
    y += 12
    for ln in sl:
        d.text((70, y), ln, font=sf, fill=MUTED)
        y += 46
    d.ellipse((70, H - 72, 90, H - 52), fill=GREEN)
    d.text((104, H - 82), "Кухонный помощник", font=ImageFont.truetype(REG, 30), fill=MUTED)
    img.save(os.path.join(HERE, name + ".png"), "PNG", optimize=True)


if __name__ == "__main__":
    for row in SETS:
        draw_card(sys.argv[1], *row)
    print("готово", len(SETS))
