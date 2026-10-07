// Keeps the page, its Python and Pyodide on the phone, so the list works with no signal.
// Page files: served from the cache, refreshed in the background. CDN files: cache first.

const CACHE = 'plywood-shop-3';
const PYODIDE = 'https://cdn.jsdelivr.net/pyodide/v314.0.7/full/';
const FILES = [
  './', 'shop.js', 'shop.css', 'worker.js', 'plywood.zip', 'icon.svg', 'manifest.webmanifest',
  'https://cdn.jsdelivr.net/npm/alpinejs@3.14.9/dist/cdn.min.js',
  ...['pyodide.mjs', 'pyodide.asm.mjs', 'pyodide.asm.wasm', 'python_stdlib.zip', 'pyodide-lock.json'].map((f) => PYODIDE + f),
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(FILES)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) {
    e.respondWith(
      caches.match(req).then((hit) => hit || fetch(req).then((res) => {
        if (res.ok) caches.open(CACHE).then((c) => c.put(req, res.clone()));
        return res;
      })),
    );
    return;
  }
  const key = req.mode === 'navigate' ? './' : req;
  e.respondWith(
    caches.open(CACHE).then(async (c) => {
      const hit = await c.match(key, { ignoreSearch: true });
      const fresh = fetch(req).then((res) => {
        if (res.ok) c.put(key, res.clone());
        return res;
      });
      if (hit) {
        e.waitUntil(fresh.catch(() => {}));
        return hit;
      }
      return fresh;
    }),
  );
});
