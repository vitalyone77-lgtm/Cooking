"""API «Нет продуктов» в вебе: рецепт / день / план, лимит, проверки ввода (ИИ подменён)."""
import os
import sys
import time

PROJ = "/tmp/ct"
os.chdir(PROJ)
sys.path.insert(0, PROJ)
os.environ["WEB_DATA_DIR"] = "/tmp/ct/web_data_test2"

from fastapi.testclient import TestClient
import config
import web_api
import missing
import day_menu
import week_menu

fails = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


NEW = ("🍽 Курица с морковью\n\nПродукты:\n- Курица — 300 г\n- Морковь — 200 г\n\nИнструкция:\n1. Варить.\n"
       "ПРОДУКТЫ_СПИСОК: курица, морковь")
prompts = []


async def fake_llm(messages, **kw):
    prompts.append(messages[-1]["content"])
    return NEW


missing.call_llm_with_fallback = fake_llm


def wait_job(c, dev, job):
    for _ in range(60):
        r = c.get(f"/api/job/{job}", headers={"X-Device": dev}).json()
        if r["status"] != "running":
            return r
        time.sleep(0.1)
    return {"status": "timeout"}


with TestClient(web_api.app) as c:
    dev = str(c.post("/api/device").json()["device"])
    H = {"X-Device": dev}
    cid = web_api._cid(int(dev)) if dev.isdigit() else web_api._cid(web_api._dev(dev))

    r = c.post("/api/missing", json={"ctx": "oops", "items": ["x"]}, headers=H)
    check("неверный ctx — 422", r.status_code == 422, str(r.status_code))
    r = c.post("/api/missing", json={"ctx": "recipe", "items": ["кабачок"], "text": ""}, headers=H)
    check("рецепт без текста — 400", r.status_code == 400, str(r.status_code))
    r = c.post("/api/missing", json={"ctx": "day", "items": ["кабачок"]}, headers=H)
    check("день без меню — 404", r.status_code == 404, str(r.status_code))

    r = c.post("/api/missing", json={"ctx": "recipe", "items": ["Кабачок", "кабачок"], "text": "🍽 Старое\n\nПродукты:\n- Кабачок — 200 г"}, headers=H)
    res = wait_job(c, dev, r.json()["job"])
    check("рецепт: заменён", res["status"] == "done" and res["result"]["title"].startswith("Курица с морковью"), str(res))
    check("рецепт: у продуктов есть количества", res["status"] == "done" and any(i.get("amount") for i in res["result"]["shopping"]), str(res)[:300])
    check("продукт ушёл в ИИ один раз", prompts and prompts[0].count("Кабачок") >= 1 and "ЭТИХ ПРОДУКТОВ НЕТ у пользователя: Кабачок." in prompts[0] and "ТО ЖЕ САМОЕ БЛЮДО" in prompts[0])

    # меню на день
    day_menu._save_menu(cid, {"order": ["lunch"], "meals": {"lunch": {"title": "Т", "text": "🍽 Т\n\nПродукты:\n- Кабачок — 200 г", "ok": True}},
                              "shopping_terms": ["кабачок"], "base_data": {"cuisine": "classic", "excluded": ""}})
    got = {}

    async def fake_gen(chat_id, meals, data):
        got["data"] = data
        day_menu._save_menu(chat_id, {"order": meals, "meals": {"lunch": {"title": "Н", "text": "🍽 Н\n\nПродукты:\n- Морковь — 200 г", "ok": True}},
                                      "shopping_terms": ["морковь"], "base_data": data})
        return {}
    day_menu.generate_day_menu = fake_gen
    r = c.post("/api/missing", json={"ctx": "day", "items": ["кабачок"]}, headers=H)
    res = wait_job(c, dev, r.json()["job"])
    entry = day_menu._load_menu(cid)
    check("день: заменено только в блюде с продуктом, меню целиком не пересобиралось",
          res["status"] == "done" and not got and entry["base_data"]["excluded"] == "кабачок", str(res) + str(got))

    # лимит замен (5 в сутки): уже потрачено 2 успешных (рецепт, день)
    kinds = 0
    for i in range(6):
        r = c.post("/api/missing", json={"ctx": "recipe", "items": ["лук"], "text": "🍽 Т\n\nПродукты:\n- Лук — 1 шт"}, headers=H)
        if r.status_code == 429:
            kinds = i
            break
        wait_job(c, dev, r.json()["job"])
    check("лимит замен срабатывает (всего 5 в сутки)", kinds == 3, str(kinds))
    check("сообщение при лимите без цифр про «12»", "Лимит подборов" in r.text and "12" not in r.text, r.text)

    # возврат попытки при сбое ИИ
    async def bad_llm(messages, **kw):
        raise RuntimeError("ИИ недоступен")
    missing.call_llm_with_fallback = bad_llm
    dev2 = str(c.post("/api/device").json()["device"])
    H2 = {"X-Device": dev2}
    r = c.post("/api/missing", json={"ctx": "recipe", "items": ["лук"], "text": "🍽 Т\n\nПродукты:\n- Лук — 1 шт"}, headers=H2)
    res = wait_job(c, dev2, r.json()["job"])
    cid2 = web_api._cid(web_api._dev(dev2))
    import web_db
    check("при сбое ИИ ошибка показана, попытка возвращена", res["status"] == "error" and
          web_db.count_since(web_api._dev(dev2), "missing", 0) == 0, str(res))

for pth in ("day_menu.json", "week_menu.json"):
    pass
print("\nИТОГ:", "ВСЁ ОК" if not fails else "ПРОВАЛЕНО: " + ", ".join(fails))
sys.exit(1 if fails else 0)
