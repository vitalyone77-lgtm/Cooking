"""
Разбор технических строк из ответов LLM и ссылки на поиск продуктов в магазинах
(Пятёрочка, ВкусВилл, Перекрёсток, Яндекс Лавка, Купер — см. пакет stores/).
"""
import re

from stores.links import search_url, short_name, full_name
from stores.prefs import get_enabled
from stores.units import is_pieces

SEARCH_TERMS_RE = re.compile(r"ПРОДУКТЫ_СПИСОК:\s*(.+)", re.IGNORECASE)
DISH_TITLE_RE = re.compile(r"^\s*🍽\s*(.+)$", re.MULTILINE)
BASE_PRODUCTS_RE = re.compile(r"БАЗОВЫЕ_ПРОДУКТЫ:\s*(.+)", re.IGNORECASE)
USED_BASE_RE = re.compile(r"ИСПОЛЬЗОВАНО_БАЗОВЫЕ:\s*(.+)", re.IGNORECASE)


def extract_dish_title(recipe_text: str) -> str:
    """Достаёт название блюда из первой строки рецепта (после эмодзи 🍽)."""
    match = DISH_TITLE_RE.search(recipe_text)
    return match.group(1).strip() if match else ""


_PRODUCTS_HEADER_RE = re.compile(r"^\s*[*_]*Продукты[*_]*:?[*_]*\s*$", re.IGNORECASE | re.MULTILINE)
_BULLET_RE = re.compile(r"^[-•–*]\s+")


def _terms_from_products_section(text: str) -> list[str]:
    """
    Запасной разбор: если ИИ пропустил служебную строку «ПРОДУКТЫ_СПИСОК», берём названия
    из раздела «Продукты:» самого рецепта (до тире с количеством, без скобок). Лучше ссылки
    по названиям из рецепта, чем пустой список покупок.
    """
    header = _PRODUCTS_HEADER_RE.search(text)
    if not header:
        return []
    terms: list[str] = []
    seen: set[str] = set()
    for line in text[header.end():].splitlines():
        s = line.strip()
        if not s:
            if terms:
                break
            continue
        if not _BULLET_RE.match(s):
            break  # начался следующий раздел («Инструкция» и т.п.)
        item = _BULLET_RE.sub("", s)
        item = re.sub(r"\([^)]*\)", "", item)
        item = re.split(r"\s[—–-]\s", item, maxsplit=1)[0]
        for part in item.split(","):
            name = part.strip(" .*_`")
            if name and name.lower() not in seen:
                seen.add(name.lower())
                terms.append(name)
    return terms[:30]


def extract_shopping_terms(recipe_text: str) -> tuple[str, list[str]]:
    """
    Ищет в тексте рецепта техническую строку "ПРОДУКТЫ_СПИСОК: ...", убирает её
    из текста и возвращает (очищенный_текст, список_продуктов). Если строки нет —
    продукты берутся из раздела «Продукты:» рецепта.
    """
    match = SEARCH_TERMS_RE.search(recipe_text)
    if not match:
        return recipe_text.strip(), _terms_from_products_section(recipe_text)

    terms_raw = match.group(1)
    terms = [t.strip(" .") for t in terms_raw.split(",") if t.strip(" .")]

    clean_text = recipe_text[:match.start()].rstrip()
    if not clean_text:
        # ИИ вывел техническую строку без самого рецепта (редкий сбой формата) —
        # лучше показать пользователю хоть что-то, чем пустое сообщение.
        clean_text = recipe_text.strip()
    return clean_text, terms


def _parse_kv_amounts(raw: str) -> dict[str, int]:
    """Разбирает 'продукт1=число, продукт2=число, ...' в {продукт: целое_число}."""
    result: dict[str, int] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, _, value = part.partition("=")
        name = name.strip(" .").lower()
        digits = re.sub(r"[^\d]", "", value)
        if name and digits:
            result[name] = int(digits)
    return result


def extract_base_products(basket_text: str) -> tuple[str, dict[str, int]]:
    """
    Для "Меню на несколько дней": ищет техническую строку "БАЗОВЫЕ_ПРОДУКТЫ: продукт=число, ...",
    убирает её из текста (пользователь её не должен видеть) и возвращает остальное вместе с
    {продукт: количество} для учёта остатков.
    """
    match = BASE_PRODUCTS_RE.search(basket_text)
    if not match:
        return basket_text.strip(), {}

    products = _parse_kv_amounts(match.group(1))
    clean_text = (basket_text[:match.start()] + basket_text[match.end():]).strip()
    clean_text = re.sub(r"\n{3,}", "\n\n", clean_text)
    if not clean_text:
        clean_text = basket_text.strip()
    return clean_text, products


def extract_used_base_products(block_text: str) -> dict[str, int]:
    """Разбирает служебный блок 'ИСПОЛЬЗОВАНО_БАЗОВЫЕ: продукт=число, ...' в словарь расхода."""
    match = USED_BASE_RE.search(block_text)
    if not match:
        return {}
    return _parse_kv_amounts(match.group(1))


def build_five_ka_link(term: str) -> str:
    return search_url("fiveka", term)


