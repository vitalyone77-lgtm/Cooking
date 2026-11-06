"""
Фирменная карточка рецепта для постов (Pillow): название, время, порции, КБЖУ на порцию, тарелка с паром.
Без нейросети: бесплатно, всегда получается, без чужих логотипов, а цифры берутся из самого рецепта.
"""
import math
import os
import re
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 800
BG, GREEN, SOFT, PEACH = "#FBF7F2", "#2F6B4F", "#E6F0EA", "#FBE3C4"
INK, MUTED, GREEN_DARK = "#2B2622", "#6B625A", "#24593F"

_FONTS = [
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    (r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\segoeuib.ttf"),
    (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf"),
    (os.getenv("CARD_FONT", ""), os.getenv("CARD_FONT_BOLD", "")),
]


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    for regular, heavy in _FONTS:
        path = heavy if bold else regular
        if path and os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def parse(title: str, recipe_text: str) -> dict:
    """Название и числа из рецепта (формат: «⏱ Время: 30 мин | 👥 Порций: 2», «Калорийность: 320 ккал | Б: 24 г | ...»)."""
    t = recipe_text or ""
    num = lambda pat: (re.search(pat, t) or [None, None])[1]
    clean = re.sub(r"^[^\wА-Яа-яЁё]+", "", title or "").strip() or "Рецепт дня"
    time_ = (re.search(r"Время:\s*([^|\n]+)", t) or [None, ""])[1].strip()
    return {"title": clean, "time": time_, "servings": num(r"Порций:\s*(\d+)"), "kcal": num(r"(\d[\d.,]*)\s*ккал"),
            "protein": num(r"Б:\s*([\d.,]+)"), "fat": num(r"Ж:\s*([\d.,]+)"), "carb": num(r"У:\s*([\d.,]+)")}


def _wrap(draw, text: str, font, max_width: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=font) <= max_width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    return lines + ([cur] if cur else [])


def _pill(draw, x: int, y: int, text: str, font, fill=SOFT, color=GREEN_DARK, pad_x: int = 24, pad_y: int = 10) -> int:
    w = int(draw.textlength(text, font=font))
    h = font.size + pad_y * 2
    draw.rounded_rectangle((x, y, x + w + pad_x * 2, y + h), radius=h // 2, fill=fill)
    draw.text((x + pad_x, y + pad_y - 2), text, font=font, fill=color)
    return x + w + pad_x * 2 + 14


def _plate(draw, cx: int, cy: int, k: float = 1.0) -> None:
    r1, r2, r3 = int(190 * k), int(140 * k), int(92 * k)
    draw.ellipse((cx - r1, cy - r1, cx + r1, cy + r1), fill=SOFT)
    draw.ellipse((cx - r2, cy - r2, cx + r2, cy + r2), fill=BG, outline=GREEN, width=10)
    draw.ellipse((cx - r3, cy - r3, cx + r3, cy + r3), fill=PEACH)
    for dx in (-50, 0, 50):                                    # пар
        pts = [(cx + int(dx * k) + 14 * math.sin(i / 20 * math.pi * 2.4), cy - r1 - 15 - i * 3.4 * k) for i in range(21)]
        draw.line(pts, fill=GREEN, width=12, joint="curve")
        for px, py in (pts[0], pts[-1]):
            draw.ellipse((px - 6, py - 6, px + 6, py + 6), fill=GREEN)


def make_card(title: str, recipe_text: str, size: tuple[int, int] = (1200, 630)) -> bytes:
    """PNG с карточкой рецепта. По умолчанию 1200×630: такой размер ВКонтакте и мессенджеры показывают под ссылкой без обрезки."""
    w, h = size
    d = parse(title, recipe_text)
    img = Image.new("RGB", (w, h), BG)
    draw = ImageDraw.Draw(img)
    _plate(draw, w - 250, h // 2 + 25, 0.9 if h < 700 else 1.0)

    _pill(draw, 80, 40, "Рецепт дня", _font(26, True), pad_y=8)

    fsize, lines = 68, []
    for fsize in (68, 60, 54, 48):                             # подбираем размер, чтобы название влезло в 3 строки
        lines = _wrap(draw, d["title"], _font(fsize, True), 640)
        if len(lines) <= 3:
            break
    if len(lines) > 3:                                          # совсем длинное название: обрезаем с многоточием
        last = lines[2]
        while last and draw.textlength(last + "…", font=_font(fsize, True)) > 640:
            last = last[:-1].rstrip()
        lines = lines[:2] + [last + "…"]
    f, y = _font(fsize, True), 112
    for ln in lines:
        draw.text((80, y), ln, font=f, fill=INK)
        y += int(fsize * 1.18)

    x, small = 80, _font(28)
    chips_end = y + 16
    if d["time"]:
        x = _pill(draw, x, y + 16, d["time"], small, pad_y=8)
    if d["servings"]:
        word = "порция" if d["servings"] == "1" else "порции" if d["servings"] in ("2", "3", "4") else "порций"
        x = _pill(draw, x, y + 16, f"{d['servings']} {word}", small, pad_y=8)
    if d["time"] or d["servings"]:
        chips_end += small.size + 16 + 8

    if d["kcal"]:
        ky = max(chips_end + 18, h - 205)
        big = _font(56, True)
        draw.text((80, ky), f"{d['kcal']} ккал", font=big, fill=GREEN)
        draw.text((80 + int(draw.textlength(f"{d['kcal']} ккал", font=big)) + 16, ky + 22), "на порцию", font=_font(28), fill=MUTED)
        parts = [f"{n} {d[k]} г" for n, k in (("Белки", "protein"), ("Жиры", "fat"), ("Углеводы", "carb")) if d[k]]
        if parts:
            draw.text((80, ky + 76), "   ".join(parts), font=_font(30), fill=INK)

    draw.ellipse((80, h - 52, 96, h - 36), fill=GREEN)
    draw.text((110, h - 62), "Кухонный помощник", font=_font(26), fill=MUTED)
    out = BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def feature_card(title: str, size: tuple[int, int] = (1200, 630)) -> bytes:
    """PNG с карточкой поста о функции для Telegram: название функции и фирменная тарелка."""
    w, h = size
    img = Image.new("RGB", (w, h), BG)
    draw = ImageDraw.Draw(img)
    _plate(draw, w - 250, h // 2 + 25, 0.9)
    _pill(draw, 80, 60, "Возможности помощника", _font(26, True), pad_y=8)
    fsize, lines = 68, []
    for fsize in (68, 60, 54, 48):
        lines = _wrap(draw, title, _font(fsize, True), 640)
        if len(lines) <= 4:
            break
    y = 150
    for ln in lines[:4]:
        draw.text((80, y), ln, font=_font(fsize, True), fill=INK)
        y += int(fsize * 1.18)
    draw.ellipse((80, h - 52, 96, h - 36), fill=GREEN)
    draw.text((110, h - 62), "Кухонный помощник", font=_font(26), fill=MUTED)
    out = BytesIO()
    img.save(out, "PNG")
    return out.getvalue()
