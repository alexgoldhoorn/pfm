// Network-pass-through service worker with push notification support and the
// share target: a statement file shared to the installed app is posted to
// SHARE_PATH (manifest.json "share_target"), parked in the Cache API and picked
// up by the page (takeSharedFile in pfm_features.js, same SHARE_* names).
const SHARE_PATH = '/share-target';
const SHARE_CACHE = 'pfm-share';
const SHARE_KEY = '/_shared-file';

// Nothing is cached for offline use, so a new worker can take over at once.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));

function shareRedirect(outcome) {
  return Response.redirect(new URL('/?shared=' + outcome, self.location.origin).href, 303);
}

async function receiveShare(request) {
  try {
    const form = await request.formData();
    const files = form.getAll('file').filter((f) => f && typeof f !== 'string' && f.size > 0);
    if (!files.length) return shareRedirect('none');
    const file = files[0];
    const cache = await caches.open(SHARE_CACHE);
    await cache.put(SHARE_KEY, new Response(file, {
      headers: {
        'Content-Type': file.type || 'application/octet-stream',
        'X-Share-Name': encodeURIComponent(file.name || 'shared-file'),
        'X-Share-Count': String(files.length),
      },
    }));
    return shareRedirect('file');
  } catch (err) {
    return shareRedirect('error');
  }
}

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (event.request.method === 'POST' && url.origin === self.location.origin && url.pathname === SHARE_PATH) {
    event.respondWith(receiveShare(event.request));
    return;
  }
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
