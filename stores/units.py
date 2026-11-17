"""
Штучные продукты в количествах плана: ИИ пишет «бананы=9», «яблоки=6» — это штуки, а не граммы.
Для таких продуктов (и только при малом числе) считаем штуками, а для весовых товаров магазина переводим в граммы.
"""
import re

# основа названия -> средний вес одной штуки, г (None — вес не нужен: яйца берутся упаковками по штукам)
PIECE_WEIGHT_G: dict[str, int | None] = {
    "яйц": None, "банан": 120, "яблок": 150, "груш": 150, "апельсин": 200, "мандарин": 80, "лимон": 100,
    "киви": 80, "персик": 150, "нектарин": 150, "авокадо": 200, "слив": 40, "гранат": 250, "грейпфрут": 300,
    "луковиц": 100, "лайм": 70,
}
_MAX_PIECES = 40   # больше — это уже граммы


# прилагательное от плода — это другой продукт: «сливочное масло», «сливки», «лимонный сок», «апельсиновый сок» (аудит 15.11.2026)
_ADJ = r"(?:н[ыоаую]|ов(?:ый|ая|ое|ые|ого|ую|ом)|ев(?:ый|ая|ое|ые)|оч|еч|к[иау])"


def _stem_of(name: str) -> str | None:
    n = name.lower().replace("ё", "е")
    for st in PIECE_WEIGHT_G:
        for m in re.finditer(r"(?<![а-я])" + st, n):
            if not re.match(_ADJ, n[m.end():]):
                return st
    return None


def is_pieces(name: str, value: float) -> bool:
    """True, если число value для продукта name — штуки."""
    return value <= _MAX_PIECES and _stem_of(name) is not None


def pieces_to_grams(name: str, value: float) -> float:
    st = _stem_of(name)
    w = PIECE_WEIGHT_G.get(st) if st else None
    return value * (w or 100)
