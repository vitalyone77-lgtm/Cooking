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
    return clean_text, terms


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
