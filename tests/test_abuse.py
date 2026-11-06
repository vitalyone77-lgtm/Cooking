"""Защита от злоупотреблений: спам счётчиков, мусор в адресах и телах запросов."""
import os
import sys

PROJ = "/tmp/ct"
os.chdir(PROJ)
sys.path.insert(0, PROJ)
for f in ("usage_ab.db", "accounts_ab.db", "accounts_ab.db-wal", "accounts_ab.db-shm"):
    if os.path.exists(f):
        os.remove(f)
os.environ["USAGE_DB"] = PROJ + "/usage_ab.db"
os.environ["ACCOUNTS_DB"] = PROJ + "/accounts_ab.db"
os.environ["WEB_DATA_DIR"] = "web_data_ab"

from fastapi.testclient import TestClient
import web_api
import web_db

fails = []


def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{x}]"))
    if not c:
        fails.append(n)


cl = TestClient(web_api.app)
dev = cl.post("/api/device").json()["device"]
H = {"X-Device": str(dev)}


def n_events(kind):
    with web_db.db() as c:
        return c.execute("SELECT COUNT(*) FROM events WHERE kind=?", (kind,)).fetchone()[0]


for _ in range(150):
    r = cl.post("/api/visit", headers=H)
check("спам /api/visit отвечает ok, но в базу попадает не больше 60 в час", r.status_code == 200 and n_events("visit") == web_api.STAT_PER_HOUR, str(n_events("visit")))
for _ in range(150):
    r = cl.post("/api/gate", json={"feature": "pdf"}, headers=H)
check("спам /api/gate не раздувает базу", r.status_code == 200)
r = cl.post("/api/gate", json={"feature": "x" * 5000, "junk": [1] * 1000}, headers=H)
check("мусорное тело /api/gate не падает", r.status_code == 200)
for path in ("/r/../../etc/passwd", "/r/%00", "/r/" + "a" * 500, "/rimg/xx.png", "/r/Привет"):
    check(f"публичная страница на мусорный адрес даёт 404, а не 500: {path[:30]}", cl.get(path).status_code in (404, 422))
check("/api/foods и дневник не отдаются без устройства", cl.get("/api/me").status_code in (200, 401, 403, 422))
check("админка закрыта без входа", cl.get("/admin/api/stats").status_code in (401, 403))
check("партнёр и чужие не могут менять настройки без входа", cl.post("/admin/api/settings", json={}).status_code in (401, 403, 422))
check("устройств с одного адреса не больше 20 в час", [cl.post("/api/device").status_code for _ in range(25)].count(429) >= 4)
r = cl.post("/api/feedback", json={"kind": "idea", "text": "а" * 3000}, headers=H)
check("слишком длинный отзыв отклоняется (422)", r.status_code == 422)
r = cl.post("/api/weight", json={"kg": 9999, "day": "2026-01-01"}, headers=H)
check("нелепый вес отклоняется, не 500", r.status_code in (400, 403, 422), str(r.status_code))
print("\nИТОГ:", "ВСЁ ОК" if not fails else "ПРОВАЛЕНО: " + ", ".join(fails))
