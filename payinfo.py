"""
Открытая страница /oplata: только то, что требует платёжный сервис: услуга и цены в рублях,
ФИО и ИНН, контакт, порядок и сроки, оплата, доставка, возврат. Видна без входа. Реквизиты лежат в .env на сервере (SELLER_*), а не в коде.
"""
from html import escape

import config
import plans


TARIFF_ROWS = (("Рецепты", "recipe"), ("Меню на день", "daymenu"), ("План на 3 дня", "week"), ("Замена продуктов", "missing"), ("Рецепт по фото", "photo"), ("Проверка состава", "label"))
TARIFF_FEATURES = ("Исключение продуктов и полка", "PDF", "Корзина ВкусВилла с ценами", "Сайт без рекламы")


def seller() -> dict:
    return {"name": config.SELLER_NAME, "inn": config.SELLER_INN, "phone": config.SELLER_PHONE, "email": config.SELLER_EMAIL}


def url() -> str:
    return (config.WEB_APP_URL or "https://menu.ymayaka.ru").rstrip("/") + "/oplata"


def page() -> str:
    s = seller()
    prices = "".join(f"<li>{escape(o['title'])}: {int(o['rub'])} ₽</li>" for o in plans.offers() if o["rub"])
    contact = " или ".join(x for x in (escape(s["email"]), escape(s["phone"])) if x) or "раздел «Написать разработчику» в справке"
    rows = [("Самозанятый", s["name"]), ("ИНН", s["inn"]), ("Телефон", s["phone"]), ("Эл. почта", s["email"])]
    req = "".join(f"<tr><th>{t}</th><td>{escape(v)}</td></tr>" for t, v in rows if v)
    name = escape(plans.PREMIUM_NAME)
    free, prem = plans.limits_of("free"), plans.limits_of("premium")
    lim = lambda v: "нет" if not v or not v[0] else (f"{v[0]} в день" if v[1] == 1 else f"{v[0]} за {v[1]} дн.")
    rows2 = "".join(f"<tr><th>{t}</th><td>{lim(free.get(k))}</td><td>{lim(prem.get(k))}</td></tr>" for t, k in TARIFF_ROWS)
    rows2 += "".join(f"<tr><th>{t}</th><td>нет</td><td>да</td></tr>" for t in TARIFF_FEATURES)
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Оплата и возврат</title><style>
:root{{--bg:#FBF7F2;--surface:#fff;--text:#2B2622;--line:#E7DED3;--primary:#2F6B4F}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1A1714;--surface:#25211D;--text:#F1EAE2;--line:#3A342E;--primary:#7CC5A0}}}}
*{{box-sizing:border-box}}body{{margin:0 auto;max-width:640px;padding:16px;background:var(--bg);color:var(--text);font:17px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif}}
h1{{font-size:26px;margin:8px 0 12px}}h2{{font-size:19px;margin:20px 0 6px}}a{{color:var(--primary)}}ul{{padding-left:22px;margin:6px 0}}p{{margin:6px 0}}
table{{border-collapse:collapse;width:100%;background:var(--surface)}}th,td{{border:1px solid var(--line);padding:8px 10px;text-align:left}}th{{font-weight:600;width:34%}}
</style></head><body><h1>Оплата и возврат</h1>
<h2>Услуга и цены</h2><p>Подписка «{name}» в «Кухонном помощнике»: цифровой доступ к расширенным функциям сайта и бота на выбранный срок.</p><ul>{prices}</ul>
<h2>Что входит</h2><table><tr><th></th><th>Бесплатно</th><th>«{name}»</th></tr>{rows2}</table>
<h2>Оплата и получение</h2><p>Оформляется в профиле, в разделе «Подписка и оплата». Оплата картой или через СБП на защищённой странице платёжного сервиса. Доступ включается автоматически через несколько минут, доставки нет.</p>
<h2>Отказ и возврат</h2><p>Автопродления нет: новых списаний не будет. Отказаться можно в любой момент, написав на {contact}. Вернём деньги за неиспользованный срок за вычетом фактических расходов (налог, комиссия сервиса).</p>
<h2>Продавец</h2><table>{req}</table>
<p style="margin-top:20px"><a href="/">Открыть Кухонного помощника</a></p></body></html>"""
