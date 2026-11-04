"""
Оплата картой и СБП через PayAnyWay (Монета, платёжная форма MONETA.Assistant).

Как это работает:
1. Человек нажимает «Оплатить» (сайт или бот): создаётся заказ (`pay_orders`), открывается страница /pay/<заказ>, она сама отправляет
   подписанную форму на PayAnyWay. Суммы и сроки берутся с сервера (plans.offers), из браузера ничего не принимается.
2. После оплаты PayAnyWay присылает уведомление на Pay URL (/api/pay/payanyway). Мы проверяем подпись (код проверки целостности лежит
   только в .env), номер магазина, заказ, сумму, валюту и режим; зачисляем подписку один раз (повторные уведомления безопасны) и отвечаем SUCCESS.

Формулы взяты из документации Монеты (docs.moneta.ru, «Запрос на оплату через MONETA.Assistant» и «Уведомление о проведенной оплате»):
  подпись формы   = MD5(MNT_ID + MNT_TRANSACTION_ID + MNT_AMOUNT + MNT_CURRENCY_CODE + MNT_SUBSCRIBER_ID + MNT_TEST_MODE + КОД)
  подпись отчёта  = MD5(MNT_ID + MNT_TRANSACTION_ID + MNT_OPERATION_ID + MNT_AMOUNT + MNT_CURRENCY_CODE + MNT_SUBSCRIBER_ID + MNT_TEST_MODE + КОД)
Сумма всегда с двумя знаками (120.00); пустой MNT_SUBSCRIBER_ID в подписи пустая строка.
"""
import hashlib
import hmac
import logging
import secrets
import time
from decimal import Decimal, InvalidOperation
from html import escape

import accounts
import config
import plans

logger = logging.getLogger(__name__)
ORDER_TTL = 24 * 3600
MAX_NEW_PER_HOUR = 10


def enabled() -> bool:
    return bool(config.PAYANYWAY_MNT_ID and config.PAYANYWAY_CODE)


def test_mode() -> int:
    return 1 if str(config.PAYANYWAY_TEST).strip().lower() in ("1", "true", "yes", "on") else 0


def _md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def fmt_amount(rub) -> str:
    return f"{Decimal(str(rub)):.2f}"


def form_signature(mnt_id: str, tx: str, amount: str, currency: str, subscriber: str, test: int, code: str) -> str:
    return _md5(f"{mnt_id}{tx}{amount}{currency}{subscriber}{test}{code}")


def notify_signature(p: dict, code: str) -> str:
    g = lambda k: str(p.get(k, "") or "")
    return _md5(g("MNT_ID") + g("MNT_TRANSACTION_ID") + g("MNT_OPERATION_ID") + g("MNT_AMOUNT") + g("MNT_CURRENCY_CODE")
                + g("MNT_SUBSCRIBER_ID") + g("MNT_TEST_MODE") + code)


def allowed_for(uid) -> bool:
    """Кнопка оплаты показывается, когда оплата настроена и подписка включена (или человек в списке тестировщиков)."""
    return enabled() and (plans.paywall() or plans.is_tester(uid))


def create_order(uid, plan_key: str) -> dict:
    """Новый заказ на оплату. ValueError с понятным текстом, если нельзя."""
    if not enabled():
        raise ValueError("Оплата картой ещё не подключена")
    o = plans.offer(plan_key)
    if not o or not o["rub"] or o["rub"] <= 0:
        raise ValueError("Такого тарифа нет")
    n = accounts._q("SELECT COUNT(*) FROM pay_orders WHERE uid=? AND status='new' AND ts > ?", (str(uid), time.time() - 3600), one=True)[0]
    if n >= MAX_NEW_PER_HOUR:
        raise ValueError("Слишком много неоплаченных заказов за час, подождите немного")
    oid = "cp" + secrets.token_hex(8)
    amount = fmt_amount(o["rub"])
    accounts._q("INSERT INTO pay_orders(id, uid, plan, days, amount, test, ts, status) VALUES(?,?,?,?,?,?,?, 'new')",
                (oid, str(uid), o["key"], int(o["days"]), amount, test_mode(), time.time()))
    return {"id": oid, "uid": str(uid), "plan": o["key"], "days": o["days"], "amount": amount, "title": o["title"], "test": test_mode()}


def get_order(oid: str) -> dict | None:
    r = accounts._q("SELECT id, uid, plan, days, amount, test, ts, status, op FROM pay_orders WHERE id=?", (str(oid)[:40],), one=True)
    if not r:
        return None
    return dict(zip(("id", "uid", "plan", "days", "amount", "test", "ts", "status", "op"), r))


