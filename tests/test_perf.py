"""Скорость открытия приложения: Метрика не держит window.load, service worker отдаёт кэш после 1.8 с,
версия кэша поднята, чтобы после выкладки не застрять на старой оболочке."""
import os
import sys

PROJ = "/tmp/ct"
os.chdir(PROJ)

fails = []


def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{x}]"))
    if not c:
        fails.append(n)


sw = open("web/sw.js", encoding="utf-8").read()
idx = open("web/index.html", encoding="utf-8").read()

check("метрика: tag.js не вставляется при разборе страницы", "document.scripts.length" not in idx)
check("метрика: загрузка отложена до простоя после load",
      "requestIdleCallback(start, {timeout: 2000})" in idx and 'window.addEventListener("load", later' in idx)
check("метрика: очередь ym работает до загрузки tag.js",
      "window.ym = window.ym || function()" in idx and 'tag.js?id=113752594' in idx)
check("service worker регистрируется сразу, а не после load",
      'navigator.serviceWorker.register("/sw.js")' in idx
      and 'window.addEventListener("load",function(){try{navigator.serviceWorker.register' not in idx)

check("SW: версия кэша поднята", 'kitchen-shell-v34' in sw)
check("SW: сеть ждём не дольше 1.8 с", "NET_TIMEOUT = 1800" in sw
      and "setTimeout(() => resolve(null), NET_TIMEOUT)" in sw)
check("SW: при таймауте отдаём кэш, сеть догружается в фоне",
      "return fresh || cached;" in sw and "e.waitUntil(net.then(() => {}).catch(() => {}))" in sw)
check("SW: главная по-прежнему единственная офлайн-страница", 'if (url.pathname !== "/") return;' in sw)
check("SW: /api и служебные разделы не кэшируются",
      r"/^\/(api|admin|go|uploads)(\/|$)/" in sw)

print("Провалов:", len(fails))
sys.exit(1 if fails else 0)
