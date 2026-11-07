"""
Арифметика рецептов без ИИ: пересчёт количеств на другое число порций и сложение продуктов из нескольких рецептов в один список.
Текст может быть с разметкой Markdown или HTML: заголовок раздела узнаём и в ней, строки вне раздела «Продукты» не меняем, кроме числа порций.
"""
import re
from fractions import Fraction

SERVINGS = (1, 2, 3, 4, 6)
MAX_SERVINGS = 12
_SERV_RE = re.compile(r"(👥\s*Порци[йия]\s*:?\s*)(\d+)")
_HEADER_RE = re.compile(r"^\s*(?:<[^>]+>|[*_])*(?:Продукты|Ингредиенты)(?:<[^>]+>|[*_:])*\s*$", re.I | re.M)
_BULLET_RE = re.compile(r"^(\s*(?:<[^>]+>)*[-•–*]\s+)")
_DASH_RE = re.compile(r"\s[—–-]\s")
_UNICODE = {"½": Fraction(1, 2), "¼": Fraction(1, 4), "¾": Fraction(3, 4), "⅓": Fraction(1, 3), "⅔": Fraction(2, 3)}
_NUM = r"\d+\s*/\s*\d+|[½¼¾⅓⅔]|\d+(?:[.,]\d+)?"
_NUM_RE = re.compile(rf"({_NUM})(\s*)([а-яёa-z%.]*)", re.I)
_APPROX_RE = re.compile(r"\s*\([^)]*\d[^)]*\)")          # «(≈ 1 небольшая луковица)»: при пересчёте не согласуется с числом, убираем
_UNIT_AFTER_RE = re.compile(rf"({_NUM})\s*(кг|гр|г|мл|л|шт)(?![а-яёa-z])", re.I)


def base_servings(text: str) -> int | None:
    """На сколько порций написан рецепт; None — в тексте не указано, пересчитывать нельзя."""
    m = _SERV_RE.search(text or "")
    n = int(m.group(2)) if m else 0
    return n if 1 <= n <= 50 else None


def _value(token: str) -> float:
    token = token.replace(" ", "")
    if token in _UNICODE:
        return float(_UNICODE[token])
    if "/" in token:
        a, b = token.split("/")
        return int(a) / int(b) if int(b) else 0.0
    return float(token.replace(",", "."))


def _fmt(v: float) -> str:
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return s.replace(".", ",")


def _round(v: float, unit: str) -> float:
    u = unit.lower().rstrip(".")
    if u in ("г", "гр", "мл"):
        step = 1 if v < 20 else 5 if v < 100 else 10
        return max(1, round(v / step) * step)
    if u in ("кг", "л"):
        return max(0.05, round(v * 20) / 20)
    return max(0.5, round(v * 2) / 2)      # штуки и ложки: половинами


def _scale_amount(amount: str, k: float, need_unit: bool = False) -> str:
    amount = _APPROX_RE.sub("", amount)

    def repl(m: re.Match) -> str:
        token, gap, unit = m.group(1), m.group(2), m.group(3)
        if unit.startswith("%") or unit.lower().startswith("щепот") or (need_unit and not re.match(r"(кг|гр|г|мл|л|шт)(?![а-яё])", unit, re.I)):
            return m.group(0)
        return _fmt(_round(_value(token) * k, unit)) + gap + unit
    return _NUM_RE.sub(repl, amount)


def _scale_line(line: str, k: float) -> str:
    bullet = _BULLET_RE.match(line)
    if not bullet:
        return line
    head, rest = line[:bullet.end()], line[bullet.end():]
    dash = _DASH_RE.search(rest)
    if dash:
        return head + rest[:dash.end()] + _scale_amount(rest[dash.end():], k)
    return head + _scale_amount(rest, k, need_unit=True)       # «мука 200 г» без тире: только число с единицей, иначе заденем «молоко 1,5%»


def scale_text(text: str, to: int) -> str:
    """Рецепт на to порций. Если число порций неизвестно или то же самое, текст не меняется. Числа в шагах остаются: об этом предупреждает пояснение."""
    base = base_servings(text)
    if not base or base == to or not 1 <= to <= MAX_SERVINGS:
        return text
    k = to / base
    out = _SERV_RE.sub(lambda m: m.group(1) + str(to), text, count=1)
    header = _HEADER_RE.search(out)
    if not header:
        return out
    lines = out[header.end():].split("\n")
    i, seen = 0, False
    while i < len(lines):
        s = lines[i].strip()
        if not s:
            if seen:
                break
        elif _BULLET_RE.match(lines[i]):
            lines[i], seen = _scale_line(lines[i], k), True
        else:
            break
        i += 1
    note = f"ℹ️ Количества пересчитаны с {base} на {to} порц. Если в шагах названо количество, оно осталось для {base} порц."
    lines[i:i] = ["", note]
    return out[:header.end()] + "\n".join(lines)


