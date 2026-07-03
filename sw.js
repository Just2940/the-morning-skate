// The Morning Skate service worker - v3 (2026-07-03 rewrite)
//
// v2 was cache-first for EVERYTHING, so every browser ran the previous
// deploy of index.html forever (fixes never arrived), and it served two
// hardcoded base64 icons that overrode the real files. Both landmines
// removed.
//
// Strategy:
//   - index.html (navigations) and data.json: NETWORK-FIRST.
//     Fresh app and fresh scores every load; cache only as offline fallback.
//   - Everything else same-origin (icons, manifest): stale-while-revalidate.
//   - Cross-origin (ESPN logos etc.): untouched, browser default.

const CACHE_NAME = 'morning-skate-v3';

self.addEventListener('install', () => {
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  const isAppShell = req.mode === 'navigate' || url.pathname === '/data.json';

  if (isAppShell) {
    // Network-first: the site must never be stale after a deploy.
    event.respondWith(
      fetch(req).then(response => {
        if (response.ok) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(req, clone));
        }
        return response;
      }).catch(() =>
        caches.match(req).then(cached => cached || caches.match('/'))
      )
    );
    return;
  }

  // Static assets: serve cache, refresh in background.
  event.respondWith(
    caches.match(req).then(cached => {
      const refresh = fetch(req).then(response => {
        if (response.ok) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(req, clone));
        }
        return response;
      }).catch(() => cached);
      return cached || refresh;
    })
  );
});
