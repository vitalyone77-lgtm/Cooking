"""
Ссылки на поиск продуктов в Пятёрочке (5ka.ru).

У Пятёрочки нет официального публичного API каталога, а прямой поиск по их сайту
(5ka.ru/search) закрыт защитой от ботов и требует JavaScript — надёжно сформировать
ссылку на него нельзя. Поэтому используем поиск Яндекса, ограниченный доменом
5ka.ru (site:5ka.ru) — такая ссылка работает стабильно и в реальном браузере
пользователя откроет то, что реально есть на сайте магазина, а не наши догадки.
"""
import re
from urllib.parse import quote

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
    # Прямой поиск по каталогу 5ka.ru (без внешнего поисковика).
    return f"https://5ka.ru/search/?text={quote(term)}"


def format_shopping_message(terms: list[str]) -> str:
    if not terms:
        return ""
    lines = ["🛒 *Найти продукты в Пятёрочке:*\n"]
    for term in terms:
        link = build_five_ka_link(term)
        lines.append(f"• [{term}]({link})")
    lines.append(
        "\n_Ссылки открывают поиск по сайту 5ka.ru — выбери подходящий товар из результатов._"
    )
    return "\n".join(lines)
