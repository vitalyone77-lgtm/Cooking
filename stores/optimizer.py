"""
Подбор упаковок: как купить нужное количество продукта дешевле всего, но чтобы его
хватило (минимально необходимое или минимально превышающее).

Задача — "покрытие с минимальной стоимостью" (вариант рюкзака): есть несколько товаров разных
фасовок и цен, нужно набрать не меньше need граммов. Решаем динамикой по шагу 10 г — точно,
а не жадным перебором, и при равной цене выбираем вариант с меньшим перебором по граммам.
"""
import math

from .base import Offer, CartLine

STEP_G = 10                    # шаг динамики (граммы)
MAX_PACKS_PER_LINE = 40        # ограничение API ВкусВилла на q
MAX_OVERSHOOT = 2.5            # не берём упаковки крупнее need*2.5, если есть меньше и подходящие


def choose_packs(need_name: str, need_g: float, offers: list[Offer]) -> list[CartLine]:
    """
    Возвращает набор строк корзины (может быть несколько разных товаров под один продукт)
    либо пустой список, если ни у одного предложения не известен размер упаковки.
    """
    usable = [o for o in offers if o.pack_g and o.pack_g > 0 and o.price > 0]
    if not usable or need_g <= 0:
        return []

    # Слишком крупные упаковки (например, 5 кг риса на 200 г) отсекаем, если есть варианты поменьше.
    reasonable = [o for o in usable if o.pack_g <= need_g * MAX_OVERSHOOT]
    if reasonable:
        usable = reasonable

    target = max(1, math.ceil(need_g / STEP_G))
    sizes = [max(1, math.ceil(o.pack_g / STEP_G)) for o in usable]

    # dp[j] = (мин. стоимость, суммарные граммы) чтобы покрыть >= j шагов
    INF = (float("inf"), float("inf"))
    dp: list[tuple[float, float]] = [INF] * (target + 1)
    choice: list[int] = [-1] * (target + 1)
    dp[0] = (0.0, 0.0)
    for j in range(1, target + 1):
        for idx, (offer, size) in enumerate(zip(usable, sizes)):
            prev = max(0, j - size)
            base_cost, base_g = dp[prev]
            cand = (base_cost + offer.price, base_g + offer.pack_g)
            if cand < dp[j]:
                dp[j] = cand
                choice[j] = idx

    if dp[target] == INF:
        return []

    counts: dict[int, int] = {}
    j = target
    while j > 0:
        idx = choice[j]
        counts[idx] = counts.get(idx, 0) + 1
        j = max(0, j - sizes[idx])

    lines = []
    for idx, count in counts.items():
        lines.append(CartLine(need_name=need_name, need_g=need_g, offer=usable[idx],
                              count=min(count, MAX_PACKS_PER_LINE)))
    return lines
