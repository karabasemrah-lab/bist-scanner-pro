const CACHE="bist-scanner-v4.1.5-live-fix-20260812";
const ASSETS=["./","./index.html","./style.css?v=4.1.5","./cloud-adapter.js?v=4.1.5","./app.js?v=4.1.5","./manifest.webmanifest"];
self.addEventListener("install",e=>{self.skipWaiting();e.waitUntil(caches.open(CACHE).then(c=>c.addAll(ASSETS)))});
self.addEventListener("activate",e=>{e.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==CACHE).map(k=>caches.delete(k)))).then(()=>self.clients.claim()))});
self.addEventListener("fetch",e=>{if(e.request.method!=="GET")return;const u=new URL(e.request.url);if(u.pathname.includes("/data/")||u.pathname.includes("/api/")){e.respondWith(fetch(e.request,{cache:"no-store"}));return;}e.respondWith(fetch(e.request).then(r=>{const copy=r.clone();caches.open(CACHE).then(c=>c.put(e.request,copy));return r}).catch(()=>caches.match(e.request)))});
