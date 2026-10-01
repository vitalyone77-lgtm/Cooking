"""
Корзина с ценами из ВкусВилла: для каждого продукта находим товары и считаем, сколько взять:
- весовые товары (цена за 1 кг: курица, морковь, лук…) берём точно в нужном количестве (шаг 50 г);
- фасованные — подбираем упаковки «минимально достаточно, как можно дешевле» (optimizer.py).
Считаем стоимость и собираем ссылку-корзину (официальный MCP ВкусВилла, до 20 позиций).
Если сервер недоступен — возвращаем None, а вызывающий код показывает только обычные ссылки на поиск.
"""
import asyncio
import logging
import math
import re

import config
from .base import Offer, CartLine
from .optimizer import choose_packs
from .vkusvill import VkusvillClient

logger = logging.getLogger(__name__)

# Счётные продукты: количество — штуки, а не граммы
_PIECE_WORDS = ("яйц",)
_PIECES_RE = re.compile(r"(\d+)\s*(?:шт|штук)\b", re.IGNORECASE)
_EGGS_DEFAULT_PACK = 10          # «Яйцо куриное С1» без числа в названии — стандартный десяток

# Основа слова из запроса (первые 4 буквы) -> другие основы, под которыми то же самое названо в каталоге
_SYNONYMS = {
    "кури": ("цыплен", "бройл"),
    "цыпл": ("кури", "бройл"),
    "поми": ("томат",),
    "тома": ("помид",),
    "говя": ("говяж", "телят"),
    "карт": ("картоф",),
    "болг": ("сладк",),
}

# Готовые блюда в каталоге («Гречка отварная с овощами»): если среди найденного есть и сырьё, их отбрасываем
_PREPARED_MARKERS = ("отварн", "запечен", "гриль", "салат", "суп ", "котлет", "готов", "жарен", "тушен",
                     "пюре", "рагу", "паштет", "с овощ", "с киноа", "с рисом", "с мясом", "с курицей")


def _stem(word: str) -> str:
    """Основа слова без окончания: яйца -> яйц, курица -> кури, помидоры -> помидо, перец -> пере."""
    n = len(word)
    if n <= 3:
        return word
    if n == 4:
        return word[:3]
    if n == 5:
        return word[:4]
    return word[:min(n - 2, 6)]


def _is_piece_item(name: str) -> bool:
    n = name.lower()
    return any(w in n for w in _PIECE_WORDS)


def _alts(stem: str) -> tuple[str, ...]:
    return (stem, *_SYNONYMS.get(stem[:4], ()))


def _starts_word(name_l: str, prefix: str) -> bool:
    return re.search(r"(?<![а-яёa-z])" + re.escape(prefix), name_l) is not None


def _relevant(term: str, offers: list[Offer], relaxed: bool = False) -> list[Offer]:
    """
    Оставляем товары, подходящие под продукт. Строго: в названии есть КАЖДОЕ слово запроса (или его
    синоним) — так «куриное филе» не превращается в «филе минтая». При relaxed=True достаточно любого слова.
    Готовые блюда («Гречка отварная с овощами») отбрасываются, если среди найденного есть сырьё.
    """
    stems = [_stem(w) for w in re.findall(r"[а-яёa-z]{3,}", term.lower())]
    if not stems:
        return offers
    groups = [_alts(s) for s in stems]
    match = any if relaxed else all
    found = [o for o in offers if match(any(_starts_word(o.name.lower(), a) for a in g) for g in groups)]
    raw = [o for o in found if not any(m in (o.name.lower() + " ") for m in _PREPARED_MARKERS)]
    return raw or found


def _usable(offers: list[Offer]) -> bool:
    return any(o.by_weight or (o.pack_g and o.pack_g > 0) for o in offers)


def _prepare_offers(term: str, offers: list[Offer]) -> list[Offer]:
    if _is_piece_item(term):
        res = []
        for o in offers:
            m = _PIECES_RE.search(o.name)
            if m:
                o.pack_g = float(m.group(1))        # для яиц pack_g = число штук
            elif _is_piece_item(o.name):
                o.pack_g = float(_EGGS_DEFAULT_PACK)
                o.approx = True
            else:
                continue
            res.append(o)
        return res
    return offers


