"""
Список покупок по ссылке: страница /l/<номер> с галочками и количеством. Её можно переслать кому угодно: отметки общие, открывается
в обычном браузере, без бота, входа и VPN. Настоящие чек-листы Telegram боту недоступны (только от имени бизнес-аккаунта),
а кнопки в пересланном сообщении не работают, поэтому общий список живёт на сайте.
"""
import hashlib
import hmac
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


def create(uid, title: str, items: list, done: list | None = None, saved: bool = False) -> str:
    """Новый список. -> номер для ссылки. ValueError — понятный текст: пустой список или слишком часто.
    saved=True — постоянный список: виден в «Избранном» (только если человек сам дал название)."""
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
    accounts._q("INSERT INTO shop_lists(id, uid, title, items, done, ts, saved) VALUES(?,?,?,?,?,?,?)",
                (lid, str(uid), re.sub(r"\s+", " ", title or "").strip()[:100] or "Список покупок",
                 json.dumps(clean, ensure_ascii=False), json.dumps(marks), now, 1 if saved else 0))
    return lid


_QTY_RE = re.compile(r"^(?P<name>.*?)[\s,:—–-]*(?P<amt>\d+(?:[.,]\d+)?\s*(?:кг|гр|г|мл|л|шт|уп\w*|пач\w*|бут\w*|бан\w*|пуч\w*|кус\w*|десят\w*)?\.?)$",
                     re.IGNORECASE)


def parse_manual(text: str) -> list[dict]:
    """Свой список словами: «молоко 2 л, хлеб, яйца 10 шт» или по строке на продукт (можно голосом). -> [{name, amount}]."""
    out, seen = [], set()
    for part in re.split(r"[\n;]+|,(?![0-9])", text or ""):
        part = re.sub(r"^\s*(?:[-–—•*]+|\d+[.)])\s*", "", part.strip()).strip(" .")      # «1) молоко», «- хлеб» (но не «2 л молока»)
        if not part:
            continue
        m = _QTY_RE.match(part)
        name, amount = (m.group("name").strip(" ,:—–-"), m.group("amt").strip()) if m and m.group("name").strip() else (part, "")
        name = name[:1].upper() + name[1:]
        if name.lower() not in seen:
            seen.add(name.lower())
            out.append({"name": name, "amount": amount})
    return out[:MAX_ITEMS]


def from_text(uid, text: str, title: str = "") -> dict:
    """Свой список покупок (не из рецепта). ValueError — понятный текст. -> {url, title, items, id}"""
    items = parse_manual(text)
    if not items:
        raise ValueError("Напишите хотя бы один продукт: например «молоко, хлеб, яйца 10 шт»")
    given = (title or "").strip()[:80]
    # название дал человек — значит это постоянный список, показываем в «Избранном»
    lid = create(uid, given or "Список покупок", items, saved=bool(given))
    return {"url": url(lid), "title": given or "Список покупок", "items": items, "id": lid}


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


def set_done(lid: str, done: list[int]) -> bool:
    """Отметки из бота переносим на уже отправленную страницу (список тот же)."""
    if not ID_RE.match(lid or ""):
        return False
    with accounts._lock:
        c = accounts._conn()
        try:
            with c:
                r = c.execute("SELECT items, ts FROM shop_lists WHERE id=?", (lid,)).fetchone()
                if not r or time.time() - r[1] > TTL:
                    return False
                n = len(json.loads(r[0]))
                c.execute("UPDATE shop_lists SET done=? WHERE id=?", (json.dumps(sorted({int(i) for i in done if 0 <= int(i) < n})), lid))
                return True
        finally:
            c.close()


def get(lid: str) -> dict | None:
    if not ID_RE.match(lid or ""):
        return None
    r = accounts._q("SELECT title, items, done, ts FROM shop_lists WHERE id=?", (lid,), one=True)
    if not r or time.time() - r[3] > TTL:
        return None
    return {"title": r[0], "items": json.loads(r[1]), "done": json.loads(r[2])}


def last(uid) -> dict | None:
    """Самый свежий список аккаунта — для кнопки «📋 Последний список» на сайте.
    TTL тот же, что у страницы: старый список не открылся бы, поэтому и не предлагаем."""
    r = accounts._q(
        "SELECT id, title, items, done, ts FROM shop_lists WHERE uid=? AND ts > ? "
        "ORDER BY ts DESC, rowid DESC LIMIT 1",
        (str(uid), time.time() - TTL), one=True)
    if not r:
        return None
    items, done = json.loads(r[2]), json.loads(r[3])
    return {"id": r[0], "title": r[1] or "Список покупок", "count": len(items),
            "checked": len(done), "ts": r[4], "url": url(r[0])}


