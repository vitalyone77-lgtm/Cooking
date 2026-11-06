import os
import sys

SCR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCR)
from cdp import Chrome, ANDROID_UA, IOS_UA, TG_IOS_UA  # noqa: E402

BASE = "http://127.0.0.1:8123/"
fails = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def fresh(port, ua, block=False, **kw):
    c = Chrome(port=port, ua=ua, **kw)
    if block:
        c.block_install_event()
    c.goto(BASE, 2)
    c.wait_for("!!document.getElementById('go')")
    return c


# ---------- 1. Android: страница, манифест, SW, карточка установки ----------
c = fresh(9341, ANDROID_UA)
try:
    check("заголовок", c.eval("document.title") == "Кухонный помощник")
    m = c.eval("fetch('/manifest.webmanifest').then(r=>r.json())", True)
    check("манифест: standalone + 3 иконки", m["display"] == "standalone" and len(m["icons"]) == 3, str(m))
    icons_ok = c.eval("Promise.all(['/icons/icon-192.png','/icons/icon-512.png','/icons/icon-maskable-512.png','/icons/apple-touch-icon.png'].map(u=>fetch(u).then(r=>r.ok&&r.headers.get('content-type'))))", True)
    check("иконки отдаются", all(x == "image/png" for x in icons_ok), str(icons_ok))
    sw = c.eval("navigator.serviceWorker.ready.then(r=>r.active&&r.active.state)", True)
    check("service worker активен", sw == "activated", str(sw))
    card = c.eval("document.getElementById('install-top').innerText")
    check("карточка установки видна", "На главный экран" in card, card)
    check("кнопка внизу видна", "Добавить на главный экран" in c.eval("document.getElementById('install-bottom').innerText"))
    c.shot(os.path.join(SCR, "ui_new.png"))

    # «Как?» -> подсказка (в headless без настоящего beforeinstallprompt)
    btn_txt = c.eval("document.querySelector('#install-top [data-install]').innerText")
    print("   (кнопка в headless Chrome:", btn_txt + ")")
    # и «Как?», и сбой настоящего prompt() (программный клик — не жест пользователя) должны заканчиваться подсказкой
    c.eval("document.querySelector('#install-top [data-install]').click()")
    c.pump(0.4)
    check("подсказка Android (или фолбэк при сбое prompt)", "главный экран" in c.eval("document.getElementById('install-help').innerText"),
          c.eval("document.getElementById('install-help').innerText"))
    c.shot(os.path.join(SCR, "ui_help_android.png"))

    # синтетический beforeinstallprompt -> «Установить» -> вызывается prompt()
    c.eval("""(function(){var e=new Event('beforeinstallprompt');e.prompt=function(){window.__prompted=true;};
      e.userChoice=Promise.resolve({outcome:'accepted'});window.dispatchEvent(e);})()""")
    check("после beforeinstallprompt кнопка = «Установить»", c.eval("document.querySelector('#install-top [data-install]').innerText") == "Установить")
    c.eval("document.querySelector('#install-top [data-install]').click()")
    c.pump(0.5)
    check("prompt() вызван", c.eval("window.__prompted") is True)
    check("после установки карточка скрылась", c.eval("document.getElementById('install-top').innerText") == "")

    # закрытие крестиком запоминается
    c.goto(BASE, 1.5)
    c.wait_for("!!document.getElementById('go')")
    c.eval("document.querySelector('[data-install-close]').click()")
    check("крестик скрывает карточку", c.eval("document.getElementById('install-top').innerText") == "")
    check("кнопка внизу остаётся", "Добавить" in c.eval("document.getElementById('install-bottom').innerText"))
    check("запомнено на 14 дней", c.eval("!!localStorage.getItem('pwa_x')") is True)
    errs = [e for e in c.console_problems() if "favicon" not in e.lower()]
    check("нет ошибок в консоли (Android)", not errs, str(errs))
finally:
    c.close()

# ---------- 2. iPhone Safari ----------
c = fresh(9342, IOS_UA, block=True)
try:
    c.eval("document.querySelector('#install-top [data-install]').click()")
    h = c.eval("document.getElementById('install-help').innerText")
    check("iPhone: инструкция «Поделиться → На экран Домой»", "Поделиться" in h and "Домой" in h, h)
    c.shot(os.path.join(SCR, "ui_help_ios.png"))
finally:
    c.close()

# ---------- 3. Telegram внутри iPhone ----------
c = fresh(9343, TG_IOS_UA, block=True)
try:
    c.eval("document.querySelector('#install-top [data-install]').click()")
    h = c.eval("document.getElementById('install-help').innerText")
    check("Telegram iOS: просит открыть в Safari", "Safari" in h and "Telegram" in h, h)
finally:
    c.close()

# ---------- 4. внутри iframe (как на Тильде) ----------
c = fresh(9344, ANDROID_UA)
try:
    c.eval("document.body.innerHTML='<iframe id=fr src=\"/\" style=\"width:390px;height:800px;border:0\"></iframe>'")
    c.wait_for("(function(){var d=document.getElementById('fr').contentDocument;return !!(d&&d.getElementById('install-top'))})()")
    c.wait_for("document.getElementById('fr').contentDocument.getElementById('install-top').innerText.length>0")
    t = c.eval("document.getElementById('fr').contentDocument.getElementById('install-top').innerText")
    link = c.eval("document.getElementById('fr').contentDocument.querySelector('#install-top a').getAttribute('target')")
    check("iframe: вместо установки — «Открыть»", "Открыть" in t and link == "_blank", t)
    c.shot(os.path.join(SCR, "ui_framed.png"))
finally:
    c.close()

print("\nИТОГ:", "ВСЁ ОК" if not fails else "ПРОВАЛЕНО: " + ", ".join(fails))
sys.exit(1 if fails else 0)
