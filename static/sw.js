/* CTone service worker: makes the app installable and shows a friendly page when offline.
   Pages and /api data are ALWAYS fetched live (money data must never be stale). */
const V = "ctone-v2";
const SHELL = ["/offline", "/static/style.css", "/static/common.js", "/static/theme.js", "/static/pwa.js", "/static/icon-192.png"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(V).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== V).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});

self.addEventListener("fetch", e => {
  const req = e.request, url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.startsWith("/api/")) return;                       // live data: network only
  if (req.mode === "navigate") {                                       // pages: network, else offline screen
    e.respondWith(fetch(req).catch(() => caches.match("/offline")));
    return;
  }
  if (url.pathname.startsWith("/static/")) {                           // css/js/icons: network first, cache as backup
    e.respondWith(fetch(req).then(r => {
      const copy = r.clone(); caches.open(V).then(c => c.put(req, copy)); return r;
    }).catch(() => caches.match(req)));
  }
});
