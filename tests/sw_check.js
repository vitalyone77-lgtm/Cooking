/* Поведение web/sw.js без браузера: кэш отдаётся через 1.8 с, если сеть молчит;
   быстрая сеть побеждает кэш; сеть догружается в фон и обновляет кэш. */
const fs = require("fs");
const vm = require("vm");
const path = require("path");

const src = fs.readFileSync(path.join(__dirname, "..", "web", "sw.js"), "utf8");
let fails = 0;

function check(name, ok, extra) {
  console.log((ok ? "PASS " : "FAIL ") + name + (ok ? "" : "  [" + (extra || "") + "]"));
  if (!ok) fails++;
}

function makeEnv(opts) {
  const handlers = {};
  const puts = [];
  const cache = {
    match: async () => opts.cached,
    put: async (key) => { puts.push(key); },
    addAll: async () => {},
  };
  const caches = {
    open: async () => cache,
    keys: async () => ["kitchen-shell-v34"],
    delete: async () => {},
    match: async () => opts.cached,
  };
  const sandbox = {
    self: {
      addEventListener: (n, f) => { handlers[n] = f; },
      skipWaiting: async () => {},
      clients: { claim: async () => {} },
      location: { origin: "https://menu.ymayaka.ru" },
    },
    caches,
    fetch: () => opts.net(),
    location: { origin: "https://menu.ymayaka.ru" },
    URL,
    Response,
    Promise,
    setTimeout,
    console,
  };
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox);
  return { handlers, puts };
}

function navEvent() {
  const e = {
    request: { method: "GET", mode: "navigate", url: "https://menu.ymayaka.ru/?source=pwa" },
    response: null,
    waits: [],
  };
  e.respondWith = (p) => { e.response = p; };
  e.waitUntil = (p) => { e.waits.push(p); };
  return e;
}

async function run(name, opts, expectText, timeoutMs) {
  const env = makeEnv(opts);
  const e = navEvent();
  env.handlers.fetch(e);
  const started = Date.now();
  const res = await Promise.race([
    e.response,
    new Promise((r) => setTimeout(() => r(null), timeoutMs || 4000)),
  ]);
  const ms = Date.now() - started;
  const text = res ? await res.text() : "";
  check(name, text === expectText, "получено «" + text + "» за " + ms + " мс");
  return { ms, env, waits: e.waits.length };
}

(async () => {
  const cached = () => new Response("cached", { status: 200 });
  const net = (txt, delay) => () => new Promise((resolve, reject) => {
    setTimeout(() => (txt === null ? reject(new Error("нет сети")) : resolve(new Response(txt, { status: 200 }))), delay);
  });

  // 1) сеть молчит: через ~1.8 с отдаём кэш, запрос продолжается в фоне
  const a = await run("SW: молчащая сеть — кэш за ~1.8 с", { cached: cached(), net: () => new Promise(() => {}) }, "cached", 5000);
  check("SW: кэш отдан не раньше таймаута", a.ms >= 1700 && a.ms < 2600, a.ms + " мс");
  check("SW: сеть оставлена догружаться в фоне", a.waits >= 1, String(a.waits));

  // 2) быстрая сеть: свежий ответ важнее кэша, кэш обновляется
  const b = await run("SW: быстрая сеть — свежий HTML", { cached: cached(), net: net("fresh", 100) }, "fresh", 5000);
  check("SW: свежий ответ обновил кэш", b.env.puts.indexOf("/") >= 0, JSON.stringify(b.env.puts));
  check("SW: свежий ответ пришёл раньше таймаута", b.ms < 1000, b.ms + " мс");

  // 3) сеть упала, кэш есть — отдаём кэш
  await run("SW: сеть упала — отдаём кэш", { cached: cached(), net: net(null, 50) }, "cached");

  // 4) кэша нет, сеть упала — ошибка сети, а не пустая страница
  const env = makeEnv({ cached: undefined, net: net(null, 50) });
  const e = navEvent();
  env.handlers.fetch(e);
  const res = await e.response.catch(() => null);
  check("SW: нет кэша и сети — ответ-ошибка", !!res && res.type === "error", res && res.type);

  console.log("Провалов:", fails);
  process.exit(fails ? 1 : 0);
})();
