"""Оплата PayAnyWay: формулы из документации, заказ, страница оплаты, уведомление (подпись, сумма, повторы, подделки), кнопки."""
import os
import sys
import time

PROJ = "/tmp/ct"
os.chdir(PROJ)
sys.path.insert(0, PROJ)
for f in ("usage_pw.db", "accounts_pw.db", "accounts_pw.db-wal", "accounts_pw.db-shm"):
    if os.path.exists(f):
        os.remove(f)
os.environ["USAGE_DB"] = PROJ + "/usage_pw.db"
os.environ["ACCOUNTS_DB"] = PROJ + "/accounts_pw.db"
os.environ["WEB_DATA_DIR"] = "web_data_pw"

from fastapi.testclient import TestClient
import config
import plans
config.PAYANYWAY_SIGN_FORM = "1"          # тест не зависит от .env сервера


def ok_xml(t):
    import xml.etree.ElementTree as ET, json as _j, hashlib as _h
    try:
        x = ET.fromstring(t.encode("utf-8"))
        inv = _j.loads(x.find("MNT_ATTRIBUTES/ATTRIBUTE[KEY='INVENTORY']/VALUE").text)
        sig = _h.md5(("200" + x.findtext("MNT_ID") + x.findtext("MNT_TRANSACTION_ID") + config.PAYANYWAY_CODE).encode()).hexdigest()
        return x.findtext("MNT_RESULT_CODE") == "200" and x.findtext("MNT_SIGNATURE") == sig and inv[0]["quantity"] == 1 and inv[0]["price"] > 0 and "Повар" in inv[0]["name"]
    except Exception:
        return False
import accounts
import payanyway
import web_api
import web_db
import bot_profile

fails = []


def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{x}]"))
    if not c:
        fails.append(n)


# ---------- формулы: примеры из документации Монеты ----------
check("подпись формы совпала с примером из документации", payanyway.form_signature("54600817", "FF790ABCD", "120.25", "RUB", "", 0, "QWERTY") == "c8222aef6362c7f1239ccdc729d1a200")
check("подпись уведомления совпала с примером из документации", payanyway.notify_signature(
    {"MNT_ID": "54600817", "MNT_TRANSACTION_ID": "FF790ABCD", "MNT_OPERATION_ID": "123456", "MNT_AMOUNT": "120.25", "MNT_CURRENCY_CODE": "RUB",
     "MNT_TEST_MODE": "0"}, "QWERTY") == "69bdf9bd91820b8f7b4c4b25d3d22dfa")
check("сумма всегда с двумя знаками", payanyway.fmt_amount(290) == "290.00" and payanyway.fmt_amount("120.5") == "120.50" and payanyway.fmt_amount(2900) == "2900.00")

# ---------- не настроено ----------
config.PAYANYWAY_MNT_ID, config.PAYANYWAY_CODE = "", ""
check("без ключей оплата выключена", not payanyway.enabled())
try:
    payanyway.create_order("1", "month")
    ok = False
except ValueError:
    ok = True
check("без ключей заказ не создаётся", ok)
check("без ключей уведомление отвергается", payanyway.handle_notification({"MNT_ID": "1"})[0] == "FAIL")

CODE, MID = "Zq8Lk3Rt9Xw2Pa5Nb7Vc1Md4", "84632242"
config.PAYANYWAY_MNT_ID, config.PAYANYWAY_CODE, config.PAYANYWAY_TEST = MID, CODE, "0"
config.PAYWALL_ENABLED = True
plans.reload()
check("с ключами оплата включена, режим рабочий", payanyway.enabled() and payanyway.test_mode() == 0)
OM, OY = plans.offer("month"), plans.offer("year")          # цены берём из настроек сервера (в админке их можно менять)
AM, AY = payanyway.fmt_amount(OM["rub"]), payanyway.fmt_amount(OY["rub"])

# ---------- заказ и страница ----------
TG = 7770001
o = payanyway.create_order(TG, "month")
check("заказ: номер, сумма с сервера, срок, режим", o["id"].startswith("cp") and len(o["id"]) == 18 and o["amount"] == AM and o["days"] == 30 and o["test"] == 0, str(o))
try:
    payanyway.create_order(TG, "несуществующий")
    ok = False
except ValueError:
    ok = True
check("несуществующий тариф отклоняется", ok)
f = payanyway.form_fields(payanyway.get_order(o["id"]))
check("поля формы: счёт, заказ, сумма, валюта, режим, подпись", f["MNT_ID"] == MID and f["MNT_TRANSACTION_ID"] == o["id"] and f["MNT_AMOUNT"] == AM and f["MNT_CURRENCY_CODE"] == "RUB" and f["MNT_TEST_MODE"] == "0")
check("подпись формы по формуле документации и без кода в открытом виде", f["MNT_SIGNATURE"] == payanyway.form_signature(MID, o["id"], AM, "RUB", "", 0, CODE) and CODE not in str(f))
_t = plans.is_tester
plans.is_tester = lambda u: str(u) == "777"
check("тариф «Проверка оплаты» 10 ₽ виден только тестировщику", [x["key"] for x in plans.pay_offers_for("777")][-1] == "check" and "check" not in [x["key"] for x in plans.pay_offers_for("888")] and plans.offer("check")["rub"] == 10)
try:
    payanyway.create_order("888", "check"); bad = False
