const CACHE = "bist-scanner-cloud-v3.3.2";

const ASSETS = [
  "./",
  "./index.html",
  "./style.css?v=3.3.2",
  "./cloud-adapter.js?v=3.3.2",
  "./app.js?v=3.3.2",
  "./repair.js?v=3.3.2",
  "./manifest.webmanifest"
];

self.addEventListener("install", event => {
  self.skipWaiting();

  event.waitUntil(
    caches.open(CACHE).then(cache => cache.addAll(ASSETS))
  );
});

self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys()
      .then(keys =>
        Promise.all(
          keys
            .filter(key => key !== CACHE)
            .map(key => caches.delete(key))
        )
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", event => {
  if (event.request.method !== "GET") return;

  const url = new URL(event.request.url);

  // JSON verileri ve sanal API yanıtları kesinlikle önbellekten gelmesin.
  if (
    url.pathname.includes("/data/") ||
    url.pathname.includes("/api/")
  ) {
    return;
  }

  event.respondWith(
    fetch(event.request)
      .then(response => {
        const copy = response.clone();

        caches.open(CACHE).then(cache => {
          cache.put(event.request, copy);
        });

        return response;
      })
      .catch(() => caches.match(event.request))
  );
});
