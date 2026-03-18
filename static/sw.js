/* sw.js — Service Worker for Parking Monitor PWA
 * Cache-first strategy for the dashboard shell.
 * API responses are intentionally NOT cached (they must be fresh).
 */

const CACHE_NAME = "parking-monitor-v1";
const SHELL_URLS = ["/dashboard", "/manifest.json", "/sw.js"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_URLS))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))
      )
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  // Only cache GET requests for the shell URLs; pass everything else through.
  if (
    event.request.method !== "GET" ||
    !SHELL_URLS.some((u) => url.pathname === u)
  ) {
    return;
  }

  event.respondWith(
    caches.match(event.request).then(
      (cached) => cached || fetch(event.request)
    )
  );
});
