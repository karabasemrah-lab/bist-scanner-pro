const CACHE = "bist-scanner-v4.0.3-stable-20260806";

const ASSETS = [
  "./",
  "./index.html",
  "./style.css?v=4.0.3",
  "./cloud-adapter.js?v=4.0.3",
  "./app.js?v=4.0.3",
  "./repair.js?v=4.0.3",
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

  // API ve veri dosyaları daima internetten gelsin
  if (
    url.pathname.includes("/data/") ||
    url.pathname.includes("/api/")
  ) {
    event.respondWith(
      fetch(event.request, { cache: "no-store" })
    );
    return;
  }

  event.respondWith(
    fetch(event.request, { cache: "no-store" })
      .then(response => {
        const copy = response.clone();
        caches.open(CACHE).then(cache => cache.put(event.request, copy));
        return response;
      })
      .catch(() => caches.match(event.request))
  );
});
