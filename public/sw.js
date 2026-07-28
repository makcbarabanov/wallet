const CACHE = 'wallet-shell-v93';
const ASSETS = ['/manifest.webmanifest'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(ASSETS)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

/** HTML/navigation: network-first so UI updates aren't stuck on an old shell. */
self.addEventListener('fetch', (e) => {
  if (e.request.method !== 'GET') return;
  const url = new URL(e.request.url);
  const sameOrigin = url.origin === self.location.origin;
  const isNav = e.request.mode === 'navigate';
  const isHtml =
    sameOrigin &&
    (isNav ||
      url.pathname === '/' ||
      url.pathname === '/index.html' ||
      url.pathname.endsWith('.html'));

  if (isHtml) {
    e.respondWith(
      fetch(e.request)
        .then((r) => r)
        .catch(() => caches.match(e.request))
    );
  }
});
