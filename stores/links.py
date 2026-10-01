"""
Ссылки на поиск товара в разных магазинах + настройки «какие магазины показывать».
"""
from urllib.parse import quote

import config

# ключ -> (короткое название для строки, полное название, шаблон ссылки)
STORES: dict[str, tuple[str, str, str]] = {
    "fiveka": ("Пятёрочка", "Пятёрочка", config.STORE_URL_FIVEKA),
    "vkusvill": ("ВкусВилл", "ВкусВилл", config.STORE_URL_VKUSVILL),
    "lavka": ("Лавка", "Яндекс Лавка", config.STORE_URL_LAVKA),
    "kuper": ("Купер", "Купер (СберМаркет)", config.STORE_URL_KUPER),
}
DEFAULT_STORES = ["fiveka", "vkusvill", "lavka", "kuper"]


def search_url(store: str, term: str) -> str:
    return STORES[store][2].format(q=quote(term))


def short_name(store: str) -> str:
    return STORES[store][0]


def full_name(store: str) -> str:
    return STORES[store][1]
