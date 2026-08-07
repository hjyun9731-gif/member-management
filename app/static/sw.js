// 강원도 개인소형화물협회 업무관리 시스템 - Service Worker
// 전략: 네트워크 우선 (network-first). 이 프로그램은 자주 수정/배포되므로
// 온라인 상태에서는 항상 최신 코드/데이터를 사용하고, 앱 셸(정적 파일)만
// 오프라인·저속망 대비용으로 캐시에 보관한다. /api/ 요청은 절대 캐시하지 않는다.

const CACHE_VERSION = 'assoc-shell-v1';
const SHELL_FILES = [
  '/',
  '/login',
  '/static/app.js',
  '/static/styles.css',
  '/static/manifest.json',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_VERSION).then((cache) =>
      cache.addAll(SHELL_FILES).catch(() => {
        // 일부 파일 캐시 실패해도 설치 자체는 계속 진행
      })
    )
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys.filter((k) => k !== CACHE_VERSION).map((k) => caches.delete(k))
      )
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const req = event.request;

  // GET 요청만 처리 (POST/PUT/DELETE 등은 그대로 네트워크로)
  if (req.method !== 'GET') return;

  const url = new URL(req.url);

  // API 요청은 캐시하지 않고 항상 네트워크로 (회원/통계/양도양수 등 실시간 데이터)
  if (url.pathname.startsWith('/api/')) {
    return;
  }

  // 같은 출처의 정적 리소스/페이지: 네트워크 우선, 실패 시 캐시 폴백
  if (url.origin === self.location.origin) {
    event.respondWith(
      fetch(req)
        .then((res) => {
          if (res && res.ok) {
            const resClone = res.clone();
            caches.open(CACHE_VERSION).then((cache) => cache.put(req, resClone));
          }
          return res;
        })
        .catch(() => caches.match(req).then((cached) => cached || caches.match('/')))
    );
  }
});
