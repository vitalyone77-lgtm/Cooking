"""
Список покупок по ссылке: страница /l/<номер> с галочками и количеством. Её можно переслать кому угодно: отметки общие, открывается
в обычном браузере, без бота, входа и VPN. Настоящие чек-листы Telegram боту недоступны (только от имени бизнес-аккаунта),
а кнопки в пересланном сообщении не работают, поэтому общий список живёт на сайте.
"""
import json
import re
import secrets
import time
from html import escape

import accounts
import config
import favorites
import plans
import recipe_math

TTL = 30 * 86400
MAX_ITEMS = 80
MAX_NEW_PER_HOUR = 30
ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,24}$")
MAX_RECIPES = 30


def _clean(items: list) -> list[dict]:
    out = []
    for it in (items or [])[:MAX_ITEMS]:
        name = re.sub(r"\s+", " ", str((it or {}).get("name") or "")).strip()[:80]
        if name:
            out.append({"name": name, "amount": re.sub(r"\s+", " ", str(it.get("amount") or "")).strip()[:24]})
    return out


def create(uid, title: str, items: list, done: list | None = None) -> str:
    """Новый список. -> номер для ссылки. ValueError — понятный текст: пустой список или слишком часто."""
    clean = _clean(items)
    if not clean:
        raise ValueError("В списке нет продуктов")
    now = time.time()
    n = accounts._q("SELECT COUNT(*) FROM shop_lists WHERE uid=? AND ts > ?", (str(uid), now - 3600), one=True)[0]
    if n >= MAX_NEW_PER_HOUR:
        raise ValueError("Слишком много списков за час, подождите немного")
    accounts._q("DELETE FROM shop_lists WHERE ts < ?", (now - TTL,))
    lid = secrets.token_urlsafe(9)
    marks = sorted({int(i) for i in (done or []) if 0 <= int(i) < len(clean)})
    accounts._q("INSERT INTO shop_lists(id, uid, title, items, done, ts) VALUES(?,?,?,?,?,?)",
                (lid, str(uid), re.sub(r"\s+", " ", title or "").strip()[:100] or "Список покупок",
                 json.dumps(clean, ensure_ascii=False), json.dumps(marks), now))
    return lid


def from_favorites(uid, ids: list[str]) -> dict:
    """Один список из нескольких рецептов избранного. То, что уже на полке, не покупаем. ValueError — понятный текст. -> {url, title, items, home}"""
    chosen = [f for f in favorites.get_favorites(uid) if f["id"] in set(ids)][:MAX_RECIPES]
    if not chosen:
        raise ValueError("Отметьте хотя бы один рецепт")
    items = recipe_math.merge_recipes([f["text"] for f in chosen])
    if not items:
        raise ValueError("В этих рецептах не нашёл список продуктов")
    home_idx = accounts.shelf_match(uid, [i["name"] for i in items]) if plans.shelf_on(uid) else set()
    buy = [it for i, it in enumerate(items) if i not in home_idx] or items
    title = chosen[0]["title"][:80] if len(chosen) == 1 else f"Покупки на {len(chosen)} рецепт" + ("а" if len(chosen) < 5 else "ов")
    lid = create(uid, title, buy)
    return {"url": url(lid), "title": title, "items": buy, "home": [it["name"] for i, it in enumerate(items) if i in home_idx]}


def get(lid: str) -> dict | None:
    if not ID_RE.match(lid or ""):
        return None
    r = accounts._q("SELECT title, items, done, ts FROM shop_lists WHERE id=?", (lid,), one=True)
    if not r or time.time() - r[3] > TTL:
        return None
    return {"title": r[0], "items": json.loads(r[1]), "done": json.loads(r[2])}


def toggle(lid: str, index: int, on: bool) -> list[int] | None:
    """Отметить или снять. Запись одним блоком под замком: двое могут отмечать в одном списке одновременно. -> отметки или None."""
    if not ID_RE.match(lid or ""):
        return None
    with accounts._lock:
        c = accounts._conn()
        try:
            with c:
                r = c.execute("SELECT items, done, ts FROM shop_lists WHERE id=?", (lid,)).fetchone()
                if not r or time.time() - r[2] > TTL or not 0 <= index < len(json.loads(r[0])):
                    return None
                marks = set(json.loads(r[1]))
                marks.add(index) if on else marks.discard(index)
                out = sorted(marks)
                c.execute("UPDATE shop_lists SET done=? WHERE id=?", (json.dumps(out), lid))
                return out
        finally:
            c.close()