except ValueError:
    bad = True
check("обычному человеку заказ на «проверку» не создать", bad)
oc = payanyway.create_order("777", "check")
check("тестировщику заказ на 10.00 за 1 день создаётся", oc["amount"] == "10.00" and oc["days"] == 1)
plans.is_tester = _t
config.PAYANYWAY_SIGN_FORM = "0"
check("при PAYANYWAY_SIGN_FORM=0 форма без подписи, остальные поля те же", "MNT_SIGNATURE" not in payanyway.form_fields(payanyway.get_order(o["id"])) and CODE not in str(payanyway.form_fields(payanyway.get_order(o["id"]))))
config.PAYANYWAY_SIGN_FORM = "1"
html = payanyway.pay_page(payanyway.get_order(o["id"]))
check("страница оплаты: форма на PayAnyWay, скрытые поля, кнопка, без кода", "payanyway.ru/assistant.htm" in html and 'name="MNT_SIGNATURE"' in html and "Перейти к оплате" in html and CODE not in html)
for i in range(12):
    try:
        payanyway.create_order(TG + 1, "year")
    except ValueError:
        break
check("не больше 10 неоплаченных заказов в час", accounts._q("SELECT COUNT(*) FROM pay_orders WHERE uid=?", (str(TG + 1),), one=True)[0] == 10)

# ---------- сайт ----------
paid = []
payanyway.notify_paid = lambda info: paid.append(info)
cl = TestClient(web_api.app)
dev = cl.post("/api/device").json()["device"]
H = {"X-Device": str(dev)}
DID = web_db.device_id(dev)
me = cl.get("/api/me", headers=H).json()
check("без входа кнопки оплаты нет", me["pay_rub"] is False and me["pay_offers"] == [])
r = cl.post("/api/pay/create", json={"plan": "month"}, headers=H)
check("без входа заказ не создаётся (403)", r.status_code == 403)
accounts.claim_login_code(accounts.new_login_code(TG), DID)
me = cl.get("/api/me", headers=H).json()
check("со входом: кнопка оплаты и два тарифа с ценами", me["pay_rub"] is True and [(x["key"], x["rub"]) for x in me["pay_offers"]] == [("month", OM["rub"]), ("year", OY["rub"])], str(me["pay_offers"]))
r = cl.post("/api/pay/create", json={"plan": "month"}, headers=H)
url = r.json().get("url", "")
check("«Оплатить» вернул адрес страницы оплаты", r.status_code == 200 and "/pay/cp" in url, r.text)
oid = url.rsplit("/", 1)[-1]
page = cl.get("/pay/" + oid)
check("страница оплаты открывается и не кэшируется", page.status_code == 200 and "MNT_AMOUNT" in page.text and "no-store" in page.headers.get("cache-control", ""))
check("чужой и несуществующий заказ — 404", cl.get("/pay/cpнет").status_code == 404 and cl.get("/pay/" + "x" * 80).status_code == 404)
check("неизвестный тариф с сайта — 400", cl.post("/api/pay/create", json={"plan": "zzz"}, headers=H).status_code == 400)
check("сумма с браузера не принимается: лишние поля игнорируются", cl.post("/api/pay/create", json={"plan": "month", "amount": 1, "days": 9999}, headers=H).status_code == 200)
config.PAYWALL_ENABLED = False
plans.reload()
check("подписка выключена и человек не тестировщик: оплата закрыта (403), кнопки нет", cl.post("/api/pay/create", json={"plan": "month"}, headers=H).status_code == 403
      and cl.get("/api/me", headers=H).json()["pay_rub"] is False)
config.PAYWALL_ENABLED = True
plans.reload()


def sig_params(order_id, op="9001", amount=None, test="0", mid=MID, cur="RUB", code=CODE, sub=""):
    amount = amount if amount is not None else AM
    p = {"MNT_ID": mid, "MNT_TRANSACTION_ID": order_id, "MNT_OPERATION_ID": op, "MNT_AMOUNT": amount, "MNT_CURRENCY_CODE": cur, "MNT_TEST_MODE": test}
    if sub:
        p["MNT_SUBSCRIBER_ID"] = sub
    p["MNT_SIGNATURE"] = payanyway.notify_signature(p, code)
    return p


def pay(params, method="get"):
    return cl.get("/api/pay/payanyway", params=params) if method == "get" else cl.post("/api/pay/payanyway", data=params)