def format_shopping_message(terms: list[str], chat_id: int | None = None) -> str:
    """
    Список продуктов со ссылками на поиск в выбранных пользователем магазинах
    (по умолчанию во всех: Пятёрочка, ВкусВилл, Перекрёсток, Яндекс Лавка, Купер).
    """
    if not terms:
        return ""
    stores = get_enabled(chat_id)
    lines = ["🛒 *Найти продукты:*\n"]
    for term in terms:
        links = " · ".join(f"[{short_name(s)}]({search_url(s, term)})" for s in stores)
        lines.append(f"• {term} — {links}")
    lines.append("\n_Ссылки открывают поиск по сайту магазина. Магазины можно выбрать в «🏪 Магазины»._")
    return "\n".join(lines)


_AMOUNT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(кг|гр|г|мл|л|шт)(?![а-яёa-z])", re.IGNORECASE)


def amounts_from_recipe_text(text: str) -> dict[str, int]:
    """
    Из раздела «Продукты:» рецепта достаёт количества: {продукт: граммы} (мл считаем за граммы, яйца — штуки).
    Строки без числа («соль — по вкусу», «2 ст.л.») пропускаются: их в магазине обычно не считают.
    Нужно для сборки корзины ВкусВилла под рецепт или меню на день.
    """
    header = _PRODUCTS_HEADER_RE.search(text or "")
    if not header:
        return {}
    result: dict[str, int] = {}
    for line in text[header.end():].splitlines():
        s = line.strip()
        if not s:
            if result:
                break
            continue
        if not _BULLET_RE.match(s):
            break
        item = _BULLET_RE.sub("", s)
        name_part, sep, rest = re.sub(r"\([^)]*\)", "", item).partition(" — ")
        if not sep:
            name_part, sep, rest = item.partition(" — ")
        name = name_part.strip(" .*_`").lower()
        if not name or "," in name or name in ("вода", "кипяток", "лёд", "лед", "вода питьевая"):
            continue  # воду из крана не покупаем
        grams = pieces = 0.0
        for m in _AMOUNT_RE.finditer(item.partition(" — ")[2]):
            value, unit = float(m.group(1).replace(",", ".")), m.group(2).lower()
            if unit == "шт":
                pieces = pieces or value
            elif not grams:
                grams = value * (1000 if unit in ("кг", "л") else 1)
        amount = grams or (pieces if is_pieces(name, pieces) else 0)
        if amount > 0:
            result[name] = result.get(name, 0) + int(round(amount))
    return result


def merge_amounts(parts: list[dict[str, int]]) -> dict[str, int]:
    """Складывает количества одних и тех же продуктов из нескольких блюд."""
    total: dict[str, int] = {}
    for d in parts:
        for k, v in d.items():
            total[k] = total.get(k, 0) + v
    return total


def terms_from_recipe_text(text: str) -> list[str]:
    """Продукты из раздела «Продукты:» готового текста рецепта (для кнопки «Найти продукты в магазине»)."""
    return _terms_from_products_section(text or "")


def _link_label(term: str) -> str:
    """Название продукта как текст Markdown-ссылки: без символов, ломающих разметку."""
    return re.sub(r"[\[\]*_`]", "", term).strip() or term


def _fmt_amount(name: str, value: int) -> str:
    if is_pieces(name, value):
        return f"{value} шт"
    if value >= 1000:
        return f"{value / 1000:.1f}".rstrip("0").rstrip(".").replace(".", ",") + " кг"
    return f"{value} г"


def _find_amount(term: str, amounts: dict[str, int]) -> tuple[str, int] | None:
    """Количество для продукта: точное совпадение названия, иначе по общей основе слова (первые 4 буквы)."""
    t = term.lower().strip()
    if t in amounts:
        return t, amounts[t]
    stems = [w[:4] for w in re.findall(r"[а-яёa-z]{4,}", t)]
    for key, val in amounts.items():
        k = key.lower()
        if stems and all(st in k for st in stems):
            return key, val
    for key, val in amounts.items():
        k_stems = [w[:4] for w in re.findall(r"[а-яёa-z]{4,}", key.lower())]
        if k_stems and all(st in t for st in k_stems):
            return key, val
    return None


def format_store_links(terms: list[str], store: str, amounts: dict[str, int] | None = None) -> str:
    """Список продуктов со ссылками на поиск ТОЛЬКО в одном магазине; после ссылки — сколько нужно купить."""
    if not terms:
        return ""
    lines = [f"🛒 *Продукты — {full_name(store)}*", ""]
    used: set[str] = set()
    for term in terms:
        line = f"• [{_link_label(term)}]({search_url(store, term)})"
        found = _find_amount(term, amounts) if amounts else None
        if found:
            used.add(found[0])
            line += f" — {_fmt_amount(term, found[1])}"
        lines.append(line)
    rest = [(k, v) for k, v in (amounts or {}).items() if k not in used and v]
    if rest:
        lines.append("")
        lines.append("Ещё по рецептам понадобится: " + ", ".join(f"{k} — {_fmt_amount(k, v)}" for k, v in rest))
    lines.append("")
    lines.append("_Нажми на продукт — откроется поиск в магазине, там можно добавить его в корзину и оформить заказ. "
                 "Справа от названия — сколько нужно купить. Другой магазин — кнопки ниже._")
    return "\n".join(lines)