def url(lid: str) -> str:
    return (config.WEB_APP_URL or "https://menu.ymayaka.ru").rstrip("/") + "/l/" + lid


_PAGE = """<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>%(title)s</title><style>
:root{--bg:#FBF7F2;--surface:#fff;--text:#2B2622;--muted:#6B625A;--line:#E7DED3;--primary:#2F6B4F}
@media (prefers-color-scheme:dark){:root{--bg:#1A1714;--surface:#25211D;--text:#F1EAE2;--muted:#B5AA9E;--line:#3A342E;--primary:#7CC5A0}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:17px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;padding:16px;max-width:560px;margin:0 auto}
h1{font-size:22px;margin:4px 0 2px}.sub{color:var(--muted);font-size:15px;margin:0 0 12px}
.list{background:var(--surface);border:1px solid var(--line);border-radius:16px;overflow:hidden}
label{display:flex;gap:12px;align-items:center;min-height:52px;padding:10px 14px;border-top:1px solid var(--line);cursor:pointer}label:first-child{border-top:0}
input{width:24px;height:24px;flex:none;accent-color:var(--primary)}.n{flex:1}.a{color:var(--muted);white-space:nowrap;font-size:15px}
.done .n,.done .a{opacity:.5;text-decoration:line-through}
button,.link{display:block;width:100%%;min-height:48px;margin-top:12px;border-radius:14px;border:2px solid var(--primary);background:transparent;color:var(--primary);font:600 16px/1 inherit;text-align:center;padding:14px;text-decoration:none}
.note{color:var(--muted);font-size:14px;margin-top:14px}
</style></head><body><h1>%(title)s</h1><p class="sub" id="count" aria-live="polite"></p><div class="list" id="list"></div>
<button id="share" type="button">Поделиться списком</button>
<p class="note">Отметки общие: их видят все, у кого есть ссылка. Список хранится 30 дней. <a href="%(site)s">Кухонный помощник</a></p>
<script>
(function(){var id=%(id)s,box=document.getElementById("list"),count=document.getElementById("count"),S=null,busy=0;
function draw(){var d={};S.done.forEach(function(i){d[i]=1;});box.textContent="";S.items.forEach(function(it,i){var l=document.createElement("label");if(d[i])l.className="done";
var c=document.createElement("input");c.type="checkbox";c.checked=!!d[i];c.setAttribute("data-i",i);var n=document.createElement("span");n.className="n";n.textContent=it.name;l.appendChild(c);l.appendChild(n);
if(it.amount){var a=document.createElement("span");a.className="a";a.textContent=it.amount;l.appendChild(a);}box.appendChild(l);});
count.textContent="Куплено "+S.done.length+" из "+S.items.length;}
function load(){if(busy||document.hidden)return;fetch("/api/list/"+id,{cache:"no-store"}).then(function(r){return r.ok?r.json():null;}).then(function(j){if(j&&!busy){S=j;draw();}}).catch(function(){});}
box.addEventListener("change",function(e){var c=e.target,i=+c.getAttribute("data-i"),on=c.checked;busy++;c.parentNode.classList.toggle("done",on);
S.done=S.done.filter(function(x){return x!==i;});if(on)S.done.push(i);count.textContent="Куплено "+S.done.length+" из "+S.items.length;
fetch("/api/list/"+id+"/toggle",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({i:i,on:on})}).then(function(r){if(!r.ok)throw 0;}).catch(function(){c.checked=!on;c.parentNode.classList.toggle("done",!on);alert("Не сохранилось: проверьте интернет");}).then(function(){busy--;});});
document.getElementById("share").addEventListener("click",function(){var u=location.href,t=document.title;if(navigator.share){navigator.share({title:t,url:u}).catch(function(){});return;}
if(navigator.clipboard){navigator.clipboard.writeText(u).then(function(){alert("Ссылка скопирована");});return;}prompt("Ссылка на список",u);});
load();setInterval(load,6000);document.addEventListener("visibilitychange",load);})();
</script></body></html>"""


def page(lid: str, rec: dict) -> str:
    site = (config.WEB_APP_URL or "https://menu.ymayaka.ru").rstrip("/") + "/"
    return _PAGE % {"title": escape(rec["title"]), "site": escape(site, quote=True), "id": json.dumps(lid)}
