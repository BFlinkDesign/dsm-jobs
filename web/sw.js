/* Service worker: app shell offline + network-first for fresh jobs. */
const CACHE = "myjobs-v3";   // bumped for the editorial-goth (oxblood) shell + RUDY mascot
const SHELL = [
  "./", "./index.html", "./manifest.webmanifest",
  "./icon-192.png", "./icon-512.png", "./apple-touch-icon.png", "./rudy.jpg",
];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const isPage = req.mode === "navigate" || req.url.endsWith("index.html") || req.url.endsWith("/");
  if (isPage) {
    // Network-first so jobs are fresh; fall back to cached page offline.
    // Only res.ok responses may be cached: a transient 404/500 must never
    // replace the app shell (it would persist even offline).
    e.respondWith(
      fetch(req)
        .then((res) => {
          if (res.ok) {
            const copy = res.clone();
            caches.open(CACHE).then((c) => c.put("./index.html", copy)).catch(() => {});
            return res;
          }
          return caches.match("./index.html").then((cached) => cached || res);
        })
        .catch(() => caches.match("./index.html"))
    );
  } else {
    // Cache-first for static assets (icons, fonts).
    e.respondWith(
      caches.match(req).then((cached) =>
        cached ||
        fetch(req).then((res) => {
          if (res.ok) {
            const copy = res.clone();
            caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
          }
          return res;
        }).catch(() => cached)
      )
    );
  }
});
