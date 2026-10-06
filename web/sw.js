/* Service worker «Кухонного помощника»: нужен, чтобы сайт устанавливался как приложение и
   открывался без интернета (оболочка + сохранённое избранное из localStorage).
   Динамику (/api, /admin, /go, /uploads) НЕ кэшируем никогда. Страницу берём из сети, из кэша — только
   когда сети нет, поэтому после обновления сайта пользователь не застревает на старой версии. */
const CACHE = "kitchen-shell-v2";
const SHELL = ["/", "/manifest.webmanifest", "/icons/icon-192.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;
  if (/^\/(api|admin|go|uploads)(\/|$)/.test(url.pathname)) return;

  if (req.mode === "navigate") {
    e.respondWith(
      fetch(req)
        .then((res) => {
          if (res.ok) { const copy = res.clone(); caches.open(CACHE).then((c) => c.put("/", copy)); }
          return res;
        })
        .catch(() => caches.match("/"))
    );
    return;
  }

  e.respondWith(
    caches.match(req).then((hit) => {
      const net = fetch(req)
        .then((res) => { if (res.ok) { const copy = res.clone(); caches.open(CACHE).then((c) => c.put(req, copy)); } return res; })
        .catch(() => hit);
      return hit || net;
    })
  );
});
