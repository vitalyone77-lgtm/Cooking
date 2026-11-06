"""Без интернета: оболочка сайта (service worker) + меню на день, план, избранное и последний рецепт из сохранённого."""
import json
import mimetypes
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SCR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCR)
from cdp import Chrome  # noqa: E402

PROJ = r"C:\Users\user\Desktop\ai_learning\Cooking"
fails = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


LINKS = [{"store": "Пятёрочка", "url": "https://x/p"}]
RECIPE = {"title": "Каша с яйцом", "text": "🍽 Каша с яйцом\n\nИнструкция:\n1. Вари 5–6 мин.", "shopping": []}
DAY = {"meals": [{"key": "lunch", "label": "🍲 Обед", "title": "Суп с фрикадельками", "text": "🍽 Суп\n\nИнструкция:\n1. Вари 20 мин."}],
       "shopping": [{"name": "фарш", "amount": "300 г", "links": LINKS}]}
WEEK = {"days": [{"day": 1, "generated": True, "is_next": False, "total": 1, "done_count": 0,
                  "meals": [{"key": "lunch", "label": "🍲 Обед", "title": "Плов", "text": "🍽 Плов", "index": 0, "done": False}], "extra_shopping": []}],
        "basket": {"items": [{"name": "рис", "amount": "600 г", "links": LINKS}], "priced_text": "", "advice": "Хранить в холоде"}, "remaining": {}}
FAV = {"items": [{"id": "f1", "title": "Сырники", "text": "🍽 Сырники\n\nИнструкция:\n1. Жарь 3 мин."}]}
META = {"cuisines": [{"key": "classic", "label": "Классическая"}], "appliances": [{"key": "stove", "label": "Плита"}],
        "meals": [{"key": "lunch", "label": "Обед"}], "portions": [{"key": "normal", "label": "Обычная"}], "week_days": 3, "limit": 12, "limits": {}}


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *a):
        pass

    def _send(self, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/":
            self._send(open(os.path.join(PROJ, "web", "index.html"), "rb").read().replace(b"<!--VERIFY-->", b""), "text/html")
        elif p == "/sw.js":
            self._send(open(os.path.join(PROJ, "web", "sw.js"), "rb").read(), "application/javascript")
        elif p == "/manifest.webmanifest":
            self._send(open(os.path.join(PROJ, "web", "manifest.webmanifest"), "rb").read(), "application/manifest+json")
        elif p.startswith("/icons/"):
            fp = os.path.join(PROJ, "web", p.lstrip("/"))
            if os.path.exists(fp):
                self._send(open(fp, "rb").read(), mimetypes.guess_type(fp)[0] or "image/png")
            else:
                self.send_response(404); self.end_headers()
        elif p == "/api/meta":
            self._send(META)
        elif p == "/api/overview":
            self._send({"has_day": True, "has_week": True})
        elif p == "/api/day":
            self._send(DAY)
        elif p == "/api/week":
            self._send(WEEK)
        elif p == "/api/favorites":
            self._send(FAV)
        elif p.startswith("/api/job/"):
            self._send({"status": "done", "result": RECIPE})
        elif p == "/api/ad":
            self._send({"ad": None})
        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        if self.path == "/api/device":
            self._send({"device": "123"})
        elif self.path == "/api/recipe":
            self._send({"job": "jobR"})
        else:
            self._send({})


srv = ThreadingHTTPServer(("127.0.0.1", 8180), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()

c = Chrome(port=9353)
try:
    c.goto("http://127.0.0.1:8180/", 2.0)
    c.wait_for("!!document.getElementById('go')")
    c.wait_for("navigator.serviceWorker && navigator.serviceWorker.controller === null || true")
    c.eval("navigator.serviceWorker.ready.then(function(){window.__swready=true;})")
    c.wait_for("window.__swready===true", 15)
    c.goto("http://127.0.0.1:8180/", 2.0)           # перезагрузка уже под управлением service worker
    c.wait_for("!!document.getElementById('go')")
    check("service worker управляет страницей", c.eval("!!navigator.serviceWorker.controller") is True)
    check("оболочка сохранена в кэше", c.eval("caches.open('kitchen-shell-v6').then(function(x){return x.match('/');}).then(function(r){return !!r;})", True) is True)

    # онлайн: открываем рецепт, меню на день, план, избранное (копии сохраняются)
    c.eval("document.getElementById('go').click()")
    c.wait_for("document.querySelector('h1') && document.querySelector('h1').innerText.indexOf('Каша')>=0", 20)
    c.eval("document.querySelector('[data-tab=menus]').click()")
    c.wait_for("document.body.innerText.indexOf('Плов')>=0", 10)
    c.eval("document.querySelector('[data-sub=day]').click()")
    c.wait_for("document.body.innerText.indexOf('Суп с фрикадельками')>=0", 10)
    c.eval("document.querySelector('[data-sub=week]').click()")
    c.wait_for("document.body.innerText.indexOf('Плов')>=0", 10)
    c.eval("document.querySelector('[data-tab=favs]').click()")
    c.wait_for("document.body.innerText.indexOf('Сырники')>=0", 10)
    keys = c.eval("Object.keys(localStorage).filter(function(k){return k.indexOf('oc:')===0;}).sort().join(',')")
    check("копии меню, плана, избранного сохранены", keys == "oc:/api/day,oc:/api/favorites,oc:/api/overview,oc:/api/week", keys)

    # интернет пропал
    srv.shutdown()
    srv.server_close()
    time.sleep(0.5)
    c.goto("http://127.0.0.1:8180/", 2.0)
    ok = c.wait_for("!!document.getElementById('go') || document.body.innerText.length>50", 10)
    check("страница открывается без интернета", c.eval("!!document.getElementById('main') && document.getElementById('main').innerText.length>20") is True, c.eval("document.body.innerText.slice(0,120)"))
    check("на главной — «Последний рецепт»", "Последний рецепт: Каша с яйцом" in c.eval("document.getElementById('main').innerText"))
    c.eval("document.querySelector('[data-last-recipe]').click()")
    c.pump(0.3)
    check("последний рецепт открывается без сети (с таймером)", "Каша с яйцом" in c.eval("document.querySelector('h1').innerText") and c.eval("!!document.querySelector('[data-tm-start]')") is True)
    c.eval("document.querySelector('[data-tab=menus]').click()")
    c.wait_for("document.body.innerText.indexOf('Нет интернета')>=0", 10)
    check("план без сети: плашка и день", "Плов" in c.eval("document.getElementById('main').innerText") and "Нет интернета" in c.eval("document.getElementById('main').innerText"))
    c.eval("document.querySelector('[data-sub=day]').click()")
    c.wait_for("document.body.innerText.indexOf('Суп с фрикадельками')>=0", 10)
    txt = c.eval("document.getElementById('main').innerText")
    check("меню на день без сети: плашка и блюдо", "Нет интернета" in txt and "Суп с фрикадельками" in txt, txt[:200])
    c.eval("document.querySelector('[data-tab=favs]').click()")
    c.wait_for("document.body.innerText.indexOf('Сырники')>=0", 10)
    check("избранное без сети открывается", "Сырники" in c.eval("document.getElementById('main').innerText"))
    c.shot(os.path.join(SCR, "ui_offline.png"))
finally:
    c.close()

print("\nИТОГ:", "ВСЁ ОК" if not fails else "ПРОВАЛЕНО: " + ", ".join(fails))
sys.exit(1 if fails else 0)
