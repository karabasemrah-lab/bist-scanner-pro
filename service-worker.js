const CACHE = 'bist-scanner-dashboard-v4.0.1-20260805';
const ASSETS = [
  './',
  './index.html',
  './style.css?v=4.0.1',
  './cloud-adapter.js?v=4.0.1',
  './app.js?v=4.0.1',
  './repair.js?v=4.0.1',
  './dashboard-v4.js?v=4.0.1',
  './manifest.webmanifest'
];
self.addEventListener('install', event => {
  self.skipWaiting();
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(ASSETS)));
});
self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key !== CACHE).map(key => caches.delete(key)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET') return;
  const url = new URL(event.request.url);
  if (url.pathname.includes('/data/') || url.pathname.includes('/api/')) {
    event.respondWith(fetch(event.request, {cache:'no-store'}));
    return;
  }
  event.respondWith(fetch(event.request).then(response => {
    const copy = response.clone();
    caches.open(CACHE).then(cache => cache.put(event.request, copy));
    return response;
  }).catch(() => caches.match(event.request)));
});
