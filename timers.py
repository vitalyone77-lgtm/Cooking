"""
Время в шагах рецепта: «вари 5–6 мин», «8 мин», «1,5 часа» → кнопки-таймеры в боте (на сайте то же делает JS в web/index.html).
Диапазон «5–6 мин»: сигнал на 5-й минуте, а время «максимум» — 6-я.
"""
import re

_TIME_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)(?:\s*[–—-]\s*(\d+(?:[.,]\d+)?))?\s*(мин[а-яё.]*|час(?:а|ов)?(?![а-яё]))", re.IGNORECASE)
_STEP_RE = re.compile(r"^\s*(\d+)[.)]\s+(.*)")
_INSTR_RE = re.compile(r"^\s*[*_]*Инструкция[*_]*:?[*_]*\s*$", re.IGNORECASE | re.MULTILINE)


def fmt_min(m: float) -> str:
    """90 -> «1 ч 30 мин», 5 -> «5 мин»."""
    m = int(round(m))
    if m >= 60:
        h, r = divmod(m, 60)
        return f"{h} ч" + (f" {r} мин" if r else "")
    return f"{m} мин"


def parse_times(line: str, limit: int = 2) -> list[tuple[int, int]]:
    """[(min_минут, max_минут)] для времён в строке (секунды игнорируем); без повторов."""
    out: list[tuple[int, int]] = []
    for m in _TIME_RE.finditer(line):
        k = 60 if m.group(3).lower().startswith("час") else 1
        a = float(m.group(1).replace(",", ".")) * k
        b = float(m.group(2).replace(",", ".")) * k if m.group(2) else a
        if a <= 0 or b < a or b > 360:
            continue
        pair = (int(round(a)), int(round(b)))
        if pair not in out:
            out.append(pair)
        if len(out) >= limit:
            break
    return out


def step_timers(recipe_text: str, max_total: int = 6) -> list[tuple[int, int, int]]:
    """[(номер_шага, минуты_сигнал, минуты_максимум)] по шагам раздела «Инструкция»."""
    header = _INSTR_RE.search(recipe_text or "")
    body = recipe_text[header.end():] if header else (recipe_text or "")
    res: list[tuple[int, int, int]] = []
    for line in body.splitlines():
        m = _STEP_RE.match(line)
        if not m:
            continue
        for a, b in parse_times(m.group(2)):
            res.append((int(m.group(1)), a, b))
            if len(res) >= max_total:
                return res
    return res


def label(mn: int, mx: int) -> str:
    """«5–6 мин» или «8 мин»."""
    if mn == mx:
        return fmt_min(mn)
    if mx < 60:
        return f"{mn}–{mx} мин"
    return f"{fmt_min(mn)} – {fmt_min(mx)}"
