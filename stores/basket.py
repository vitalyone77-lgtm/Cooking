"""
Корзина с ценами из ВкусВилла: для каждого продукта плана находим товары, подбираем упаковки
(минимально достаточно / минимальный перебор, см. optimizer.py), считаем стоимость и собираем
ссылку-корзину. Если сервер недоступен — возвращаем None, а вызывающий код показывает только
обычные ссылки на поиск.
"""
import asyncio
import logging
import re

import config
from .base import Offer, CartLine
from .optimizer import choose_packs
from .vkusvill import VkusvillClient

logger = logging.getLogger(__name__)

# Счётные продукты: количество в плане — штуки, а не граммы
_PIECE_WORDS = ("яйц",)
_PIECES_RE = re.compile(r"(\d+)\s*(?:шт|штук)\b", re.IGNORECASE)


def _is_piece_item(name: str) -> bool:
    n = name.lower()
    return any(w in n for w in _PIECE_WORDS)


def _relevant(term: str, offers: list[Offer]) -> list[Offer]:
    """Оставляем товары, в названии которых есть основа слова из запроса (по первым 5 буквам)."""
    stems = [w[:5] for w in re.findall(r"[а-яёa-z]{4,}", term.lower())]
    if not stems:
        return offers
    return [o for o in offers if any(s in o.name.lower() for s in stems)]


def _prepare_offers(term: str, offers: list[Offer]) -> list[Offer]:
    if _is_piece_item(term):
        res = []
        for o in offers:
            m = _PIECES_RE.search(o.name)
            if m:
                o.pack_g = float(m.group(1))   # для яиц pack_g = число штук
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
    offers = _prepare_offers(term, _relevant(term, offers))
    return term, choose_packs(term, need, offers)


def _fmt_g(v: float) -> str:
    return f"{v / 1000:.1f} кг".replace(".0", "") if v >= 1000 else f"{int(v)} г"


async def _run(inventory: dict[str, int]) -> str | None:
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
                parts.append(f"{cl.offer.name} × {cl.count} = {cl.total_price:.0f} ₽")
            lines.append(f"• {term} ({need_txt}) → " + "; ".join(parts))

        if not lines:
            return None

        out = ["🥬 *Корзина ВкусВилл* (минимум, которого хватит на весь план):\n"]
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


async def build_priced_basket(inventory: dict[str, int]) -> str | None:
    """Текст корзины с ценами или None, если ВкусВилл недоступен/отключён/ничего не нашлось."""
    if not config.VKUSVILL_PRICES_ENABLED or not inventory:
        return None
    try:
        return await asyncio.wait_for(_run(inventory), timeout=config.STORE_PRICES_TIMEOUT)
    except Exception as e:
        logger.warning("Корзина ВкусВилл не собрана: %s", e)
        return None
