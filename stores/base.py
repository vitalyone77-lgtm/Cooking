"""
Общие типы для магазинов: предложение (Offer) и разбор размера упаковки из названия товара.
"""
import re
from dataclasses import dataclass

# "2 х 200 г", "6x50г" — мультипак
_MULTIPACK_RE = re.compile(
    r"(\d+)\s*[xх×*]\s*(\d+(?:[.,]\d+)?)\s*(кг|гр?|мл|л)\b", re.IGNORECASE
)
# "500 г", "1,5 кг", "0.9 л", "330мл"
_PACK_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(кг|гр?|мл|л)\b", re.IGNORECASE)


def _to_grams(value: float, unit: str) -> float:
    unit = unit.lower()
    if unit in ("кг", "л"):
        return value * 1000
    return value  # г, гр, мл — считаем 1 мл ≈ 1 г (для расчёта корзины этого достаточно)


def parse_pack_size_g(name: str) -> float | None:
    """Размер одной упаковки в граммах, если он указан в названии товара, иначе None."""
    if not name:
        return None
    m = _MULTIPACK_RE.search(name)
    if m:
        count = int(m.group(1))
        value = float(m.group(2).replace(",", "."))
        return count * _to_grams(value, m.group(3))
    m = _PACK_RE.search(name)
    if m:
        return _to_grams(float(m.group(1).replace(",", ".")), m.group(2))
    return None


@dataclass
class Offer:
    """Один товар из каталога магазина. Цена — за ОДНУ упаковку/штуку."""
    store: str                 # ключ магазина, например "vkusvill"
    name: str
    price: float               # руб. за упаковку
    pack_g: float | None       # размер упаковки в граммах (None — не удалось определить)
    url: str = ""
    product_id: str = ""       # id товара в магазине (для корзины)
    approx: bool = False       # размер упаковки оценён приблизительно (например, "цена за кг")
    by_weight: bool = False    # весовой товар: цена за 1 кг, количество можно брать дробное (1,5 кг)


@dataclass
class CartLine:
    """Строка итоговой корзины: сколько упаковок какого товара берём под конкретный продукт."""
    need_name: str             # что нужно по рецепту ("куриное филе")
    need_g: float              # сколько нужно грамм
    offer: Offer
    count: float               # число упаковок (для весового товара — килограммы, может быть дробным)

    @property
    def total_price(self) -> float:
        return self.offer.price * self.count

    @property
    def total_g(self) -> float:
        return (self.offer.pack_g or 0) * self.count
