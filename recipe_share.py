"""
Рецепт по ссылке /rc/<номер>: отправить другу целиком. Продукты с количеством и галочками, кнопка магазина превращает их в ссылки на поиск,
пересчёт порций, шаги с галочками и таймерами. Открывается в любом браузере, без бота, входа и VPN.
Ссылка живёт 30 дней. Отметки галочек хранятся у каждого в браузере, рецепт можно сохранить в избранное на сайте или в боте.
"""
import json
import re
import secrets
import time
from html import escape

import accounts
import config
import recipe_math
import timers
from stores.links import DEFAULT_STORES, search_url, short_name

TTL = 30 * 86400
MAX_NEW_PER_HOUR = 30
ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,24}$")
_BULLET = re.compile(r"^\s*[-•–*]\s+")
_MD = re.compile(r"[*_`]")
_STEP = re.compile(r"^\s*(\d+)[.)]\s+(.*)")


def _site() -> str:
    return (config.WEB_APP_URL or "https://menu.ymayaka.ru").rstrip("/")


def url(rid: str) -> str:
    return f"{_site()}/rc/{rid}"


def _title(text: str) -> str:
    for ln in (text or "").splitlines():
        if "🍽" in ln:
            return _MD.sub("", ln.replace("🍽", "")).strip()[:100]
    return ""


def create(uid, title: str, text: str) -> str:
    """Новая ссылка на рецепт (тот же рецепт тем же человеком — та же ссылка). ValueError — понятный текст."""
    text = (text or "").strip()[:12000]
    data = parse(text)
    if not data["steps"] and not data["products"]:
        raise ValueError("В этом тексте не нашёл рецепт")
    same = accounts._q("SELECT id FROM shared_recipes WHERE uid=? AND text=? AND ts>?", (str(uid), text, time.time() - TTL), one=True)
    if same:
        return same[0]
    if accounts._q("SELECT COUNT(*) FROM shared_recipes WHERE uid=? AND ts>?", (str(uid), time.time() - 3600), one=True)[0] >= MAX_NEW_PER_HOUR:
        raise ValueError("Слишком много ссылок за час, подождите немного")
    accounts._q("DELETE FROM shared_recipes WHERE ts<?", (time.time() - TTL,))
    rid = secrets.token_urlsafe(9)
    accounts._q("INSERT INTO shared_recipes(id, uid, title, text, ts) VALUES(?,?,?,?,?)",
                (rid, str(uid), (title or data["title"] or "Рецепт")[:100], text, time.time()))
    return rid


def get(rid: str) -> dict | None:
    if not ID_RE.match(rid or ""):
        return None
    r = accounts._q("SELECT title, text, ts FROM shared_recipes WHERE id=?", (rid,), one=True)
    if not r or time.time() - r[2] > TTL:
        return None
    return {"title": r[0], "text": r[1]}


def parse(text: str) -> dict:
    """Рецепт для страницы: заголовок, строки сведений, продукты (название, количество, поиск в магазинах), шаги с таймерами, советы."""
    lines = (text or "").splitlines()
    out = {"title": _title(text), "info": [], "products": [], "steps": [], "tips": [], "servings": recipe_math.base_servings(text)}
    section = "head"
    for ln in lines:
        s = _MD.sub("", ln).strip()
        low = s.lower().rstrip(":")
        if not s or "🍽" in s or s.startswith(("ПРОДУКТЫ_СПИСОК", "ℹ️")):
            continue
        if low in ("продукты", "ингредиенты"):
            section = "products"
            continue
        if low.startswith("инструкция"):
            section = "steps"
            continue
        if s.startswith("💡") or low.startswith("совет"):
            section = "tips"
        if section == "products" and _BULLET.match(ln):
            item = _BULLET.sub("", _MD.sub("", ln)).strip()
            name, sep, amount = item.partition(" — ")
            if not sep:
                name, sep, amount = item.partition(" - ")
            out["products"].append({"name": name.strip(), "amount": amount.strip()})
        elif section == "steps":
            m = _STEP.match(s)
            if m:
                out["steps"].append({"text": m.group(2).strip(), "timers": timers.parse_times(m.group(2))})
            elif out["steps"] and ln.startswith((" ", "\t")):
                out["steps"][-1]["text"] += " " + s
        elif section == "tips":
            out["tips"].append(s)
        elif section == "head":
            out["info"].append(s)
    for p in out["products"]:                       # поиск в магазине по чистому названию: без скобок, «соль, перец» — по первому
        q = re.sub(r"\([^)]*\)", "", p["name"]).split(",")[0].strip() or p["name"]
        p["links"] = {short_name(st): search_url(st, q) for st in DEFAULT_STORES}
    out["stores"] = [short_name(st) for st in DEFAULT_STORES]
    return out


