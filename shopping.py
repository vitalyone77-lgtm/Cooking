"""
Разбор технических строк из ответов LLM и ссылки на поиск продуктов в магазинах
(Пятёрочка, ВкусВилл, Яндекс Лавка, Купер — см. пакет stores/).
"""
import re

from stores.links import search_url, short_name
from stores.prefs import get_enabled

SEARCH_TERMS_RE = re.compile(r"ПРОДУКТЫ_СПИСОК:\s*(.+)", re.IGNORECASE)
DISH_TITLE_RE = re.compile(r"^\s*🍽\s*(.+)$", re.MULTILINE)
BASE_PRODUCTS_RE = re.compile(r"БАЗОВЫЕ_ПРОДУКТЫ:\s*(.+)", re.IGNORECASE)
USED_BASE_RE = re.compile(r"ИСПОЛЬЗОВАНО_БАЗОВЫЕ:\s*(.+)", re.IGNORECASE)


def extract_dish_title(recipe_text: str) -> str:
    """Достаёт название блюда из первой строки рецепта (после эмодзи 🍽)."""
    match = DISH_TITLE_RE.search(recipe_text)
    return match.group(1).strip() if match else ""


def extract_shopping_terms(recipe_text: str) -> tuple[str, list[str]]:
    """
    Ищет в тексте рецепта техническую строку "ПРОДУКТЫ_СПИСОК: ...", убирает её
    из текста и возвращает (очищенный_текст, список_продуктов).
    """
    match = SEARCH_TERMS_RE.search(recipe_text)
    if not match:
        return recipe_text.strip(), []

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
    (по умолчанию во всех: Пятёрочка, ВкусВилл, Яндекс Лавка, Купер).
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
