"""Экран веба: рецепт → «Нет каких-то продуктов» → замена. Сервер-заглушка отдаёт готовые ответы API."""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer as HTTPServer

SCR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCR)
from cdp import Chrome  # noqa: E402

PROJ = r"C:\Users\user\Desktop\ai_learning\Cooking"
fails = []
posted = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


LINKS = [{"store": s, "url": "https://x/" + s} for s in ("Пятёрочка", "ВкусВилл")]
RECIPE = {"title": "Курица с кабачком", "text": "🍽 Курица с кабачком\n\nПродукты:\n- Курица — 300 г\n- Кабачок — 200 г\n\nИнструкция:\n1. Варить.",
          "shopping": [{"name": "курица", "amount": "300 г", "links": LINKS}, {"name": "кабачок", "amount": "200 г", "links": LINKS}]}
NEW = {"title": "Курица с морковью", "text": "🍽 Курица с морковью\n\nПродукты:\n- Курица — 300 г\n- Морковь — 200 г\n\nИнструкция:\n1. Варить.",
       "shopping": [{"name": "курица", "amount": "300 г", "links": LINKS}, {"name": "морковь", "amount": "200 г", "links": LINKS}]}
META = {"cuisines": [{"key": "classic", "label": "Классическая"}], "appliances": [{"key": "stove", "label": "Плита"}],
        "meals": [{"key": "lunch", "label": "Обед"}], "week_days": 3, "limit": 12, "limits": {}}


class H(BaseHTTPRequestHandler):
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
        elif p == "/api/meta":
            self._send(META)
        elif p.startswith("/api/job/"):
            self._send({"status": "done", "result": NEW if p.endswith("jobM") else RECIPE})
        elif p in ("/api/ad", "/api/overview", "/api/favorites"):
            self._send({"ad": None, "items": []})
        elif p == "/sw.js":
            self._send(b"", "application/javascript")
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode() if n else ""
        if self.path == "/api/device":
            self._send({"device": "123"})
        elif self.path == "/api/recipe":
            self._send({"job": "jobR"})
        elif self.path == "/api/missing":
            posted.append(json.loads(body))
            self._send({"job": "jobM"})
        else:
            self._send({})


srv = HTTPServer(("127.0.0.1", 8178), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()

c = Chrome(port=9351)
try:
    c.goto("http://127.0.0.1:8178/", 1.5)
    c.wait_for("!!document.getElementById('go')")
    c.eval("window.alert=function(m){window.__alert=m;}")
    c.pump(0.3)
    c.eval("document.getElementById('go').click()")
    try:
        c.wait_for("document.querySelector('h1') && document.querySelector('h1').innerText.indexOf('кабачком')>=0", 12)
    except TimeoutError:
        print("DEBUG main:", c.eval("document.getElementById('main').innerText.slice(0,300)"), "| alert:", c.eval("window.__alert||''"), "| posts:", posted)
        print("DEBUG console:", c.console_problems())
        raise
    check("рецепт показан", "кабачком" in c.eval("document.querySelector('h1').innerText"))
    check("в рецепте есть кнопка «Нет каких-то продуктов»", c.eval("!!document.querySelector('[data-miss-open]')") is True)
    check("панель замены скрыта", c.eval("document.querySelector('.misspanel').style.display") == "none")
    c.eval("document.querySelector('.acc-head').click()")  # раскрыть блок «Найти продукты»
    c.eval("document.querySelector('[data-miss-open]').click()")
    check("панель открылась", c.eval("document.querySelector('.misspanel').style.display") == "")
    check("в панели 2 продукта с чекбоксами", c.eval("document.querySelectorAll('[data-miss-name]').length") == 2)
    c.shot(os.path.join(SCR, "ui_missing.png"))
    # без выбора — предупреждение (alert глушим)
    c.eval("document.querySelector('[data-miss-go]').click()")
    check("без выбора — подсказка", "хотя бы один" in (c.eval("window.__alert||''") or ""))
    c.eval("document.querySelector('[data-miss-name=\"кабачок\"]').click()")
    c.eval("document.querySelector('[data-miss-extra]').value='лук, чеснок'")
    c.eval("document.querySelector('[data-miss-go]').click()")
    c.wait_for("document.querySelector('h1') && document.querySelector('h1').innerText.indexOf('морковью')>=0", 20)
    check("после замены показан новый рецепт", "морковью" in c.eval("document.querySelector('h1').innerText"))
    check("на сервер ушло: кабачок + лук + чеснок", posted and posted[0]["items"] == ["кабачок", "лук", "чеснок"] and posted[0]["ctx"] == "recipe", str(posted))
    check("на сервер ушёл текст рецепта", posted and "Курица с кабачком" in posted[0]["text"], str(posted)[:200])
    check("у нового рецепта снова есть кнопка", c.eval("!!document.querySelector('[data-miss-open]')") is True)
    errs = [e for e in c.console_problems() if "favicon" not in e.lower()]
    check("нет ошибок в консоли", not errs, str(errs))
finally:
    c.close()
    srv.shutdown()

print("\nИТОГ:", "ВСЁ ОК" if not fails else "ПРОВАЛЕНО: " + ", ".join(fails))
sys.exit(1 if fails else 0)
