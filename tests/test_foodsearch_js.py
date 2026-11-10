"""Поиск продуктов в телефоне (JS из web/index.html) находит то же, что поиск на сервере, и так же отделяет количество. Нужен node."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.environ.get("PROJ") or os.path.dirname(HERE)
if not os.path.exists(os.path.join(PROJ, "foods.py")):
    PROJ = "/tmp/ct"
sys.path.insert(0, PROJ)
import foods

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
block = re.search(r"/\*FOODSEARCH\*/(.*?)/\*/FOODSEARCH\*/", html, re.S).group(1)
cat = {"v": foods.CATALOG_VERSION, "stop": sorted(foods._STOP), "items": [{"id": f["id"], "name": f["name"], "t": f["tokens"]} for f in foods.FOODS]}
queries = ["яйца", "2 яйца", "хлеб", "хлеб 2шт", "бутерброд", "курица", "куриная грудка", "суп из курицы", "гречка", "гречка 3 ложки",
           "овсянка 3 ложки с горкой", "пол банана", "две котлеты", "молоко 2,5%", "картошка", "борщ", "кофе с молоком", "салат", "сыр", "рыба красная"]
js = block + "\nFCAT=fPrep(" + json.dumps(cat, ensure_ascii=False) + ");\nvar Q=" + json.dumps(queries, ensure_ascii=False) + ";\n" + \
    "console.log(JSON.stringify(Q.map(function(q){var s=fSplit(q);return {q:q,split:s,ids:(fSearch(s[0])||[]).map(function(f){return f.id;})};})));"
with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
    f.write(js)
out = json.loads(subprocess.run([node, f.name], capture_output=True, text=True, encoding="utf-8").stdout)
os.unlink(f.name)
for r in out:
    rest, qty = foods.split_qty(r["q"])
    py = [x["id"] for x in foods.search(rest)]
    check(f"«{r['q']}»: количество отделено так же", r["split"] == [rest, qty], f"{r['split']} / {[rest, qty]}")
    check(f"«{r['q']}»: найдено то же, что на сервере", r["ids"] == py[:10] or (not r["ids"] and not py), f"{r['ids'][:5]} / {py[:5]}")
print("ИТОГО: упало", len(fails))
sys.exit(1 if fails else 0)
