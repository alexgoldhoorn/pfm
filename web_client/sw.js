// Network-pass-through service worker — enables PWA installability.
// All requests go to the network; no caching is performed.
self.addEventListener('fetch', (event) => {
  event.respondWith(fetch(event.request));
});
