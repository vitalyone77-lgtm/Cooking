"""Админка: вход владельца и партнёра, права, статистика, тарифы из админки доходят до лимитов."""
import os
import sys
import time

PROJ = "/tmp/ct"
os.chdir(PROJ)
sys.path.insert(0, PROJ)
for f in ("usage_adm.db", "accounts_adm.db"):
    if os.path.exists(f):
        os.remove(f)
os.environ["USAGE_DB"] = PROJ + "/usage_adm.db"
os.environ["ACCOUNTS_DB"] = PROJ + "/accounts_adm.db"
os.environ["WEB_DATA_DIR"] = "web_data_adm"

from fastapi.testclient import TestClient
import config
config.LIMITS_FREE = "recipe=3/1,daymenu=1/7,week=0/3,missing=2/1,photo=1/1"   # не зависим от .env сервера
config.ADMIN_PASSWORD, config.PARTNER_PASSWORD, config.WEB_SECRET = "owner-pass-1", "partner-pass-1", "secret-x"
import accounts
import plans
import usage
import web_api

fails = []


def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{x}]"))
    if not c:
        fails.append(n)


with TestClient(web_api.app) as cl:
    check("без входа — 401", cl.get("/admin/api/stats").status_code == 401)
    check("неверный пароль — 401", cl.post("/admin/login", json={"password": "x"}).status_code == 401)
    r = cl.post("/admin/login", json={"password": "partner-pass-1"})
    check("партнёр входит", r.status_code == 200 and r.json()["role"] == "partner")
    st = cl.get("/admin/api/stats").json()
    check("статистика: роль, деньги, использование, тарифы", st["role"] == "partner" and "money" in st and "usage" in st
          and st["settings"]["limits"]["free"]["recipe"] == [3, 1] and st["settings"]["limits"]["free"]["daymenu"] == [1, 7], str(st.get("settings", {}).get("limits")))
    form = {"limits": {"free": {"recipe": [4, 1], "daymenu": [1, 7], "week": [0, 3], "missing": [2, 1]}},
            "offers": [{"key": "month", "days": 30, "rub": 249, "stars": 170}], "promo": {"blog_anna": 14},
            "features": ["memory", "pdf"], "trial_days": 5}
    r = cl.post("/admin/api/settings", json=form)
    check("партнёр НЕ может менять цены, лимиты, промокоды (только смотрит)", r.status_code == 403, r.text)
    plans.reload()
    check("настройки не изменились", plans.limits_of("free")["recipe"] == (3, 1) and plans.offer("month")["rub"] == 199, str(plans.limits_of("free")))
    r = cl.post("/admin/api/settings", json={"rollout": {"voice": "all"}, "testers": "123456789"})
    check("партнёр не включает функции и не меняет тестировщиков", r.status_code == 403 and plans.rollout()["voice"] == "testers")
    check("партнёр не выдаёт подписку", cl.post("/admin/api/grant", json={"uid": 555, "days": 10}).status_code == 403
          and not accounts.is_premium(555))
    check("партнёр не управляет рекламой", cl.post("/admin/api/ads/1/toggle").status_code == 403 and cl.delete("/admin/api/ads/1").status_code == 403)
    check("партнёр видит журнал и статистику", cl.get("/admin/api/log").status_code == 200 and cl.get("/admin/api/stats").status_code == 200)
    # дальше проверяем, что владелец всё это может (тарифы из админки доходят до лимитов)
    cl.post("/admin/logout")
    cl.cookies.clear()
    cl.post("/admin/login", json={"password": "owner-pass-1"})
    r = cl.post("/admin/api/settings", json=form)
    check("владелец меняет цены, лимиты, промокоды", r.status_code == 200 and set(r.json()["changed"]) >= {"LIMITS_FREE", "PREMIUM_PLANS", "PROMO_CODES"}, r.text[:200])
    plans.reload()
    config.PAYWALL_ENABLED = True
    check("новые настройки сразу в тарифах", plans.limits_of("free")["recipe"] == (4, 1) and plans.offer("month")["rub"] == 249
          and plans.promo_days("BLOG_ANNA") == 14 and plans.has_feature("123", "cart") and not plans.has_feature("123", "pdf"))
    U = 990001
    got = [usage.try_spend(U, "recipe", "bot")[0] for _ in range(4)]
    check("лимит из админки действует (4 блюда бесплатно)", all(got))
    config.PAYWALL_ENABLED = False
    check("владелец выдаёт подписку вручную", cl.post("/admin/api/grant", json={"uid": 555, "days": 10}).status_code == 200
          and accounts.is_premium(555))

    cl.post("/admin/logout")
    cl.cookies.clear()
    check("после выхода — 401", cl.get("/admin/api/stats").status_code == 401)
    cl.post("/admin/login", json={"password": "owner-pass-1"})
    r = cl.post("/admin/api/settings", json={"daily_cap": 800, "partner_share": 50})
    check("владелец меняет потолок", r.status_code == 200 and "DAILY_GEN_CAP" in r.json()["changed"] and int(plans.cfg("DAILY_GEN_CAP")) == 800)
    log = cl.get("/admin/api/log").json()["items"]
    whos = {(x["who"], x["action"]) for x in log}
    check("журнал: кто входил и что менял", ("partner", "вход") in whos and ("owner", "настройки") in whos
          and ("owner", "подписка вручную") in whos and any("LIMITS_FREE" in x["details"] for x in log), str(whos))
    check("подделанная кука партнёра не проходит", (cl.cookies.clear() or True)
          and cl.get("/admin/api/stats", cookies={"adm": "owner.fake"}).status_code == 401)
    # статистика с данными
    accounts.apply_payment("ch-a", 1001, 150, "XTR", 30, "month")
    accounts.apply_payment("ch-b", 1002, 19900, "RUB", 30, "month")
    usage.event("cook_start", 1001)
    cl.post("/admin/login", json={"password": "owner-pass-1"})
    st = cl.get("/admin/api/stats").json()
    m = st["money"]
    check("деньги: рубли из копеек, звёзды отдельно", m["total_rub"] == 199 and m["total_stars"] == 150, str(m)[:200])
    check("использование функций за 7 дней", st["usage"]["features_7d"].get("cook_start", {}).get("times") == 1)
    check("страница админки отдаётся", "Тарифы и лимиты" in cl.get("/admin").text)

print("\nИТОГ:", "ВСЁ ОК" if not fails else "ПРОВАЛЕНО: " + ", ".join(fails))
