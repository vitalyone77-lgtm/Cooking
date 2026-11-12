"""Ожидание рецепта на сайте (JS из web/index.html): сорвавшийся опрос и перезапуск сервера не превращаются в ошибку,
запущенная генерация переживает перезагрузку страницы, ошибки ИИ показываются. Нужен node."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.environ.get("PROJ") or os.path.dirname(HERE)
if not os.path.exists(os.path.join(PROJ, "web", "index.html")):
    PROJ = "/tmp/ct"

fails = []


def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{x}]"))
    if not c:
        fails.append(n)


node = shutil.which("node")
if not node:
    print("PASS нет node: проверка пропущена")
    sys.exit(0)
html = open(os.path.join(PROJ, "web", "index.html"), encoding="utf-8").read()
block = re.search(r"(  var JOB_MAX=.*?)\n  function finishJob", html, re.S).group(1)

STUBS = r"""
var store={}, main={innerHTML:""}, toasts=[];
function ls(k,v){if(v===undefined)return store[k]===undefined?null:store[k];store[k]=v;}
function esc(s){return s;}
function toast(t){toasts.push(t);}
function sleep(){return Promise.resolve();}
var script=[];   // ответы на опрос по очереди: "net" — обрыв, "502", "404", {status:...}
function call(m,p){var a=script.shift();
  if(a==="net")return Promise.reject(new TypeError("Связь очень медленная"));
  if(a==="502"){var e=new Error("Ошибка 502");e.status=502;return Promise.reject(e);}
  if(a==="404"){var e2=new Error("Задача не найдена");e2.status=404;return Promise.reject(e2);}
  return Promise.resolve(a);}
"""
SCEN = r"""
async function run(name, steps, mode){
  script=steps.slice(); store={};
  try{var r=await runJob(Promise.resolve({job:"J1"}),"…",mode);return {name:name,ok:r,pend:store.pendjob||""};}
  catch(e){return {name:name,err:e.message,pend:store.pendjob||""};}
}
(async function(){
  var out=[];
  out.push(await run("blips",["net","502",{status:"running"},"net",{status:"done",result:"РЕЦЕПТ"}],"recipe"));
  out.push(await run("ai_error",[{status:"running"},{status:"error",error:"ИИ сейчас не ответил"}],"recipe"));
  out.push(await run("lost",["404"],"recipe"));
  script=[{status:"running"}]; store={};
  var p=runJob(Promise.resolve({job:"J2"}),"…","day"); await Promise.resolve();
  var saved=JSON.parse(store.pendjob||"null");
  out.push({name:"pending_saved",ok:saved&&saved.job==="J2"&&saved.mode==="day"});
  console.log(JSON.stringify(out));
})();
"""
with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
    f.write(STUBS + block + SCEN)
res = subprocess.run([node, f.name], capture_output=True, text=True, encoding="utf-8")
os.unlink(f.name)
try:
    out = {o["name"]: o for o in json.loads(res.stdout.strip().splitlines()[-1])}
except Exception:  # noqa: BLE001
    out = {}
    check("скрипт ожидания запускается", False, res.stderr[:400])
if out:
    b = out["blips"]
    check("обрывы связи и перезапуск сервера во время ожидания — рецепт всё равно приходит", b.get("ok") == "РЕЦЕПТ", str(b))
    check("после результата отметка о незаконченной задаче стёрта", b.get("pend") == "")
    a = out["ai_error"]
    check("ошибка ИИ показывается человеку и не остаётся «висеть»", a.get("err") == "ИИ сейчас не ответил" and a.get("pend") == "", str(a))
    lo = out["lost"]
    check("задача пропала на сервере — понятная ошибка, без вечного ожидания", lo.get("err") == "Задача не найдена" and lo.get("pend") == "", str(lo))
    check("запущенная генерация запоминается (переживёт перезагрузку)", out["pending_saved"].get("ok") is True)
check("после перезагрузки сайт продолжает ожидание", "resumePending();" in html and "function resumePending" in html)

print("ИТОГО: упало", len(fails))
sys.exit(1 if fails else 0)
