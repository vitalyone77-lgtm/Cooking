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
MACRO_COLORS = {"protein": "#2a78d6", "fat": "#eb6834", "carb": "#1baf7a"}     # проверено на различимость при цветовой слепоте
NO_MACRO = "#C9C2B8"
REFERENCE = "Ориентир для взрослых (AMDR, США): белки 10–35%, жиры 20–35%, углеводы 45–65% калорий."


def _dashed(draw, x1, x2, y, color, width=3, dash=14, gap=9):
    x = x1
    while x < x2:
        draw.line((x, y, min(x + dash, x2), y), fill=color, width=width)
        x += dash + gap


def _fmt(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def macro_shares(series: list[dict]) -> dict | None:
    """Доли белков, жиров и углеводов в калориях за период, %. None — записей с БЖУ нет."""
    p, f, c = (sum(d.get(k, 0) or 0 for d in series) * m for k, m in (("protein", 4), ("fat", 9), ("carb", 4)))
    total = p + f + c
    if total <= 0:
        return None
    return {"protein": int(round(p * 100 / total)), "fat": int(round(f * 100 / total)), "carb": int(round(c * 100 / total))}


def _segments(d: dict) -> list[tuple[str, float]]:
    """Столбик по частям: [(цвет, ккал)] снизу вверх. Записи без БЖУ («пицца 450») остаются нейтральным хвостом."""
    kcal = d["kcal"]
    parts = [(MACRO_COLORS[k], (d.get(k, 0) or 0) * m) for k, m in (("protein", 4), ("fat", 9), ("carb", 4))]
    known = sum(v for _, v in parts)
    if known <= 0:
        return [(NO_MACRO, kcal)]
    scale = kcal / known if known >= kcal * 0.9 else 1
    out = [(col, v * scale) for col, v in parts if v > 0]
    rest = kcal - known * scale
    if rest > 1:
        out.append((NO_MACRO, rest))
    return out


def diary_chart(series: list[dict], tdee: int | None = None, weights: list[dict] | None = None) -> bytes:
    """series: [{"day": "2026-11-04", "kcal": 1800, "protein", "fat", "carb", "n": 3}, ...] по порядку; weights: [{"day", "kg"}]. -> PNG.
    Сверху калории: столбик раскрашен по долям белков, жиров и углеводов. Снизу отдельной панелью вес, с той же шкалой дат."""
    n = len(series)
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    logged = [d for d in series if d["n"]]
    avg = int(round(sum(d["kcal"] for d in logged) / len(logged))) if logged else 0
    wts = [w for w in (weights or []) if series and series[0]["day"] <= w["day"] <= series[-1]["day"]]
    has_w = len(wts) >= 2

    weekly = n > 31                                             # за 2–3 месяца столбик = среднее за неделю
    bars = []
    for k in range(0, n, 7 if weekly else 1):
        chunk = series[k:k + (7 if weekly else 1)]
        got = [d for d in chunk if d["n"]]
        mean = lambda key: sum(d.get(key, 0) or 0 for d in got) / len(got) if got else 0
        bars.append({"day": chunk[0]["day"], "kcal": int(round(mean("kcal"))), "protein": mean("protein"), "fat": mean("fat"), "carb": mean("carb"), "n": len(got)})
    nb = len(bars)
    step = 7 if weekly else 1
    draw.text((60, 26), f"Калории и вес за {n} дн." + (" (недельные средние)" if weekly else ""), font=cards._font(38, True), fill=INK)
    sub = f"В среднем {_fmt(avg)} ккал в день" if logged else "Записей пока нет"
    shares = macro_shares(logged)
    if shares:
        sub += f"  ·  Б {shares['protein']}%  Ж {shares['fat']}%  У {shares['carb']}%"
    draw.text((60, 76), sub, font=cards._font(26), fill=MUTED)

    left, right = 120, 1140
    top, bottom = 130, (390 if has_w else 520)
    ymax = max([d["kcal"] for d in bars] + [tdee or 0, 1000]) * 1.12
    ymax = int(-(-ymax // 500) * 500)
    Y = lambda v: bottom - (bottom - top) * v / ymax

    f_small = cards._font(22)
    tick = 1000 if ymax > 2000 else 500
    for v in range(0, ymax + 1, tick):                          # сетка и шкала калорий: круглые числа
        draw.line((left, Y(v), right, Y(v)), fill=LINE, width=2)
        draw.text((left - 12 - draw.textlength(_fmt(v), font=f_small), Y(v) - 12), _fmt(v), font=f_small, fill=MUTED)

    slot = (right - left) / max(nb, 1)
    bw = slot * 0.62
    any_none = False
    for i, d in enumerate(bars):
        cx = left + slot * (i + 0.5)
        if d["n"]:
            y = float(bottom)
            segs = _segments(d)
            any_none = any_none or any(col == NO_MACRO for col, _ in segs)
            for j, (col, v) in enumerate(segs):
                h = (bottom - top) * v / ymax
                y2 = y - h
                last = j == len(segs) - 1
                if last:
                    draw.rounded_rectangle((cx - bw / 2, y2, cx + bw / 2, y), radius=min(4, int(bw / 3)), fill=col, corners=(True, True, False, False))
                else:
                    draw.rectangle((cx - bw / 2, y2 + (2 if h > 5 else 0), cx + bw / 2, y), fill=col)   # зазор между частями столбика
                y = y2
        else:
            draw.line((cx - bw / 4, bottom - 2, cx + bw / 4, bottom - 2), fill=LINE, width=4)

    if tdee:                                                    # линия примерного расхода
        _dashed(draw, left, right, Y(tdee), INK, width=2)
        t = f"расход ≈ {_fmt(tdee)}"
        tw = draw.textlength(t, font=f_small)
        draw.rounded_rectangle((right - tw - 12, Y(tdee) - 32, right, Y(tdee) - 4), radius=6, fill=BG)   # подложка: подпись не сливается со столбиками
        draw.text((right - tw - 6, Y(tdee) - 30), t, font=f_small, fill=INK)

    axis = bottom
    if has_w:                                                   # вес отдельной панелью: своя шкала, те же даты
        wtop, wbottom = 428, 520
        lo, hi = min(w["kg"] for w in wts), max(w["kg"] for w in wts)
        if hi - lo < 1:
            lo, hi = lo - 0.5, hi + 0.5
        pad = (hi - lo) * 0.15
        lo, hi = lo - pad, hi + pad
        YW = lambda v: wbottom - (wbottom - wtop) * (v - lo) / (hi - lo)
        for v in (lo + pad, hi - pad):
            draw.line((left, YW(v), right, YW(v)), fill=LINE, width=2)
            draw.text((left - 12 - draw.textlength(f"{v:.1f}", font=f_small), YW(v) - 12), f"{v:.1f}", font=f_small, fill=MUTED)
        draw.text((left, wtop - 32), "Вес, кг", font=f_small, fill=INK)
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
        draw.line([(x, y) for x, y, _, _ in pts], fill=INK, width=3, joint="curve")
        for x, y, _, _ in pts:
            draw.ellipse((x - 6, y - 6, x + 6, y + 6), fill=BG, outline=INK, width=3)
        lastp = pts[-1]
        draw.text((min(lastp[0] + 10, right - 60), lastp[1] - 30), f"{lastp[2]:g}", font=f_small, fill=INK)
        axis = wbottom

    f_day = cards._font(20 if nb > 14 else 22)
    every = 1 if nb <= 14 else 2 if nb <= 20 else 3
    for i, d in enumerate(bars):
        if i % every == 0 or i == nb - 1:
            cx = left + slot * (i + 0.5)
            dt = datetime.date.fromisoformat(d["day"])
            label = f"{dt.day:02d}.{dt.month:02d}" if weekly else f"{dt.day}"
            draw.text((cx - draw.textlength(label, font=f_day) / 2, axis + 8), label, font=f_day, fill=INK)
            if not weekly and nb <= 14:
                wd = WEEKDAYS[dt.weekday()]
                draw.text((cx - draw.textlength(wd, font=f_small) / 2, axis + 32), wd, font=f_small, fill=MUTED)

    ly = H - 96                                                 # легенда и оговорка
    x = left
    legend = [(MACRO_COLORS["protein"], "белки"), (MACRO_COLORS["fat"], "жиры"), (MACRO_COLORS["carb"], "углеводы")]
    if any_none:
        legend.append((NO_MACRO, "без БЖУ"))
    for col, name in legend:
        draw.rounded_rectangle((x, ly + 4, x + 22, ly + 22), radius=4, fill=col)
        draw.text((x + 30, ly), name, font=f_small, fill=INK)
        x += 30 + int(draw.textlength(name, font=f_small)) + 30
    draw.text((left, H - 64), REFERENCE, font=cards._font(20), fill=MUTED)
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
