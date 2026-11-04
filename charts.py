"""
График дневника питания картинкой (Pillow): столбики калорий по дням, линия примерного расхода, линия веса (правая шкала).
Нужен, чтобы в Telegram показать «сколько калорий в день и как реагирует вес». Все цифры оценочные.
"""
import datetime
from io import BytesIO

from PIL import Image, ImageDraw

import cards

W, H = 1200, 675
BG, GREEN, GREEN_DARK, SOFT, PEACH, ORANGE = cards.BG, cards.GREEN, cards.GREEN_DARK, cards.SOFT, cards.PEACH, "#C9772E"
INK, MUTED, LINE = cards.INK, cards.MUTED, "#E7DED3"
WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def _dashed(draw, x1, x2, y, color, width=3, dash=14, gap=9):
    x = x1
    while x < x2:
        draw.line((x, y, min(x + dash, x2), y), fill=color, width=width)
        x += dash + gap


def _fmt(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def diary_chart(series: list[dict], tdee: int | None = None, weights: list[dict] | None = None) -> bytes:
    """series: [{"day": "2026-11-04", "kcal": 1800, "n": 3}, ...] по порядку; weights: [{"day", "kg"}]. -> PNG."""
    n = len(series)
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    logged = [d for d in series if d["n"]]
    avg = int(round(sum(d["kcal"] for d in logged) / len(logged))) if logged else 0
    wts = [w for w in (weights or []) if series and series[0]["day"] <= w["day"] <= series[-1]["day"]]

    weekly = n > 31                                             # за 2–3 месяца столбик = среднее за неделю
    bars = []
    for k in range(0, n, 7 if weekly else 1):
        chunk = series[k:k + (7 if weekly else 1)]
        got = [d for d in chunk if d["n"]]
        bars.append({"day": chunk[0]["day"], "kcal": int(round(sum(d["kcal"] for d in got) / len(got))) if got else 0, "n": len(got)})
    nb = len(bars)
    step = 7 if weekly else 1
    draw.text((60, 30), f"Калории и вес за {n} дн." + (" (недельные средние)" if weekly else ""), font=cards._font(40, True), fill=INK)
    sub = f"В среднем {_fmt(avg)} ккал в день" if logged else "Записей пока нет"
    if tdee:
        sub += f"  ·  расход примерно {_fmt(tdee)} ккал"
    draw.text((60, 84), sub, font=cards._font(26), fill=MUTED)

    left, right = 120, (1050 if len(wts) >= 2 else 1140)
    top, bottom = 150, 540
    ymax = max([d["kcal"] for d in bars] + [tdee or 0, 1000]) * 1.12
    ymax = int(-(-ymax // 500) * 500)
    Y = lambda v: bottom - (bottom - top) * v / ymax

    f_small = cards._font(22)
    for i in range(5):                                          # сетка и шкала калорий
        v = ymax * i / 4
        draw.line((left, Y(v), right, Y(v)), fill=LINE, width=2)
        draw.text((left - 12 - draw.textlength(_fmt(int(v)), font=f_small), Y(v) - 12), _fmt(int(v)), font=f_small, fill=MUTED)

    slot = (right - left) / max(nb, 1)
    bw = slot * 0.62
    f_day = cards._font(20 if nb > 14 else 22)
    every = 1 if nb <= 14 else 2 if nb <= 20 else 3
    for i, d in enumerate(bars):
        cx = left + slot * (i + 0.5)
        if d["n"]:
            color = GREEN_DARK if i == nb - 1 else GREEN
            draw.rounded_rectangle((cx - bw / 2, Y(d["kcal"]), cx + bw / 2, bottom), radius=min(8, int(bw / 3)), fill=color)
        else:
            draw.line((cx - bw / 4, bottom - 2, cx + bw / 4, bottom - 2), fill=LINE, width=4)
        if i % every == 0 or i == nb - 1:
            dt = datetime.date.fromisoformat(d["day"])
            label = f"{dt.day:02d}.{dt.month:02d}" if weekly else f"{dt.day}"
            draw.text((cx - draw.textlength(label, font=f_day) / 2, bottom + 10), label, font=f_day, fill=INK)
            if not weekly:
                wd = WEEKDAYS[dt.weekday()]
                draw.text((cx - draw.textlength(wd, font=f_small) / 2, bottom + 36), wd, font=f_small, fill=MUTED)

    if tdee:                                                    # линия примерного расхода
        _dashed(draw, left, right, Y(tdee), ORANGE)
        t = f"расход ≈ {_fmt(tdee)}"
        draw.text((right - draw.textlength(t, font=f_small), Y(tdee) - 30), t, font=f_small, fill=ORANGE)

    if len(wts) >= 2:                                           # вес по правой шкале
        lo, hi = min(w["kg"] for w in wts), max(w["kg"] for w in wts)
        if hi - lo < 1:
            lo, hi = lo - 0.5, hi + 0.5
        pad = (hi - lo) * 0.15
        lo, hi = lo - pad, hi + pad
        YW = lambda v: bottom - (bottom - top) * (v - lo) / (hi - lo)
        first = datetime.date.fromisoformat(series[0]["day"])
        pts = []
        for w in wts:
            idx = (datetime.date.fromisoformat(w["day"]) - first).days
            pts.append((left + slot * (idx + 0.5) / step, YW(w["kg"]), w["kg"], idx))
        if len(pts) >= 3 and pts[-1][3] - pts[0][3] >= 14:      # линия тренда (по методу наименьших квадратов)
            mx, my = sum(p[3] for p in pts) / len(pts), sum(p[2] for p in pts) / len(pts)
            den = sum((p[3] - mx) ** 2 for p in pts)
            k = sum((p[3] - mx) * (p[2] - my) for p in pts) / den if den else 0
            x0, x1 = pts[0][0], pts[-1][0]
            y0, y1 = YW(my + k * (pts[0][3] - mx)), YW(my + k * (pts[-1][3] - mx))
            steps = max(int((x1 - x0) // 24), 1)
            for s in range(steps):
                if s % 2 == 0:
                    draw.line((x0 + (x1 - x0) * s / steps, y0 + (y1 - y0) * s / steps, x0 + (x1 - x0) * (s + 1) / steps,
                               y0 + (y1 - y0) * (s + 1) / steps), fill=MUTED, width=3)
        draw.line([(x, y) for x, y, _, _ in pts], fill=INK, width=4, joint="curve")
        for x, y, _, _ in pts:
            draw.ellipse((x - 7, y - 7, x + 7, y + 7), fill=BG, outline=INK, width=4)
        for v in (lo + pad, (lo + hi) / 2, hi - pad):
            draw.text((right + 14, YW(v) - 12), f"{v:.1f}", font=f_small, fill=MUTED)
        draw.text((right + 14, top - 36), "вес, кг", font=f_small, fill=MUTED)

    ly = H - 70                                                 # легенда и оговорка
    draw.rounded_rectangle((left, ly + 4, left + 22, ly + 22), radius=4, fill=GREEN)
    draw.text((left + 32, ly), "калории за день", font=f_small, fill=INK)
    x = left + 32 + int(draw.textlength("калории за день", font=f_small)) + 36
    if tdee:
        _dashed(draw, x, x + 40, ly + 13, ORANGE, dash=10, gap=6)
        draw.text((x + 50, ly), "примерный расход", font=f_small, fill=INK)
        x += 50 + int(draw.textlength("примерный расход", font=f_small)) + 36
    if len(wts) >= 2:
        draw.ellipse((x, ly + 3, x + 20, ly + 23), fill=BG, outline=INK, width=4)
        draw.text((x + 30, ly), "вес (правая шкала)", font=f_small, fill=INK)
    draw.text((left, H - 36), "Оценка приблизительная и не заменяет совет врача или диетолога.", font=cards._font(20), fill=MUTED)
    out = BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def weight_chart(weights: list[dict], goal_kg: float | None = None) -> bytes:
    """График только веса (если дневник калорий не ведётся): линия по датам и пунктир цели. -> PNG."""
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    ws = sorted(weights, key=lambda w: w["day"])
    draw.text((60, 30), "Вес", font=cards._font(40, True), fill=INK)
    if ws:
        delta = ws[-1]["kg"] - ws[0]["kg"]
        sub = f"Сейчас {ws[-1]['kg']:g} кг, за период {delta:+.1f} кг" + (f"  ·  цель {goal_kg:g} кг" if goal_kg else "")
    else:
        sub = "Записей пока нет"
    draw.text((60, 84), sub, font=cards._font(26), fill=MUTED)
    left, right, top, bottom = 120, 1140, 150, 540
    vals = [w["kg"] for w in ws] + ([goal_kg] if goal_kg else [])
    lo, hi = (min(vals), max(vals)) if vals else (60.0, 80.0)
    if hi - lo < 1:
        lo, hi = lo - 0.5, hi + 0.5
    pad = (hi - lo) * 0.12
    lo, hi = lo - pad, hi + pad
    Y = lambda v: bottom - (bottom - top) * (v - lo) / (hi - lo)
    f_small = cards._font(22)
    for i in range(5):
        v = lo + (hi - lo) * i / 4
        draw.line((left, Y(v), right, Y(v)), fill=LINE, width=2)
        draw.text((left - 12 - draw.textlength(f"{v:.1f}", font=f_small), Y(v) - 12), f"{v:.1f}", font=f_small, fill=MUTED)
    if goal_kg:
        _dashed(draw, left, right, Y(goal_kg), ORANGE)
        draw.text((right - draw.textlength(f"цель {goal_kg:g}", font=f_small), Y(goal_kg) - 30), f"цель {goal_kg:g}", font=f_small, fill=ORANGE)
    if ws:
        d0 = datetime.date.fromisoformat(ws[0]["day"])
        span = max((datetime.date.fromisoformat(ws[-1]["day"]) - d0).days, 1)
        pts = [(left + 30 + (right - left - 60) * ((datetime.date.fromisoformat(w["day"]) - d0).days / span), Y(w["kg"])) for w in ws]
        if len(pts) > 1:
            draw.line(pts, fill=INK, width=4, joint="curve")
        for x, y in pts:
            draw.ellipse((x - 7, y - 7, x + 7, y + 7), fill=BG, outline=INK, width=4)
        for i in sorted({0, len(ws) - 1}):
            dt = datetime.date.fromisoformat(ws[i]["day"])
            label = f"{dt.day:02d}.{dt.month:02d}"
            draw.text((pts[i][0] - draw.textlength(label, font=f_small) / 2, bottom + 12), label, font=f_small, fill=INK)
    draw.text((left, H - 36), "Вес нужен для оценки динамики, это не медицинское заключение. Не заменяет совет врача или диетолога.", font=cards._font(20), fill=MUTED)
    out = BytesIO()
    img.save(out, "PNG")
    return out.getvalue()
