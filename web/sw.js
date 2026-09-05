/**
 * opencloud-docserver — Service Worker (PWA offline support)
 *
 * Strategy (Stoic: minimal, honest, no magic):
 *   - install():  precache the static app shell (tolerant of failures —
 *                 the page is still fully usable once visited online).
 *   - activate(): drop caches from older versions, claim existing clients.
 *   - fetch():
 *       navigations         -> network-first  (cache page after first visit;
 *                             offline fallback page as last resort)
 *       /static/*           -> stale-while-revalidate (instant from cache,
 *                             refreshed in the background)
 *       /api/*              -> NEVER cached (freshness wins for server state)
 *       cross-origin (WOPI) -> never intercepted
 *
 * Scope note: this worker is mounted at /static/, so its default control
 * scope is /static/. To make it control the whole app (/, /editor/*) the
 * server must either serve it from the site root, register it from a
 * root-level script, or respond with `Service-Worker-Allowed: /`. See the
 * task summary for the wiring step that lives outside this file's scope.
 */

"use strict";

const VERSION = "v1";

const CACHES = {
  static: `opencloud-docserver-static-${VERSION}`,
  pages: `opencloud-docserver-pages-${VERSION}`,
};

// Same-origin assets that make the app shell usable offline.
const PRECACHE_URLS = [
  "/static/style.css",
  "/static/editor.js",
  "/static/i18n.js",
  "/static/demo.mp4",
];

// Last-resort page for navigations that have never been cached.
const OFFLINE_PAGE = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Offline — opencloud-docserver</title>
  <style>
    body{margin:0;min-height:100vh;display:flex;align-items:center;
      justify-content:center;background:#1e1e28;color:#e8e8f0;
      font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;}
    .card{max-width:420px;padding:32px;background:#2a2a3a;
      border:1px solid #3a3a4e;border-radius:12px;text-align:center;}
    h1{font-size:18px;margin:0 0 8px;}
    p{color:#9a9ab0;font-size:14px;line-height:1.5;margin:0 0 16px;}
    button{background:#4a6cf7;color:#fff;border:0;border-radius:6px;
      padding:8px 16px;font-size:14px;cursor:pointer;}
  </style>
</head>
<body>
  <div class="card">
    <h1>You're offline</h1>
    <p>This page has not been cached on this device yet. Connect to the
       network, open it once, and it will be available offline afterwards.</p>
    <button onclick="location.reload()">Retry</button>
  </div>
</body>
</html>`;

// ---------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHES.static)
      .then((cache) =>
        // allSettled: a failing asset must not fail the whole install.
        Promise.allSettled(PRECACHE_URLS.map((url) => cache.add(url)))
      )
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  const active = Object.values(CACHES);
  event.waitUntil(
    (async () => {
      const keys = await caches.keys();
      await Promise.all(
        keys
          .filter(
            (key) =>
              key.startsWith("opencloud-docserver-") && !active.includes(key)
          )
          .map((key) => caches.delete(key))
      );
      await self.clients.claim();
    })()
  );
});

// ---------------------------------------------------------------------------
// Fetch strategies
// ---------------------------------------------------------------------------

/**
 * Network-first with page-cache fallback and an offline page as last resort.
 * Used for document navigations: the freshest server version wins, but a
 * previously opened document keeps working on a dead link.
 */
async function networkFirst(req) {
  const cache = await caches.open(CACHES.pages);
  try {
    const res = await fetch(req);
    if (res && res.ok) cache.put(req, res.clone());
    return res;
  } catch (err) {
    const cached = await cache.match(req);
    if (cached) return cached;
    if (req.mode === "navigate") {
      return new Response(OFFLINE_PAGE, {
        headers: { "Content-Type": "text/html; charset=utf-8" },
      });
    }
    return new Response("Offline", { status: 504, statusText: "Offline" });
  }
}

/**
 * Cache-first with background refresh. Static assets rarely change and are
 * versioned by cache name on deploy, so serving from cache is safe and fast.
 */
async function staleWhileRevalidate(req) {
  const cache = await caches.open(CACHES.static);
  const cached = await cache.match(req);
  const refresh = fetch(req)
    .then((res) => {
      if (res && res.ok) cache.put(req, res.clone());
      return res;
    })
    .catch(() => null);
  return (
    cached ||
    (await refresh) ||
    new Response("Offline", { status: 504, statusText: "Offline" })
  );
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  // Only same-origin GETs: never touch PUT/POST (save, upload, WOPI lock)
  // and never intercept cross-origin traffic (OCIS WOPI host).
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  if (req.mode === "navigate") {
    event.respondWith(networkFirst(req));
    return;
  }

  // API: network-only. Stale document lists / metadata are worse than none.
  if (url.pathname.startsWith("/api/")) return;

  if (url.pathname.startsWith("/static/")) {
    event.respondWith(staleWhileRevalidate(req));
    return;
  }

  event.respondWith(networkFirst(req));
});