def saved(uid, limit: int = 50) -> list[dict]:
    """Избранное: списки, которым человек сам дал название (saved=1). Разовые и собранные
    из рецептов (saved=0) сюда не попадают, даже если у них есть автоназвание."""
    rows = accounts._q(
        "SELECT id, title, items, done, ts FROM shop_lists "
        "WHERE uid=? AND saved=1 AND ts > ? ORDER BY ts DESC, rowid DESC LIMIT ?",
        (str(uid), time.time() - TTL, limit), all_=True)
    return [{"id": r[0], "title": r[1], "count": len(json.loads(r[2])),
             "checked": len(json.loads(r[3])), "ts": r[4], "url": url(r[0])} for r in rows]


def get_one(lid: str, uid) -> dict | None:
    """Один список для правки: только владельцу и только если не устарел."""
    if not ID_RE.match(lid or ""):
        return None
    r = accounts._q("SELECT title, items, done, ts, uid, saved FROM shop_lists WHERE id=?", (lid,), one=True)
    if not r or str(r[4]) != str(uid) or time.time() - r[3] > TTL:
        return None
    return {"id": lid, "title": r[0] or "", "items": json.loads(r[1]), "done": json.loads(r[2]),
            "ts": r[3], "saved": bool(r[5]), "url": url(lid)}


def update(lid: str, uid, title: str, items: list) -> bool:
    """Название и продукты. Галочки сбрасываем: индексы продуктов изменились.
    ValueError — пустой список. False — не владелец или списка уже нет."""
    if not ID_RE.match(lid or ""):
        return False
    clean = _clean(items)
    if not clean:
        raise ValueError("В списке нет продуктов")
    now = time.time()
    r = accounts._q("SELECT uid FROM shop_lists WHERE id=? AND ts > ?", (lid, now - TTL), one=True)
    if not r or str(r[0]) != str(uid):
        return False
    accounts._q("UPDATE shop_lists SET title=?, items=?, done=?, ts=? WHERE id=?",
                (re.sub(r"\s+", " ", title or "").strip()[:100] or "Список покупок",
                 json.dumps(clean, ensure_ascii=False), json.dumps([]), now, lid))
    return True


def delete(lid: str, uid) -> bool:
    """Удалить список. Только владелец."""
    if not ID_RE.match(lid or ""):
        return False
    r = accounts._q("SELECT uid FROM shop_lists WHERE id=?", (lid,), one=True)
    if not r or str(r[0]) != str(uid):
        return False
    accounts._q("DELETE FROM shop_lists WHERE id=?", (lid,))
    return True


def toggle_saved(lid: str, uid=None) -> bool | None:
    """Переключить флаг «избранное». uid=None — владение уже подтверждено подписью из ссылки.
    ts не трогаем: переключение избранного не должно менять «Последний список»."""
    if not ID_RE.match(lid or ""):
        return None
    with accounts._lock:
        c = accounts._conn()
        try:
            with c:
                r = c.execute("SELECT uid, saved FROM shop_lists WHERE id=?", (lid,)).fetchone()
                if not r or (uid is not None and str(r[0]) != str(uid)):
                    return None
                new_saved = 1 - int(r[1] or 0)
                c.execute("UPDATE shop_lists SET saved=? WHERE id=?", (new_saved, lid))
                return bool(new_saved)
        finally:
            c.close()


def saved_flag(lid: str) -> bool:
    """Только флаг избранного — для отрисовки страницы /l/<id>."""
    if not ID_RE.match(lid or ""):
        return False
    r = accounts._q("SELECT saved FROM shop_lists WHERE id=?", (lid,), one=True)
    return bool(r and r[0])


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


def owner_token(lid: str) -> str:
    """Подпись «этот список открыл владелец» для кнопки на /l/<id>. Страница открывается
    новой вкладкой, заголовок X-Device туда не попадает — владение подтверждаем подписью в адресе.
    Пусто, если WEB_SECRET не настроен: тогда кнопки на странице просто не будет."""
    if not config.WEB_SECRET or not ID_RE.match(lid or ""):
        return ""
    return hmac.new(config.WEB_SECRET.encode(), ("mylist:" + lid).encode(), hashlib.sha256).hexdigest()[:32]


def is_owner_token(lid: str, token: str) -> bool:
    want = owner_token(lid)
    return bool(want) and hmac.compare_digest(want, (token or "").strip())


