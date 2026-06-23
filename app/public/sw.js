/* PWA shell — network-first page + feed JSON; cache-first static assets. */
const CACHE = "myjobs-v5-goth";
const SHELL = [
  "./", "./index.html", "./manifest.webmanifest",
  "./icon-192.png", "./icon-512.png", "./apple-touch-icon.png",
  "./rudy.jpg", "./portal.json",
];
const FEED_PATHS = new Set(["jobs.json", "meta.json"]);

function isFeedRequest(url) {
  const leaf = url.pathname.split("/").pop() || "";
  return FEED_PATHS.has(leaf);
}

function networkFirst(req, cacheKey) {
  return fetch(req)
    .then((res) => {
      if (res.ok) {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(cacheKey || req, copy)).catch(() => {});
      }
      return res;
    })
    .catch(() => caches.match(cacheKey || req));
}

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  const isPage = req.mode === "navigate" || url.pathname.endsWith("index.html") || url.pathname.endsWith("/");
  if (isPage) {
    e.respondWith(
      networkFirst(req, "./index.html").then((res) => {
        if (res && res.ok) return res;
        return caches.match("./index.html").then((cached) => cached || res);
      }),
    );
  } else if (isFeedRequest(url)) {
    e.respondWith(networkFirst(req));
  } else {
    e.respondWith(
      caches.match(req).then((cached) =>
        cached || fetch(req).then((res) => {
          if (res.ok) {
            const copy = res.clone();
            caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
          }
          return res;
        }).catch(() => cached),
      ),
    );
  }
});