async def _cart_for_term(client: VkusvillClient, sem: asyncio.Semaphore,
                         term: str, need: float) -> tuple[str, list[CartLine]]:
    async with sem:
        try:
            offers = await client.search(term)
        except Exception as e:
            logger.warning("ВкусВилл: поиск %r не удался: %s", term, e)
            return term, []
    prepared = _prepare_offers(term, _relevant(term, offers))
    if not _usable(prepared):
        # строгий отбор дал только товары без известного веса — пробуем шире («перец болгарский» -> «перец сладкий»)
        prepared = _prepare_offers(term, _relevant(term, offers, relaxed=True))
    offers = prepared
    if not _is_piece_item(term):
        weighed = [o for o in offers if o.by_weight]
        if weighed:
            best = min(weighed, key=lambda o: o.price)
            qty = max(0.1, math.ceil(need / 1000 * 20) / 20)      # шаг 50 г, не меньше 100 г
            return term, [CartLine(need_name=term, need_g=need, offer=best, count=qty)]
    return term, choose_packs(term, need, [o for o in offers if not o.by_weight])


def _fmt_g(v: float) -> str:
    return f"{v / 1000:.1f} кг".replace(".0", "") if v >= 1000 else f"{int(v)} г"


def _fmt_qty(cl: CartLine) -> str:
    if cl.offer.by_weight:
        return f"{cl.count:.2f}".rstrip("0").rstrip(".").replace(".", ",") + " кг"
    return str(int(cl.count))


async def _run(inventory: dict[str, int], scope: str) -> str | None:
    last_error: Exception | None = None
    client = None
    for url in config.VKUSVILL_MCP_URLS:
        c = VkusvillClient(url)
        try:
            await c.start()
            client = c
            break
        except Exception as e:
            last_error = e
            logger.warning("ВкусВилл MCP %s недоступен: %s", url, e)
            await c.close()
    if client is None:
        logger.warning("ВкусВилл: ни один MCP-сервер не ответил (%s)", last_error)
        return None

    try:
        sem = asyncio.Semaphore(4)
        items = [(n, a) for n, a in inventory.items() if a > 0]
        results = await asyncio.gather(*[_cart_for_term(client, sem, n, float(a)) for n, a in items])

        lines, not_found, cart_items, total = [], [], [], 0.0
        for (term, need), (_, cart) in zip(items, results):
            if not cart:
                not_found.append(term)
                continue
            unit = "шт" if _is_piece_item(term) else None
            need_txt = f"{int(need)} шт" if unit else _fmt_g(need)
            parts = []
            for cl in cart:
                total += cl.total_price
                cart_items.append((cl.offer.product_id, cl.count))
                parts.append(f"{cl.offer.name} × {_fmt_qty(cl)} = {cl.total_price:.0f} ₽")
            lines.append(f"• {term} ({need_txt}) → " + "; ".join(parts))

        if not lines:
            return None

        out = [f"🥬 *Корзина ВкусВилл* (минимум, которого хватит на {scope}):\n"]
        out.extend(lines)
        out.append(f"\n💰 *Итого: {total:.0f} ₽*")
        if not_found:
            out.append("Не нашёл в ВкусВилле: " + ", ".join(not_found))
        try:
            link = await client.cart_link(cart_items)
        except Exception as e:
            logger.warning("ВкусВилл: не удалось создать ссылку-корзину: %s", e)
            link = None
        if link:
            out.append(f"\n🛒 [Открыть корзину во ВкусВилле]({link})")
        out.append("\n_Цены и наличие ориентировочные — окончательные видны в приложении магазина._")
        return "\n".join(out)
    finally:
        await client.close()


async def build_priced_basket(inventory: dict[str, int], scope: str = "весь план") -> str | None:
    """Текст корзины с ценами или None, если ВкусВилл недоступен/отключён/ничего не нашлось."""
    if not config.VKUSVILL_PRICES_ENABLED or not inventory:
        return None
    try:
        return await asyncio.wait_for(_run(inventory, scope), timeout=config.STORE_PRICES_TIMEOUT)
    except Exception as e:
        logger.warning("Корзина ВкусВилл не собрана: %s", e)
        return None
