/**
 * Service Worker — 체크리스트 #76 PWA.
 *
 * 캐시 전략:
 *  - 정적 자산: cache-first
 *  - /api/*  : network-only (캐시 금지 — 시세/잔고는 항상 fresh)
 *  - HTML    : network-first (오프라인 시 캐시 fallback)
 *
 * CLAUDE.md §2.1.5: 어떤 secret 도 캐시되어선 안 됨 → /api/* 는 절대 캐시 금지.
 */
const CACHE_NAME = "agent-trader-v1";
const STATIC_ASSETS = ["/", "/index.html", "/manifest.json"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_ASSETS)),
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)),
      ),
    ),
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  // API: 절대 캐시 금지 (CLAUDE.md §2.1.5)
  if (url.pathname.startsWith("/api/")) {
    return;  // 기본 fetch 그대로
  }

  // HTML: network-first
  if (event.request.mode === "navigate") {
    event.respondWith(
      fetch(event.request).catch(() =>
        caches.match("/index.html").then((r) => r || new Response("offline")),
      ),
    );
    return;
  }

  // 정적 자산: cache-first
  event.respondWith(
    caches.match(event.request).then(
      (cached) =>
        cached ||
        fetch(event.request).then((res) => {
          const clone = res.clone();
          if (res.status === 200 && res.type === "basic") {
            void caches.open(CACHE_NAME).then((c) => c.put(event.request, clone));
          }
          return res;
        }),
    ),
  );
});