def form_fields(order: dict) -> dict:
    o = plans.offer(order["plan"]) or {"title": order["plan"]}
    sig = form_signature(str(config.PAYANYWAY_MNT_ID), order["id"], order["amount"], "RUB", "", order["test"], config.PAYANYWAY_CODE)
    fields = {"MNT_ID": str(config.PAYANYWAY_MNT_ID), "MNT_TRANSACTION_ID": order["id"], "MNT_CURRENCY_CODE": "RUB", "MNT_AMOUNT": order["amount"],
              "MNT_TEST_MODE": str(order["test"]), "MNT_DESCRIPTION": f"Кухонный помощник: подписка «{plans.PREMIUM_NAME}», {o['title'].lower()}",
              "moneta.locale": "ru"}
    if str(config.PAYANYWAY_SIGN_FORM).strip().lower() not in ("0", "false", "no", "off"):
        fields["MNT_SIGNATURE"] = sig
    return fields


def pay_page(order: dict) -> str:
    """Страница, которая сама отправляет подписанную форму в PayAnyWay (есть и кнопка на случай, если скрипты отключены)."""
    fields = "".join(f'<input type="hidden" name="{escape(k)}" value="{escape(v, quote=True)}">' for k, v in form_fields(order).items())
    return ("<!doctype html><html lang=ru><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            "<meta name=robots content=noindex><title>Оплата</title>"
            "<body style='font-family:system-ui,sans-serif;max-width:480px;margin:40px auto;padding:0 16px;text-align:center'>"
            f"<h1 style='font-size:22px'>Переходим к оплате</h1><p>Подписка «{escape(plans.PREMIUM_NAME)}», {escape(str(order['amount']))} ₽.</p>"
            f"<form method=post action='{escape(config.PAYANYWAY_URL, quote=True)}' id=f>{fields}"
            "<button type=submit style='font-size:18px;padding:12px 24px;border-radius:12px;border:0;background:#2d6a4f;color:#fff'>Перейти к оплате</button></form>"
            "<script>setTimeout(function(){document.getElementById('f').submit();},300);</script></body></html>")


def handle_notification(p: dict) -> tuple[str, dict | None]:
    """Уведомление об оплате. -> ("SUCCESS" | "FAIL", сведения для сообщений о новой оплате или None)."""
    try:
        if not enabled():
            return "FAIL", None
        g = lambda k: str(p.get(k, "") or "")
        if g("MNT_ID") != str(config.PAYANYWAY_MNT_ID):
            return "FAIL", None
        if not hmac.compare_digest(notify_signature(p, config.PAYANYWAY_CODE), g("MNT_SIGNATURE").strip().lower()):
            logger.warning("PayAnyWay: подпись уведомления не сошлась (заказ %s)", g("MNT_TRANSACTION_ID")[:40])
            return "FAIL", None
        order = get_order(g("MNT_TRANSACTION_ID"))
        op = g("MNT_OPERATION_ID")
        if not order or not op or len(op) > 64:
            return "FAIL", None
        try:
            if Decimal(g("MNT_AMOUNT")) != Decimal(order["amount"]):
                logger.warning("PayAnyWay: сумма %s не равна заказанной %s (заказ %s)", g("MNT_AMOUNT"), order["amount"], order["id"])
                return "FAIL", None
        except InvalidOperation:
            return "FAIL", None
        if g("MNT_CURRENCY_CODE") != "RUB" or g("MNT_TEST_MODE") not in ("0", "1") or int(g("MNT_TEST_MODE")) != int(order["test"]):
            return "FAIL", None
        rub = int(Decimal(order["amount"]))
        charge = ("pwtest:" if order["test"] else "pw:") + op
        until = accounts.apply_payment(charge, order["uid"], rub, "RUBTEST" if order["test"] else "RUB", int(order["days"]), order["plan"])
        accounts._q("UPDATE pay_orders SET status='paid', op=? WHERE id=?", (op, order["id"]))
        info = {"uid": order["uid"], "plan": order["plan"], "amount": order["amount"], "test": order["test"],
                "until": until, "new": until is not None}
        return "SUCCESS", info
    except Exception:  # noqa: BLE001 — не знаем наверняка: пусть Монета повторит уведомление
        logger.exception("PayAnyWay: ошибка разбора уведомления")
        return "FAIL", None


def notify_paid(info: dict) -> None:
    """Сообщить человеку (если это аккаунт Telegram) и владельцу о новой оплате. Сбой сообщений на оплату не влияет."""
    import httpx
    import feedback
    try:
        if info["until"] and config.BOT_TOKEN and str(info["uid"]).isdigit() and int(info["uid"]) < accounts.VK_OFFSET:
            text = f"🎉 Спасибо за оплату! Подписка действует до {time.strftime('%d.%m.%Y', time.localtime(info['until']))}."
            httpx.post(f"https://api.telegram.org/bot{config.BOT_TOKEN}/sendMessage", json={"chat_id": int(info["uid"]), "text": text}, timeout=5)
    except Exception as e:  # noqa: BLE001
        logger.warning("Сообщение об оплате человеку не ушло: %s", e)
    feedback.notify_sync(f"💰 Оплата PayAnyWay{' (ТЕСТ)' if info['test'] else ''}: {info['amount']} ₽, тариф {info['plan']}, пользователь {info['uid']}")
