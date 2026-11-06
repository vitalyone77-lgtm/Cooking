"""Таймеры в шагах рецепта (веб): у каждого шага со временем свой таймер; «5–6 мин» — сигнал на 5-й, счёт до 6-й."""
import json
import os
import sys
import threading
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


RECIPE = {"title": "Каша с яйцом",
          "text": ("🍽 Каша с яйцом\n\nИнструкция:\n1. В кастрюле доведи молоко до кипения.\n"
                   "2. Всыпь овсянку и вари 5–6 мин, помешивая.\n3. Пока варится каша, отвари яйцо: вари 8 мин, затем остуди.\n"
                   "4. Подай через 1,5 часа после тренировки."),
          "shopping": []}
META = {"cuisines": [{"key": "classic", "label": "Классическая"}], "appliances": [{"key": "stove", "label": "Плита"}],
        "meals": [{"key": "lunch", "label": "Обед"}], "portions": [{"key": "normal", "label": "Обычная"}], "week_days": 3, "limit": 12, "limits": {}}


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
            self._send({"status": "done", "result": RECIPE})
        elif p in ("/api/ad", "/api/overview", "/api/favorites"):
            self._send({"ad": None, "items": []})
        elif p == "/sw.js":
            self._send(b"", "application/javascript")
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        if self.path == "/api/device":
            self._send({"device": "123"})
        elif self.path == "/api/recipe":
            self._send({"job": "jobR"})
        else:
            self._send({})


srv = ThreadingHTTPServer(("127.0.0.1", 8179), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()


def timers(c):
    return c.eval("Array.prototype.map.call(document.querySelectorAll('[data-tm]'),function(n){return n.className+'|'+n.innerText.replace(/\\n/g,' ');})")


c = Chrome(port=9352)
try:
    c.goto("http://127.0.0.1:8179/", 1.5)
    c.wait_for("!!document.getElementById('go')")
    c.eval("window.alert=function(m){window.__alert=m;}")
    # ускоряем время: Date.now() со сдвигом
    c.eval("(function(){var o=Date.now;window.__off=0;Date.now=function(){return o.call(Date)+window.__off;};})()")
    c.eval("document.getElementById('go').click()")
    c.wait_for("document.querySelector('h1') && document.querySelector('h1').innerText.indexOf('Каша')>=0", 20)
    btns = c.eval("Array.prototype.map.call(document.querySelectorAll('[data-tm-start]'),function(b){return b.innerText;})")
    check("таймеры у шагов со временем: 5–6 мин, 8 мин, 1,5 ч", btns == ["⏱ 5–6 мин", "⏱ 8 мин", "⏱ 1,5 ч"], str(btns))
    check("шаг без времени — без таймера", c.eval("document.querySelectorAll('.step').length") == 4 and c.eval("document.querySelectorAll('.timers').length") == 3)
    c.eval("document.querySelectorAll('[data-tm-start]')[0].click()")   # 5–6 мин
    c.eval("document.querySelectorAll('[data-tm-start]')[0].click()")   # следующая кнопка — «8 мин»
    c.pump(1.3)
    st = timers(c)
    check("два независимых таймера идут", len(st) == 2 and st[0].startswith("tm run") and st[1].startswith("tm run"), str(st))
    check("видно «сигнал на 5:00, до 6:00»", "5:00" in st[0] and "6:00" in st[0], str(st))
    c.shot(os.path.join(SCR, "ui_timers1.png"))

    c.eval("window.__off=310000")   # прошло 5 мин 10 с
    c.pump(1.5)
    st = timers(c)
    check("5–6: на 5-й минуте «можно снимать», счёт идёт дальше", st[0].startswith("tm ok") and "можно снимать" in st[0] and "5:1" in st[0], str(st))
    check("таймер на 8 мин на 5-й минуте ещё идёт", st[1].startswith("tm run"), str(st))
    check("всплыло уведомление", c.eval("!!document.querySelector('.toast')") is True and "можно снимать" in c.eval("document.querySelector('.toast').innerText"))
    c.shot(os.path.join(SCR, "ui_timers2.png"))

    c.eval("window.__off=370000")   # 6 мин 10 с
    c.pump(1.5)
    st = timers(c)
    check("после 6-й минуты первый таймер закончился", st[0].startswith("tm end") and "Время вышло" in st[0], str(st))
    check("таймер на 8 мин всё ещё считает", not st[1].startswith("tm end"), str(st))

    c.eval("window.__off=490000")   # 8 мин 10 с
    c.pump(1.5)
    st = timers(c)
    check("после 8-й минуты закончился и второй", st[1].startswith("tm end"), str(st))

    c.eval("document.querySelector('[data-tab=favs]').click()")
    c.pump(1.0)
    check("таймеры сохранены в браузере", c.eval("Object.keys(JSON.parse(localStorage.getItem('timers')||'{}')).length") == 2)
    c.eval("document.querySelector('[data-tab=new]').click()")
    c.pump(0.5)
    c.eval("document.getElementById('go').click()")
    c.wait_for("document.querySelector('h1') && document.querySelector('h1').innerText.indexOf('Каша')>=0", 20)
    check("после перерисовки идущие таймеры показаны снова", c.eval("document.querySelectorAll('[data-tm]').length") == 2)
    c.eval("document.querySelector('[data-tm-stop]').click()")
    check("✕ возвращает кнопку запуска", c.eval("document.querySelectorAll('[data-tm]').length") == 1 and c.eval("document.querySelectorAll('[data-tm-start]').length") == 2)
    errs = [e for e in c.console_problems() if "favicon" not in e.lower() and "404" not in e and "anifest" not in e]
    check("нет ошибок в консоли", not errs, str(errs))
finally:
    c.close()
    srv.shutdown()

print("\nИТОГ:", "ВСЁ ОК" if not fails else "ПРОВАЛЕНО: " + ", ".join(fails))
sys.exit(1 if fails else 0)
