/* Service worker «Кухонного помощника»: нужен, чтобы сайт устанавливался как приложение и
   открывался без интернета (оболочка + сохранённое избранное из localStorage).
   Динамику (/api, /admin, /go, /uploads) НЕ кэшируем никогда. Главную отдаём из кэша,
   если сеть не ответила за 1.8 с, а свежую версию догружаем в фоне: на медленной связи
   приложение открывается сразу, но после обновления сайта не застревает на старой версии. */
const CACHE = "kitchen-shell-v34";
const SHELL = ["/", "/manifest.webmanifest", "/icons/icon-192.png", "/icons/og-image-2.png"];
const NET_TIMEOUT = 1800;

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
    // офлайн-копия — только главная страница приложения: раньше любая открытая страница (кабинет партнёра со ссылкой-ключом,
    // оплата, чужой список) записывалась вместо неё и показывалась без интернета (аудит 15.11.2026)
    if (url.pathname !== "/") return;
    const cacheP = caches.open(CACHE);
    const net = fetch(req).then((res) => {
      if (!res.ok) throw new Error("HTTP " + res.status);
      cacheP.then((c) => c.put("/", res.clone())).catch(() => {});
      return res;
    });
    e.waitUntil(net.then(() => {}).catch(() => {}));   // даём сети догрузиться, даже если уже отдали кэш
    e.respondWith((async () => {
      const cached = await cacheP.then((c) => c.match("/", { ignoreSearch: true })).catch(() => null);
      if (!cached) return net.catch(() => Response.error());
      const fresh = await Promise.race([
        net.catch(() => null),
        new Promise((resolve) => setTimeout(() => resolve(null), NET_TIMEOUT)),
      ]);
      return fresh || cached;
    })());
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