# ---------- общий список из нескольких рецептов ----------

_SKIP = {"вода", "вода питьевая", "кипяток", "лед", "лёд"}
_UNITS = (("кг", "g", 1000), ("гр", "g", 1), ("г", "g", 1), ("мл", "ml", 1), ("л", "ml", 1000), ("шт", "pcs", 1))
_AMOUNT_RE = re.compile(rf"({_NUM})\s*(ст\.?\s*л\.?|ч\.?\s*л\.?|кг|гр|г|мл|л|шт)(?![а-яёa-z])", re.I)


def _name_key(name: str) -> str:
    """Один продукт в разных рецептах: те же слова в любом порядке и падеже («Помидоры» и «помидор»), но «масло оливковое» и «масло сливочное» не сливаем."""
    words = re.findall(r"[а-яa-z]{3,}", name.lower().replace("ё", "е"))
    return " ".join(sorted(w[:5] for w in words)) or name.lower().strip()


def _parse_items(text: str) -> list[tuple[str, str]]:
    header = _HEADER_RE.search(text or "")
    if not header:
        return []
    out, seen = [], False
    for line in text[header.end():].split("\n"):
        s = line.strip()
        if not s:
            if seen:
                break
            continue
        bullet = _BULLET_RE.match(line)
        if not bullet:
            break
        seen = True
        rest = re.sub(r"[*_`]", "", line[bullet.end():]).strip()
        dash = _DASH_RE.search(rest)
        if dash:
            name, amount = rest[:dash.start()], rest[dash.end():]
        else:
            m = _AMOUNT_RE.search(rest)
            name, amount = (rest[:m.start()], rest[m.start():]) if m else (rest, "")
        name = re.sub(r"\([^)]*\)", "", name).strip(" .,;")
        if name and "," in name and not amount:              # «Соль, перец»: два продукта одной строкой
            out += [(p.strip(), "") for p in name.split(",") if p.strip()]
        elif name:
            out.append((name, amount.strip(" .,;")))
    return out


def _amount_text(sums: dict[str, float], other: str) -> str:
    parts = []
    if sums.get("g"):
        parts.append(_fmt(round(sums["g"] / 1000, 2)) + " кг" if sums["g"] >= 1000 else _fmt(round(sums["g"])) + " г")
    if sums.get("ml"):
        parts.append(_fmt(round(sums["ml"] / 1000, 2)) + " л" if sums["ml"] >= 1000 else _fmt(round(sums["ml"])) + " мл")
    if sums.get("pcs"):
        parts.append(_fmt(sums["pcs"]) + " шт")
    if sums.get("tbsp"):
        parts.append(_fmt(sums["tbsp"]) + " ст. л.")
    if sums.get("tsp"):
        parts.append(_fmt(sums["tsp"]) + " ч. л.")
    return " + ".join(parts) or other


def merge_recipes(texts: list[str]) -> list[dict]:
    """Продукты из нескольких рецептов: одинаковые сложены, разные единицы написаны через плюс. Количество словами («по вкусу») остаётся, если чисел нет. -> [{name, amount}]"""
    merged: dict[str, dict] = {}
    for text in texts:
        for name, amount in _parse_items(text):
            if name.lower().replace("ё", "е") in {x.replace("ё", "е") for x in _SKIP}:
                continue                   # воду из крана не покупаем
            row = merged.setdefault(_name_key(name), {"name": name, "sums": {}, "other": ""})
            found = False
            for m in _AMOUNT_RE.finditer(_APPROX_RE.sub("", amount)):
                value, unit = _value(m.group(1)), re.sub(r"[\s.]", "", m.group(2).lower())
                if unit == "стл":
                    row["sums"]["tbsp"] = row["sums"].get("tbsp", 0) + value
                elif unit == "чл":
                    row["sums"]["tsp"] = row["sums"].get("tsp", 0) + value
                else:
                    kind, mult = next((b, c) for a, b, c in _UNITS if a == unit)
                    row["sums"][kind] = row["sums"].get(kind, 0) + value * mult
                found = True
                break                      # первое число: остальное обычно пояснение
            if not found and amount and not row["other"]:
                row["other"] = amount[:24]
    return [{"name": r["name"][:80], "amount": _amount_text(r["sums"], r["other"])} for r in merged.values()]