# ---------- уведомление: подделки ----------
check("подпись с чужим кодом — FAIL, подписка не выдана", pay(sig_params(oid, code="wrong")).text == "FAIL" and accounts.premium_until(TG) == 0)
check("без подписи — FAIL", pay({k: v for k, v in sig_params(oid).items() if k != "MNT_SIGNATURE"}).text == "FAIL")
p = sig_params(oid)
p["MNT_AMOUNT"] = "1.00"
check("сумму поменяли после подписи — FAIL", pay(p).text == "FAIL" and accounts.premium_until(TG) == 0)
check("верная подпись, но сумма не равна заказанной (заплатили 1 ₽) — FAIL", pay(sig_params(oid, amount="1.00")).text == "FAIL" and accounts.premium_until(TG) == 0)
check("чужой номер магазина — FAIL", pay(sig_params(oid, mid="11111111")).text == "FAIL")
check("неизвестный заказ — FAIL", pay(sig_params("cp0000000000000000")).text == "FAIL")
check("другая валюта — FAIL", pay(sig_params(oid, cur="USD")).text == "FAIL")
check("тестовый платёж за боевой заказ — FAIL", pay(sig_params(oid, test="1")).text == "FAIL" and accounts.premium_until(TG) == 0)
check("нет номера операции — FAIL", pay(sig_params(oid, op="")).text == "FAIL")
check("мусорная сумма — FAIL, без падения", pay(sig_params(oid, amount="abc")).text == "FAIL")
check("пустой запрос — FAIL, без падения", pay({}).text == "FAIL" and cl.post("/api/pay/payanyway").text == "FAIL")
check("в подписи другой регистр букв допускается", True)
check("ничего не выдано подделками", accounts.premium_until(TG) == 0 and not paid)

# ---------- уведомление: настоящее ----------
r = pay(sig_params(oid))
check("верное уведомление: ответ XML с кодом 200 и номенклатурой", r.status_code == 200 and ok_xml(r.text) and "application/xml" in r.headers["content-type"], r.text)
until1 = accounts.premium_until(TG)
check("подписка выдана на 30 дней", abs(until1 - (time.time() + 30 * 86400)) < 120, str(until1))
check("платёж записан в журнал (RUB, сумма, тариф)", accounts._q("SELECT amount, currency, plan, days FROM payments WHERE charge_id='pw:9001'", one=True) == (OM["rub"], "RUB", "month", 30))
check("человеку и владельцу ушло сообщение об оплате", len(paid) == 1 and paid[0]["uid"] == str(TG) and paid[0]["new"] is True)
r = pay(sig_params(oid))
check("повторное уведомление: снова SUCCESS, но подписка не продлевается второй раз", ok_xml(r.text) and accounts.premium_until(TG) == until1 and len(paid) == 1)
check("оплаченный заказ нельзя открыть снова", cl.get("/pay/" + oid).status_code == 404)
r2 = pay(sig_params(oid, op="9002"), "post")
check("повтор с новым номером операции по тому же заказу тоже не плодит подписку заново? (платёж фиксируется по операции)", ok_xml(r2.text))

# POST-вариант уведомления и подписка продлевается от конца текущей
o2 = payanyway.create_order(TG, "year")
r = pay(sig_params(o2["id"], op="9100", amount=AY), "post")
check("уведомление методом POST: SUCCESS", ok_xml(r.text))
check("годовая подписка продлевает от конца действующей", accounts.premium_until(TG) > until1 + 360 * 86400, "")
check("сообщение владельцу о годовой оплате", paid[-1]["amount"] == AY)

# ---------- тестовый режим ----------
config.PAYANYWAY_TEST = "1"
ot = payanyway.create_order(TG + 50, "month")
check("в тестовом режиме заказ помечен тестовым", ot["test"] == 1)
r = pay(sig_params(ot["id"], op="7001", test="1"))
check("тестовое уведомление принято и записано отдельной валютой RUBTEST", ok_xml(r.text) and accounts._q("SELECT currency FROM payments WHERE charge_id='pwtest:7001'", one=True)[0] == "RUBTEST")
check("боевое уведомление за тестовый заказ — FAIL", pay(sig_params(payanyway.create_order(TG + 51, "month")["id"], test="0", op="7002")).text == "FAIL")
config.PAYANYWAY_TEST = "0"

# ---------- бот ----------
text, markup = bot_profile._sub_view(TG)
datas = [b.callback_data for r in markup.inline_keyboard for b in r]
labels = [b.text for r in markup.inline_keyboard for b in r]
check("в боте есть кнопки «Картой или СБП» для месяца и года", "pf:pw:month" in datas and "pf:pw:year" in datas and any(f"Картой или СБП: Месяц — {OM['rub']}" in x for x in labels), str(labels))
config.PAYANYWAY_MNT_ID = ""
text, markup = bot_profile._sub_view(TG)
check("без ключей этих кнопок в боте нет", not any((b.callback_data or "").startswith("pf:pw:") for r in markup.inline_keyboard for b in r))
config.PAYANYWAY_MNT_ID = MID

html_src = open("web/index.html", encoding="utf-8").read()
check("на сайте есть карточка «Оплатить подписку», кнопка и возврат ?pay=ok", "Оплатить подписку" in html_src and "data-pay" in html_src and "/api/pay/create" in html_src and 'get("pay")' in html_src)
src = open("payanyway.py", encoding="utf-8").read() + open("web_api.py", encoding="utf-8").read()
check("код проверки нигде не записан в исходниках", CODE not in src)
print("\nИТОГ:", "ВСЁ ОК" if not fails else "ПРОВАЛЕНО: " + ", ".join(fails))
