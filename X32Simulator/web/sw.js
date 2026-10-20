self.addEventListener('install', e => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(clients.claim()));
self.addEventListener('fetch', e => { /* 全部直连网络,控制台数据不允许离线缓存 */ });