def owner_url(lid: str) -> str:
    """Ссылка для владельца: с подписью, чтобы на странице была кнопка «Сохранить в избранное»."""
    t = owner_token(lid)
    return url(lid) + ("?k=" + t if t else "")


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
button,.link{display:block;width:100%%;min-height:48px;margin-top:12px;border-radius:14px;border:2px solid var(--primary);background:transparent;color:var(--primary);font-family:inherit;font-weight:600;font-size:16px;line-height:1.2;text-align:center;padding:14px;text-decoration:none}
.note{color:var(--muted);font-size:14px;margin-top:14px}
.acts{display:flex;gap:10px;margin-top:12px}.acts button{margin-top:0}.acts .sq{flex:none;width:56px;padding:0;font-size:22px}
.chips{display:flex;flex-wrap:wrap;gap:8px}.chips button{width:auto;min-height:44px;margin:0;padding:10px 14px;border-width:1px;font-weight:500}.chips button[aria-pressed=true]{background:var(--primary);color:var(--surface)}
.go{color:var(--primary);font-size:15px;white-space:nowrap;text-decoration:none;padding:6px 0 6px 8px}
.promo{margin-top:22px;padding:16px;border-radius:16px;background:var(--surface);border:1px solid var(--line);display:flex;flex-direction:column;gap:6px}.promo span{color:var(--muted);font-size:15px}
</style></head><body><h1>%(title)s</h1><p class="sub" id="count" aria-live="polite"></p><div class="list" id="list"></div>
%(save_btn)s
<div class="acts"><button id="find" type="button" aria-expanded="false">🛒 Найти в магазине</button><button id="share" type="button" class="sq" aria-label="Поделиться списком" title="Поделиться списком">📤</button></div>
<div id="stores" class="stores" hidden><p class="sub" style="margin:10px 0 6px">Где искать? Ссылки появятся у каждого продукта, который ещё не куплен.</p><div class="chips" id="chips"></div></div>
<p class="note">Отметки общие: их видят все, у кого есть ссылка. Список хранится 30 дней.</p>
<div class="promo"><strong>Список сделан в «Кухонном помощнике»</strong><span>Рецепт из ваших продуктов за минуту, с калориями и списком покупок. Начать можно бесплатно, работает без VPN.</span>
<a class="link" href="%(site)s?utm_source=list">🍳 Открыть помощника</a></div>
<script>
(function(){var id=%(id)s,ST=%(stores)s,box=document.getElementById("list"),count=document.getElementById("count"),S=null,busy=0,shop=null;
function q(n){return encodeURIComponent(String(n).replace(/\\([^)]*\\)/g,"").split(",")[0].trim());}
function draw(){var d={};S.done.forEach(function(i){d[i]=1;});box.textContent="";S.items.forEach(function(it,i){var l=document.createElement("label");if(d[i])l.className="done";
var c=document.createElement("input");c.type="checkbox";c.checked=!!d[i];c.setAttribute("data-i",i);var n=document.createElement("span");n.className="n";n.textContent=it.name;l.appendChild(c);l.appendChild(n);
if(it.amount){var a=document.createElement("span");a.className="a";a.textContent=it.amount;l.appendChild(a);}
if(shop&&!d[i]){var g=document.createElement("a");g.className="go";g.href=shop.tpl.replace("{q}",q(it.name));g.target="_blank";g.rel="noopener";g.textContent="найти ›";g.addEventListener("click",function(e){e.stopPropagation();});l.appendChild(g);}box.appendChild(l);});
count.textContent="Куплено "+S.done.length+" из "+S.items.length;}
function load(){if(busy||document.hidden)return;fetch("/api/list/"+id,{cache:"no-store"}).then(function(r){return r.ok?r.json():null;}).then(function(j){if(j&&!busy){S=j;draw();}}).catch(function(){});}
box.addEventListener("change",function(e){var c=e.target,i=+c.getAttribute("data-i"),on=c.checked;busy++;c.parentNode.classList.toggle("done",on);
S.done=S.done.filter(function(x){return x!==i;});if(on)S.done.push(i);count.textContent="Куплено "+S.done.length+" из "+S.items.length;
fetch("/api/list/"+id+"/toggle",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({i:i,on:on})}).then(function(r){if(!r.ok)throw 0;}).catch(function(){c.checked=!on;c.parentNode.classList.toggle("done",!on);alert("Не сохранилось: проверьте интернет");}).then(function(){busy--;});});
var chips=document.getElementById("chips");ST.forEach(function(s){var b=document.createElement("button");b.type="button";b.textContent=s.name;b.setAttribute("aria-pressed","false");
b.addEventListener("click",function(){shop=(shop===s)?null:s;Array.prototype.forEach.call(chips.children,function(x){x.setAttribute("aria-pressed",String(x===b&&!!shop));});if(S)draw();});chips.appendChild(b);});
document.getElementById("find").addEventListener("click",function(){var p=document.getElementById("stores"),open=p.hidden;p.hidden=!open;this.setAttribute("aria-expanded",String(open));if(open&&ST.length===1){chips.firstChild.click();}});
var saveBtn=document.getElementById("save-btn");
if(saveBtn){saveBtn.addEventListener("click",function(){var lid=saveBtn.getAttribute("data-save-lid"),k=saveBtn.getAttribute("data-save-k");saveBtn.disabled=true;
fetch("/api/mylist/"+encodeURIComponent(lid)+"/save?k="+encodeURIComponent(k),{method:"POST"}).then(function(r){if(!r.ok)throw 0;return r.json();}).then(function(j){saveBtn.setAttribute("data-saved",String(j.saved));saveBtn.textContent=j.saved?"💔 Убрать из избранного":"⭐ Сохранить в избранное";saveBtn.disabled=false;}).catch(function(){saveBtn.disabled=false;alert("Не сохранилось: проверьте интернет");});});}
document.getElementById("share").addEventListener("click",function(){var u=location.href,h=u.indexOf("#");if(h>=0)u=u.slice(0,h);u=u.replace(/([?&])k=[^&]*&?/,"$1").replace(/[?&]$/,"");var t=document.title;if(navigator.share){navigator.share({title:t,url:u}).catch(function(){});return;}
if(navigator.clipboard){navigator.clipboard.writeText(u).then(function(){alert("Ссылка скопирована");});return;}prompt("Ссылка на список",u);});
load();setInterval(load,6000);document.addEventListener("visibilitychange",load);})();
</script></body></html>"""


def page(lid: str, rec: dict, can_save: bool = False, saved: bool = False) -> str:
    site = (config.WEB_APP_URL or "https://menu.ymayaka.ru").rstrip("/") + "/"
    from stores.links import STORES, DEFAULT_STORES
    stores = [{"key": k, "name": STORES[k][1], "tpl": STORES[k][2]} for k in DEFAULT_STORES]
    save_btn = ""
    if can_save:
        text = "💔 Убрать из избранного" if saved else "⭐ Сохранить в избранное"
        save_btn = ('<button id="save-btn" type="button" data-save-lid="%s" data-save-k="%s" data-saved="%s">%s</button>'
                    % (escape(lid, quote=True), escape(owner_token(lid), quote=True),
                       "true" if saved else "false", text))
    return _PAGE % {"title": escape(rec["title"] or "Список покупок"), "site": escape(site, quote=True),
                    "id": json.dumps(lid), "save_btn": save_btn,
                    "stores": json.dumps(stores, ensure_ascii=False).replace("</", "<\\/")}


# ---------- «корзина рецептов»: общий список покупок собирается по ходу, одна на бота и сайт ----------

def _combo_key(uid) -> str:
    return f"combo:{uid}"


def combo_get(uid) -> dict:
    """{"ids": [...], "page": n}; рецепты, которых уже нет в избранном, выпадают сами."""
    s = accounts.sess_get(_combo_key(uid)) or {"ids": [], "page": 0}
    have = {f["id"] for f in favorites.get_favorites(uid)}
    s["ids"] = [i for i in s.get("ids", []) if i in have]
    s.setdefault("page", 0)
    return s


def combo_save(uid, s: dict) -> None:
    s["ids"] = list(dict.fromkeys(s.get("ids", [])))[:MAX_RECIPES]
    accounts.sess_set(_combo_key(uid), s)


def combo_add(uid, title: str, text: str, cuisine: str = "") -> tuple[str, int]:
    """Рецепт — в «Любимые» (если ещё нет) и в общий список. Возвращает (id в избранном, сколько рецептов в списке)."""
    s = combo_get(uid)
    if len(s["ids"]) >= MAX_RECIPES:
        raise ValueError(f"В общем списке уже {MAX_RECIPES} рецептов — соберите список или уберите лишние")
    fid = favorites.add_favorite(uid, title, text, cuisine or "")
    if fid not in s["ids"]:
        s["ids"].append(fid)
    combo_save(uid, s)
    return fid, len(s["ids"])


def combo_clear(uid) -> None:
    accounts.sess_set(_combo_key(uid), {"ids": [], "page": 0})
