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

async function networkFirst(req, cacheKey) {
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
  if (url.origin !== self.location.origin) return;
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

/* ── Web Push: real OS notifications for follow-up reminders ─────────────────
 * Best-effort / soft feature: the in-app Notification path (app.ts
 * maybeNotifyFollowUps) keeps working exactly as it does today regardless of
 * whether push is subscribed, supported, or permitted — this only ADDS a
 * delivery path for when the app is fully closed (the normal iOS PWA state).
 * A malformed/missing payload never throws past this handler; it always falls
 * back to a generic notification rather than silently doing nothing, since a
 * push event with no notification shown can get the browser to unsubscribe it.
 */
self.addEventListener("push", (e) => {
  let data = {};
  try {
    if (e.data) data = e.data.json();
  } catch {
    data = { title: "Time to follow up", body: e.data ? e.data.text() : "" };
  }
  const title = data.title || "Time to follow up";
  const base = self.registration.scope; // e.g. https://.../dsm-jobs/
  const options = {
    body: data.body || "A follow-up reminder is due.",
    icon: `${base}icon-192.png`,
    badge: `${base}icon-192.png`,
    tag: data.tag || "followup",
    data: { jobId: data.jobId || null, url: `${base}?view=apps` },
  };
  e.waitUntil(self.registration.showNotification(title, options));
});

/* Tapping the notification focuses an already-open same-origin tab (so state
 * isn't lost) or opens a new one to the apps tab, landing her on the follow-up
 * that's due. Never navigates a client to another origin — the fallback open
 * target is always this SW's own registration scope. */
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const targetUrl = (e.notification.data && e.notification.data.url) || self.registration.scope;
  const scopeOrigin = new URL(self.registration.scope).origin;
  e.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
      for (const client of list) {
        if (new URL(client.url).origin === scopeOrigin && "focus" in client) {
          if ("navigate" in client) {
            try { client.navigate(targetUrl); } catch { /* ignore */ }
          }
          return client.focus();
        }
      }
      if (self.clients.openWindow) return self.clients.openWindow(targetUrl);
    }),
  );
});