_PAGE = """<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>%(title)s</title>
<meta property="og:type" content="article"><meta property="og:title" content="%(title)s"><meta property="og:description" content="%(desc)s">
<meta property="og:image" content="%(img)s"><meta property="og:image:width" content="1200"><meta property="og:image:height" content="630"><meta name="twitter:card" content="summary_large_image">
<style>
:root{--bg:#FBF7F2;--surface:#fff;--text:#2B2622;--muted:#6B625A;--line:#E7DED3;--primary:#2F6B4F;--soft:#E8F1EC;--on:#fff}
@media (prefers-color-scheme:dark){:root{--bg:#1A1714;--surface:#25211D;--text:#F1EAE2;--muted:#B5AA9E;--line:#3A342E;--primary:#7CC5A0;--soft:#24342C;--on:#10261B}}
*{box-sizing:border-box}body{margin:0 auto;max-width:640px;padding:16px;background:var(--bg);color:var(--text);font:17px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif}
h1{font-size:24px;line-height:1.25;margin:6px 0 8px}h2{font-size:19px;margin:22px 0 8px}.sub{color:var(--muted);font-size:15px;margin:2px 0}
img.card{width:100%%;border-radius:16px;display:block;margin:6px 0 10px}
.box{background:var(--surface);border:1px solid var(--line);border-radius:16px;overflow:hidden}
.row{display:flex;gap:12px;align-items:flex-start;padding:12px 14px;border-top:1px solid var(--line)}.row:first-child{border-top:0}
.row input{width:24px;height:24px;flex:none;margin-top:1px;accent-color:var(--primary)}.row .t{flex:1}.row .a{color:var(--muted);white-space:nowrap;font-size:15px}
.done .t,.done .a{opacity:.5;text-decoration:line-through}.row a{color:var(--primary)}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}.chip{min-height:40px;padding:0 14px;border-radius:99px;border:1.5px solid var(--line);background:var(--surface);color:var(--text);font:inherit;font-size:15px}
.chip[aria-pressed=true]{background:var(--primary);border-color:var(--primary);color:var(--on)}
.tm{margin-top:8px;min-height:40px;padding:0 14px;border-radius:12px;border:1.5px solid var(--primary);background:transparent;color:var(--primary);font:600 15px/1 inherit}
.tm.run{background:var(--soft)}.tm.end{background:var(--primary);color:var(--on)}
.btn{display:block;width:100%%;min-height:48px;margin-top:14px;border-radius:14px;border:2px solid var(--primary);background:transparent;color:var(--primary);font:600 16px/1 inherit;text-align:center;padding:14px;text-decoration:none}
.promo{margin-top:22px;padding:16px;border-radius:16px;background:var(--surface);border:1px solid var(--line);display:flex;flex-direction:column;gap:6px}.promo span{color:var(--muted);font-size:15px}
.note{color:var(--muted);font-size:14px;margin-top:14px}
</style></head><body>
<img class="card" src="%(img)s" alt="">
<h1 id="ttl"></h1><div id="info"></div>
<h2>Продукты</h2><div class="chips" id="srv"></div><div class="box" id="prod"></div>
<p class="sub" style="margin-top:10px">Где купить:</p><div class="chips" id="st"></div>
<h2>Как готовить</h2><div class="box" id="steps"></div><div id="tips"></div>
<a class="btn" href="%(site)s/?rc=%(rid)s" style="background:var(--primary);color:var(--on)">⭐ Сохранить в избранное</a>
<p class="sub" style="text-align:center;margin-top:6px">Откроется «Кухонный помощник», рецепт будет в «Любимых». Есть Telegram? <a href="https://t.me/%(bot)s?start=rc_%(rid)s">Сохранить в боте</a></p>
<button class="btn" id="share" type="button">📤 Поделиться рецептом</button>
<p class="note">Рецепт составил ИИ: проверяйте готовность мяса, птицы, рыбы и яиц, а при аллергии сверяйте состав. Галочки видны только вам. Ссылка работает 30 дней: чтобы рецепт не пропал, сохраните его в избранное.</p>
<div class="promo"><strong>Рецепт из «Кухонного помощника»</strong><span>Рецепт из ваших продуктов за минуту, с калориями, списком покупок и таймерами. Начать можно бесплатно, работает без VPN.</span>
<a class="btn" style="margin-top:6px" href="%(site)s/?utm_source=recipe">🍳 Открыть помощника</a></div>
<script>
(function(){
var D=%(data)s,id=%(id)s,store="",key="rc:"+id;
function esc(s){return String(s).replace(/[&<>"]/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;","\\"":"&quot;"}[c];});}
var S={};try{S=JSON.parse(localStorage.getItem(key)||"{}");}catch(e){}
function save(){try{localStorage.setItem(key,JSON.stringify(S));}catch(e){}}
document.getElementById("ttl").textContent=D.title;
document.getElementById("info").innerHTML=D.info.map(function(l){return '<p class="sub">'+esc(l)+'</p>';}).join("");
function drawProd(){
  document.getElementById("prod").innerHTML=D.products.map(function(p,i){var on=S["p"+i];var nm=store&&p.links[store]?'<a href="'+esc(p.links[store])+'" target="_blank" rel="noopener">'+esc(p.name)+'</a>':esc(p.name);
    return '<label class="row'+(on?" done":"")+'"><input type="checkbox" data-p="'+i+'"'+(on?" checked":"")+'><span class="t">'+nm+'</span><span class="a">'+esc(p.amount||"")+'</span></label>';}).join("");
}
function drawStores(){document.getElementById("st").innerHTML=D.stores.map(function(s){return '<button class="chip" type="button" data-st="'+esc(s)+'" aria-pressed="'+(s===store)+'">'+esc(s)+'</button>';}).join("");}
function drawSrv(){
  if(!D.servings){document.getElementById("srv").innerHTML="";return;}
  var opts=[1,2,3,4,6];if(opts.indexOf(D.servings)<0){opts.push(D.servings);opts.sort(function(a,b){return a-b;});}
  document.getElementById("srv").innerHTML='<span class="sub" style="align-self:center">Порций:</span>'+opts.map(function(n){return '<button class="chip" type="button" data-n="'+n+'" aria-pressed="'+(n===D.servings)+'">'+n+'</button>';}).join("");
}
function lbl(t){return t[0]===t[1]?t[0]+" мин":t[0]+"–"+t[1]+" мин";}
document.getElementById("steps").innerHTML=D.steps.map(function(s,i){var on=S["s"+i];
  return '<div class="row'+(on?" done":"")+'"><input type="checkbox" data-s="'+i+'"'+(on?" checked":"")+' aria-label="Шаг '+(i+1)+' готов"><div class="t">'+(i+1)+'. '+esc(s.text)+
    (s.timers.length?'<div>'+s.timers.map(function(t){return '<button class="tm" type="button" data-tm="'+t[0]+'-'+t[1]+'" data-step="'+(i+1)+'">⏱ '+lbl(t)+'</button>';}).join(" ")+'</div>':'')+'</div></div>';}).join("");
document.getElementById("tips").innerHTML=D.tips.map(function(t){return '<p class="sub" style="margin-top:12px">'+esc(t)+'</p>';}).join("");
drawProd();drawStores();drawSrv();
document.addEventListener("change",function(e){var c=e.target,p=c.getAttribute("data-p"),s=c.getAttribute("data-s"),k=p!==null?"p"+p:s!==null?"s"+s:null;if(!k)return;
  S[k]=c.checked;save();c.closest(".row").classList.toggle("done",c.checked);});
var AC=null;
function beep(){try{AC=AC||new (window.AudioContext||window.webkitAudioContext)();[0,0.35,0.7].forEach(function(t){var o=AC.createOscillator(),g=AC.createGain();o.frequency.value=880;o.connect(g);g.connect(AC.destination);g.gain.value=0.2;o.start(AC.currentTime+t);o.stop(AC.currentTime+t+0.2);});}catch(e){}
  try{navigator.vibrate&&navigator.vibrate([300,150,300,150,300]);}catch(e){}}
document.addEventListener("click",function(e){
  var b=e.target.closest&&e.target.closest("button");if(!b)return;
  if(b.hasAttribute("data-st")){var s=b.getAttribute("data-st");store=store===s?"":s;drawStores();drawProd();return;}
  if(b.hasAttribute("data-n")){var n=+b.getAttribute("data-n");if(n===D.servings)return;b.disabled=true;
    fetch(location.pathname+"/data?to="+n).then(function(r){return r.json();}).then(function(j){D.products=j.products;D.servings=j.servings;drawProd();drawSrv();}).catch(function(){b.disabled=false;alert("Не получилось, проверьте интернет");});return;}
  if(b.hasAttribute("data-tm")){
    if(b._t){clearInterval(b._t);b._t=null;b.className="tm";b.textContent=b._l;return;}
    try{AC=AC||new (window.AudioContext||window.webkitAudioContext)();AC.resume&&AC.resume();}catch(x){}
    var mm=b.getAttribute("data-tm").split("-"),mn=+mm[0],mx=+mm[1],st=Date.now(),step=b.getAttribute("data-step");b._l=b._l||b.textContent;b.className="tm run";
    b._t=setInterval(function(){var left=Math.round(mx*60-(Date.now()-st)/1000),firstAt=mn*60-(Date.now()-st)/1000;
      if(left<=0){clearInterval(b._t);b._t=null;b.className="tm end";b.textContent="✅ Шаг "+step+": время вышло";beep();return;}
      if(mx>mn&&firstAt<=0&&!b._half){b._half=1;beep();}
      b.textContent="⏱ "+Math.floor(left/60)+":"+("0"+left%%60).slice(-2)+" · нажмите, чтобы остановить";},500);return;}
  if(b.id==="share"){var u=location.href;if(navigator.share){navigator.share({title:D.title,url:u}).catch(function(){});return;}
    if(navigator.clipboard){navigator.clipboard.writeText(u).then(function(){alert("Ссылка скопирована");});return;}prompt("Ссылка на рецепт",u);}
});
})();
</script></body></html>"""


def page(rid: str, rec: dict) -> str:
    d = parse(rec["text"])
    d["title"] = d["title"] or rec["title"]
    desc = " · ".join(d["info"][:2])[:200] or "Рецепт с продуктами, шагами и таймерами"
    return _PAGE % {"title": escape(d["title"]), "desc": escape(desc, quote=True), "img": escape(f"{_site()}/rc/{rid}.png", quote=True),
                    "site": escape(_site(), quote=True), "id": json.dumps(rid), "rid": escape(rid, quote=True),
                    "bot": escape(config.BOT_USERNAME or "Cookingvkusno_bot", quote=True), "data": json.dumps(d, ensure_ascii=False).replace("</", "<\\/")}


def data(rec: dict, to: int | None = None) -> dict:
    text = recipe_math.scale_text(rec["text"], to) if to else rec["text"]
    d = parse(text)
    return {"products": d["products"], "servings": d["servings"]}
