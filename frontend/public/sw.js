/*
 * VAYU service worker — an OFFLINE FALLBACK and nothing else.
 *
 * What it does:
 *   1. On install it stores /offline.html (+ the local fonts it uses) in its own cache.
 *   2. When a PAGE NAVIGATION cannot reach the network (no internet, DNS failure, a connection that hangs for 15 s, or a 502/503/504 from
 *      the host), it answers with the cached /offline.html instead of the browser's dinosaur / error page.
 *   3. Requests for /fonts/* are served cache-first so the offline page (and the in-app overlay) keep their typography with no network.
 *
 * What it deliberately does NOT do: it never caches or rewrites the app's HTML, JS, CSS, API calls or map tiles, so it can never serve
 * a stale version of Vayu. A normal response — including a 404 — always passes through untouched (your 404.html keeps working).
 *
 * It only helps visitors who have loaded Vayu at least once online (a service worker has to be installed first). To retire it, ship a
 * new VERSION or unregister it from the browser's Application tab; old caches named vayu-offline-* are removed on activation.
 */
const VERSION = 'v1';
const CACHE = `vayu-offline-${VERSION}`;
const OFFLINE_URL = '/offline.html';
const FONTS = [
  '/fonts/orbitron-latin-700-normal.woff2', '/fonts/orbitron-latin-900-normal.woff2',
  '/fonts/rajdhani-latin-500-normal.woff2', '/fonts/rajdhani-latin-600-normal.woff2',
];
const OPTIONAL = [...FONTS, '/favicon.ico'];
const NAVIGATION_TIMEOUT_MS = 15000;
const UNREACHABLE_STATUSES = [502, 503, 504];

self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE);
    await cache.add(new Request(OFFLINE_URL, { cache: 'reload' }));          // required: if this fails the install fails and is retried on the next visit
    await Promise.all(OPTIONAL.map((u) => cache.add(new Request(u, { cache: 'reload' })).catch(() => {})));
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter((k) => k.startsWith('vayu-offline-') && k !== CACHE).map((k) => caches.delete(k)));
    await self.clients.claim();
  })());
});

async function offlineResponse() {
  const cached = await caches.match(OFFLINE_URL);
  return cached || new Response('You are offline.', { status: 503, headers: { 'Content-Type': 'text/plain; charset=utf-8' } });
}

async function navigate(request) {
  let timer;
  try {
    const timeout = new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('navigation timeout')), NAVIGATION_TIMEOUT_MS); });
    const response = await Promise.race([fetch(request), timeout]);
    if (UNREACHABLE_STATUSES.includes(response.status)) {
      const cached = await caches.match(OFFLINE_URL);
      return cached || response;                                              // no cached page? show the host's own error rather than inventing one
    }
    return response;
  } catch (e) {
    return offlineResponse();
  } finally {
    clearTimeout(timer);
  }
}

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;                             // never touch third-party requests (maps, fonts CDNs, APIs)
  if (request.mode === 'navigate') {
    event.respondWith(navigate(request));
  } else if (FONTS.includes(url.pathname)) {
    event.respondWith(caches.match(request).then((hit) => hit || fetch(request)));
  }
});
