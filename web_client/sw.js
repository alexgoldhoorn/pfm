// Network-pass-through service worker with push notification support.
self.addEventListener('fetch', (event) => {
  event.respondWith(fetch(event.request));
});

self.addEventListener('push', (event) => {
  if (!event.data) return;
  const data = event.data.json();
  event.waitUntil(
    self.registration.showNotification(data.title || 'PFM Alert', {
      body: data.body || '',
      icon: data.icon || '/icons/icon-192.png',
      badge: '/icons/icon-192.png',
      tag: 'pfm-alert',
      renotify: true,
    })
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  event.waitUntil(clients.openWindow('/'));
});
