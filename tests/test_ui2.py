import glob
import json
import os
import sqlite3
import sys
import time

SCR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCR)
import pymupdf  # noqa: E402
from cdp import Chrome, ANDROID_UA  # noqa: E402

BASE = "http://127.0.0.1:8123/"
WEBDATA = os.path.join(SCR, "webdata")
DL = os.path.join(SCR, "dl")
fails = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def clear_dl():
    os.makedirs(DL, exist_ok=True)
    for f in glob.glob(os.path.join(DL, "*")):
        os.remove(f)


def read_pdf(path, shot=None):
    """Открывает PDF, возвращает (текст, число страниц) и ОБЯЗАТЕЛЬНО закрывает файл (иначе Windows не даст его удалить)."""
    doc = pymupdf.open(path)
    try:
        if shot:
            doc[0].get_pixmap(dpi=70).save(shot)
        return "".join(pg.get_text() for pg in doc), len(doc)
    finally:
        doc.close()


def wait_pdf(timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        done = [f for f in glob.glob(os.path.join(DL, "*.pdf"))]
        part = glob.glob(os.path.join(DL, "*.crdownload"))
        if done and not part:
            return done[0]
        time.sleep(0.3)
    return None


RECIPE = """🍽 Куриное филе с киноа, брокколи и авокадо

⏱ Время: 15 мин | 👥 Порций: 1 | 🔥 Способ: плита

*Пищевая ценность (на 1 порцию):*
Калорийность: **492 ккал** | Б: 41 г | Ж: 17 г | У: 47 г

*Продукты:*
- Куриное филе без кожи — 150 г
- Киноа (сухая) — 50 г

*Инструкция:*
1. Промойте киноа и варите ≈ 10 мин.
2. Обжарьте курицу 5‑6 мин, часто помешивая.

💡 Совет: Оставьте киноа под крышкой ещё 5 мин."""


def meal(title):
    return {"title": title, "text": f"🍽 {title}\n\n*Продукты:*\n- рис — 100 г\n\n*Инструкция:*\n1. Приготовить."}


c = Chrome(port=9361, ua=ANDROID_UA, download_dir=DL)
try:
    c.goto(BASE, 2)
    c.wait_for("!!document.getElementById('go')")
    dev = c.eval("localStorage.getItem('dev')")
    check("получен код устройства", bool(dev) and len(dev) == 32)

    # --- наполняем устройство: 2 избранных, меню на день, план на 3 дня ---
    for t in ("Куриное филе с киноа", "Рисовая каша"):
        c.eval(f"fetch('/api/favorites',{{method:'POST',headers:{{'X-Device':'{dev}','Content-Type':'application/json'}},body:JSON.stringify({{title:{json.dumps(t, ensure_ascii=False)},text:{json.dumps(RECIPE, ensure_ascii=False)}}})}}).then(r=>r.status)", True)
    con = sqlite3.connect(os.path.join(WEBDATA, "web.db"))
    dev_id = con.execute("SELECT id FROM devices WHERE uuid=?", (dev,)).fetchone()[0]
    cid = str(10 ** 12 + dev_id)
    con.execute("CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT)")
    con.execute("INSERT OR REPLACE INTO kv VALUES(?,?)", (f"dayshop:{cid}", json.dumps([{"name": "рис", "links": [{"store": "5ka", "url": "http://x"}]}], ensure_ascii=False)))
    con.commit(); con.close()
    day = {cid: {"order": ["breakfast", "dinner"], "meals": {"breakfast": {**meal("Рисовая каша"), "ok": True}, "dinner": {**meal("Куриный суп"), "ok": True}}}}
    json.dump(day, open(os.path.join(WEBDATA, "day_menu.json"), "w", encoding="utf-8"), ensure_ascii=False)
    basket = ("*Список покупок:*\n- курица — 1500 г\n- рис — 900 г\n\n💡 *Советы по хранению и разморозке:*\n"
              "- Курицу раздели на 3 части: одну оставь в холодильнике, две убери в морозилку.\n- Рис храни в сухой банке.")
    week = {cid: {"base_data": {}, "meal_keys": ["breakfast", "dinner"], "days_total": 3,
                  "inventory": {"курица": 900, "рис": 500}, "inventory_initial": {"курица": 1500, "рис": 900},
                  "basket_text": basket, "history_titles": ["Рисовая каша", "Куриный суп"],
                  "days": {"1": {"order": ["breakfast", "dinner"], "meals": {"breakfast": meal("Рисовая каша"), "dinner": meal("Куриный суп")}, "shopping_message": ""}},
                  "current_day": 2, "progress_index": 1}}
    json.dump(week, open(os.path.join(WEBDATA, "week_menu.json"), "w", encoding="utf-8"), ensure_ascii=False)

    # ---------- избранное ----------
    c.eval("document.querySelector('.tab[data-tab=favs]').click()")
    c.wait_for("document.querySelectorAll('.acc-head').length>=2")
    txt = c.eval("document.getElementById('main').innerText")
    check("избранное: кнопка «Всё избранное — одним PDF»", "Всё избранное — одним PDF" in txt)
    check("избранное: подсказка про потерю данных", "чтобы не потерять, скачай PDF" in txt)
    c.shot(os.path.join(SCR, "ui_favs.png"))

    clear_dl()
    c.eval("document.querySelector('.acc-head').click()")
    c.eval("document.querySelector('[data-pdf]').click()")
    path = wait_pdf()
    check("PDF рецепта скачался", path is not None)
    if path:
        name = os.path.basename(path)
        check("имя файла = название рецепта", name == "Куриное филе с киноа.pdf", name)
        text, _ = read_pdf(path, os.path.join(SCR, "ui_pdf_single.png"))
        check("в PDF есть текст и «5-6 мин» (дефис не потерян)", "киноа" in text.lower() and "5-6 мин" in text, text[:200])
    check("кнопка вернулась в исходный вид", c.eval("document.querySelector('[data-pdf]').innerText") == "📄 Скачать PDF")

    clear_dl()
    c.eval("document.querySelector('[data-pdfall]').click()")
    path = wait_pdf()
    check("PDF «всё избранное» скачался", path is not None)
    if path:
        _, pages = read_pdf(path)
        check("в нём 2 рецепта (2+ страниц)", pages >= 2, str(pages))
        check("имя «Избранные рецепты.pdf»", os.path.basename(path) == "Избранные рецепты.pdf", os.path.basename(path))

    # ---------- готовые меню: день и план ----------
    c.eval("document.querySelector('.tab[data-tab=menus]').click()")
    c.wait_for("document.getElementById('main').innerText.length>40")
    txt = c.eval("document.getElementById('main').innerText")
    sub = "week" if "Весь план — одним PDF" in txt else "day"
    print("   (открылась вкладка:", sub + ")")
    if sub != "week":
        c.eval("document.querySelector('[data-sub=week]').click()")
        c.wait_for("document.getElementById('main').innerText.includes('Весь план')")
        txt = c.eval("document.getElementById('main').innerText")
    check("план: кнопка «Весь план — одним PDF»", "Весь план — одним PDF" in txt)
    check("план: блок «Хранение и разморозка» показан", "Хранение и разморозка" in txt)
    check("план: корзина показана", "Корзина на весь план" in txt)
    c.eval("Array.from(document.querySelectorAll('.acc-head')).filter(h=>h.innerText.includes('Хранение')).forEach(h=>h.click())")
    check("совет по хранению читается", "Курицу раздели на 3 части" in c.eval("document.getElementById('main').innerText"))
    c.shot(os.path.join(SCR, "ui_week.png"), full=True)
    clear_dl()
    c.eval("document.querySelector('[data-pdfall]').click()")
    path = wait_pdf()
    check("PDF плана скачался", path is not None)
    if path:
        text, _ = read_pdf(path)
        check("в PDF плана: советы по хранению и блюда дня 1", "Курицу раздели" in text and "Куриный суп" in text, text[:300])

    c.eval("document.querySelector('[data-sub=day]').click()")
    c.wait_for("document.getElementById('main').innerText.includes('Всё меню')")
    clear_dl()
    c.eval("document.querySelector('[data-pdfall]').click()")
    path = wait_pdf()
    check("PDF меню на день скачался", path is not None)
    if path:
        text, _ = read_pdf(path)
        check("в PDF меню: блюда и список покупок", "Рисовая каша" in text and "Куриный суп" in text and "Список покупок" in text, text[:300])
    c.shot(os.path.join(SCR, "ui_day.png"))

    # ---------- сервер не смог собрать PDF -> диалог печати ----------
    c.eval("document.querySelector('.tab[data-tab=favs]').click()")
    c.wait_for("document.querySelectorAll('.acc-head').length>=2")
    c.eval("""window.__printed=false;window.print=function(){window.__printed=true;};
      var of=window.fetch;window.fetch=function(u,o){if(String(u).indexOf('/api/pdf')>=0)return Promise.resolve(new Response('{}',{status:501}));return of.apply(this,arguments);};""")
    c.eval("document.querySelector('.acc-head').click()")
    c.eval("document.querySelector('[data-pdf]').click()")
    c.wait_for("window.__printed===true", 8)
    check("при ошибке сервера открылся диалог печати", c.eval("window.__printed") is True)
    check("текст для печати подготовлен", "киноа" in c.eval("document.getElementById('print-area').textContent").lower())
    check("кнопка вернулась после ошибки", c.eval("document.querySelector('[data-pdf]').innerText") == "📄 Скачать PDF"
          and c.eval("document.querySelector('[data-pdf]').disabled") is False)

    # ---------- без интернета: оболочка из service worker + избранное из кэша ----------
    c.send("Network.emulateNetworkConditions", offline=True, latency=0, downloadThroughput=-1, uploadThroughput=-1)
    c.goto(BASE, 2.5)
    c.wait_for("document.getElementById('main').innerText.length>20", 12)
    check("офлайн: страница открылась", "Что приготовим" in c.eval("document.getElementById('main').innerText"),
          c.eval("document.getElementById('main').innerText")[:120])
    c.eval("document.querySelector('.tab[data-tab=favs]').click()")
    c.wait_for("document.getElementById('main').innerText.includes('Куриное филе')", 8)
    check("офлайн: избранное открывается из кэша", "Куриное филе с киноа" in c.eval("document.getElementById('main').innerText"))
    c.shot(os.path.join(SCR, "ui_offline.png"))
    c.send("Network.emulateNetworkConditions", offline=False, latency=0, downloadThroughput=-1, uploadThroughput=-1)

    errs = [e for e in c.console_problems() if "favicon" not in e.lower() and "Failed to load resource" not in e and "ERR_INTERNET_DISCONNECTED" not in e and "501" not in e]
    check("нет ошибок JS в консоли", not errs, str(errs))
finally:
    c.close()

print("\nИТОГ:", "ВСЁ ОК" if not fails else "ПРОВАЛЕНО: " + ", ".join(fails))
sys.exit(1 if fails else 0)
