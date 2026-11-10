/* ポプラジ Service Worker
 * アプリ本体・番組データ: ネットワーク優先 → オフライン時はキャッシュ
 * 音声: アプリで「保存」した回はキャッシュから Range 対応で返す（オフライン再生）。それ以外はブラウザに任せる
 */
const VERSION = "popradi-v3";
const AUDIO_CACHE = "popradi-audio";
const SHELL = ["./", "index.html", "app.css", "app.js", "lib.js", "manifest.webmanifest", "icons/icon.svg", "icons/icon-192.png", "icons/icon-512.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== VERSION && k !== AUDIO_CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.includes("/audio/")) {
    // 保存済みならキャッシュから（シークのための Range にも対応）、無ければブラウザに任せる
    e.respondWith(
      caches.open(AUDIO_CACHE).then((c) => c.match(url.href)).then((hit) => (hit ? rangeResponse(e.request, hit) : fetch(e.request)))
    );
    return;
  }
  // ネットワーク優先 → 失敗時（オフライン）だけキャッシュ。毎朝の更新やアプリ改修が即反映される
  e.respondWith(
    fetch(e.request)
      .then((r) => {
        if (r.ok) { const copy = r.clone(); caches.open(VERSION).then((c) => c.put(e.request, copy)); }
        return r;
      })
      .catch(() => caches.match(e.request, { ignoreSearch: true }))
  );
});

async function rangeResponse(request, cached) {
  const range = request.headers.get("range");
  const type = cached.headers.get("content-type") || "audio/mpeg";
  const buf = await cached.arrayBuffer();
  if (!range) return new Response(buf, { status: 200, headers: { "content-type": type, "content-length": String(buf.byteLength), "accept-ranges": "bytes" } });
  const m = /bytes=(\d*)-(\d*)/.exec(range) || [];
  const start = m[1] ? Number(m[1]) : 0;
  const end = m[2] ? Math.min(Number(m[2]), buf.byteLength - 1) : buf.byteLength - 1;
  return new Response(buf.slice(start, end + 1), {
    status: 206,
    headers: { "content-type": type, "content-range": `bytes ${start}-${end}/${buf.byteLength}`, "content-length": String(end - start + 1), "accept-ranges": "bytes" },
  });
}
