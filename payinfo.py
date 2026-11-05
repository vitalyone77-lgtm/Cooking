"""
Открытая страница /oplata: что продаём, цены в рублях, порядок оплаты и возврата, реквизиты продавца. Её требует платёжный сервис
(оферта Self.PayAnyWay, п. 5.2.1.12), поэтому она видна без входа. Реквизиты лежат в .env на сервере (SELLER_*), а не в коде.
"""
from html import escape

import config
import plans

REFUND_DAYS = 3


def seller() -> dict:
    return {"name": config.SELLER_NAME, "inn": config.SELLER_INN, "phone": config.SELLER_PHONE, "email": config.SELLER_EMAIL, "address": config.SELLER_ADDRESS}


def url() -> str:
    return (config.WEB_APP_URL or "https://menu.ymayaka.ru").rstrip("/") + "/oplata"


def page() -> str:
    s = seller()
    prices = "".join(f"<li>{escape(o['title'])}: {int(o['rub'])} ₽</li>" for o in plans.offers() if o["rub"])
    contact = " или ".join(x for x in (escape(s["email"]), escape(s["phone"])) if x) or "раздел «Написать разработчику» в справке"
    rows = [("Самозанятый", s["name"]), ("ИНН", s["inn"]), ("Телефон", s["phone"]), ("Эл. почта", s["email"]), ("Адрес", s["address"])]
    req = "".join(f"<tr><th>{t}</th><td>{escape(v)}</td></tr>" for t, v in rows if v)
    name = escape(plans.PREMIUM_NAME)
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Оплата и возврат</title><meta name="description" content="Подписка «{name}» в Кухонном помощнике: цены, порядок оплаты и возврата, реквизиты продавца."><style>
:root{{--bg:#FBF7F2;--surface:#fff;--text:#2B2622;--muted:#6B625A;--line:#E7DED3;--primary:#2F6B4F}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1A1714;--surface:#25211D;--text:#F1EAE2;--muted:#B5AA9E;--line:#3A342E;--primary:#7CC5A0}}}}
*{{box-sizing:border-box}}body{{margin:0 auto;max-width:640px;padding:16px;background:var(--bg);color:var(--text);font:17px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif}}
h1{{font-size:26px;margin:8px 0 12px}}h2{{font-size:19px;margin:22px 0 6px}}a{{color:var(--primary)}}ul,ol{{padding-left:22px;margin:6px 0}}p{{margin:6px 0}}
table{{border-collapse:collapse;width:100%;background:var(--surface)}}th,td{{border:1px solid var(--line);padding:8px 10px;text-align:left;vertical-align:top}}th{{font-weight:600;width:34%}}
</style></head><body><h1>Оплата и возврат</h1>
<h2>Что продаётся</h2><p>Подписка «{name}» в сервисе «Кухонный помощник» (сайт и бот в Telegram): больше рецептов в день, меню и планы питания, замены продуктов, корзина с ценами и PDF. Это цифровой доступ, товар не доставляется.</p>
<h2>Цены</h2><ul>{prices}</ul>
<h2>Как оформить и оплатить</h2><ol><li>Войдите в профиль на сайте или откройте бота.</li><li>В настройках откройте «Подписка и оплата» и выберите срок.</li><li>Оплатите банковской картой или через СБП на защищённой странице платёжного сервиса PayAnyWay. Данные карты мы не получаем и не храним.</li></ol>
<h2>Сроки</h2><p>Доступ включается автоматически, обычно в течение нескольких минут после оплаты. Если этого не произошло, напишите нам: {contact}. Чек формируется автоматически.</p>
<h2>Возврат и отказ от услуги</h2><p>Отказаться от подписки и вернуть деньги можно в течение {REFUND_DAYS} дней с даты оплаты. Напишите на {contact}, укажите дату и сумму платежа. Возвращается оплаченная сумма за вычетом налога и расходов платёжного и других сервисов, связанных с этой оплатой.</p>
<p>Деньги приходят тем же способом, которым была оплата. Срок зависит от банка, обычно до 10 рабочих дней. Эти условия не ограничивают права покупателя по Закону «О защите прав потребителей».</p>
<h2>Продавец</h2><table>{req}</table>
<p style="margin-top:20px"><a href="/">Открыть Кухонного помощника</a></p></body></html>"""
