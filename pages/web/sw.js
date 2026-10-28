// Service worker: trasforma ogni richiesta verso /app/ in una chiamata all'app Python che gira nella pagina.
'use strict';
const BASE = new URL('./', self.location).pathname;          // es. /FantaRF/
const APP = BASE + 'app/';
const STATIC = APP + 'static/';
const NO_BODY = [101, 204, 205, 304];

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname === APP.slice(0, -1)) {                   // /FantaRF/app  ->  /FantaRF/app/
    event.respondWith(Response.redirect(APP + url.search, 302));
    return;
  }
  if (!url.pathname.startsWith(APP)) return;
  if (url.pathname.startsWith(STATIC) && ['GET', 'HEAD'].includes(event.request.method)) {
    // File statici (CSS, JS, stemmi): serviti direttamente dal sito, senza passare da Python.
    event.respondWith(fetch(BASE + 'static/' + url.pathname.slice(STATIC.length) + url.search));
    return;
  }
  event.respondWith(serve(event.request, url));
});

async function findShell() {
  const all = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
  return all.find((c) => {
    const p = new URL(c.url).pathname;
    return p === BASE || p === BASE + 'index.html';
  });
}

function ask(client, message, transfer) {
  return new Promise((resolve, reject) => {
    const ch = new MessageChannel();
    const timer = setTimeout(() => reject(new Error('Timeout: l\'app Python non ha risposto')), 180000);
    ch.port1.onmessage = (ev) => { clearTimeout(timer); resolve(ev.data); };
    client.postMessage(message, [ch.port2, ...(transfer || [])]);
  });
}

function backToShell(url) {
  const go = encodeURIComponent(url.pathname.slice(APP.length - 1) + url.search);
  const target = BASE + '?go=' + go;
  return new Response(
    '<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0;url=' + target + '">' +
    '<script>location.replace(' + JSON.stringify(target) + ')</script>',
    { status: 200, headers: { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' } });
}

function toResponse(r, reqUrl) {
  const headers = new Headers();
  for (const [k, v] of r.headers) { try { headers.append(k, v); } catch (_) { /* intestazione non ammessa */ } }
  const loc = headers.get('location');
  if ([301, 302, 303, 307, 308].includes(r.status) && loc) {
    return Response.redirect(new URL(loc, reqUrl).href, r.status);
  }
  return new Response(NO_BODY.includes(r.status) ? null : r.body, { status: r.status, headers });
}

async function serve(request, url) {
  try {
    const shell = await findShell();
    if (!shell) return backToShell(url);
    const hasBody = !['GET', 'HEAD'].includes(request.method);
    const body = hasBody ? await request.arrayBuffer() : null;
    const headers = {};
    request.headers.forEach((v, k) => { headers[k] = v; });
    const reply = await ask(shell, {
      kind: 'http',
      method: request.method,
      path: '/' + url.pathname.slice(APP.length),
      query: url.search.slice(1),
      headers,
      body: body && body.byteLength ? body : null,
      referrer: request.referrer && request.referrer !== 'about:client' ? request.referrer : '',
    }, body && body.byteLength ? [body] : []);
    return toResponse(reply, url);
  } catch (err) {
    return new Response('Errore della demo: ' + err.message, {
      status: 502, headers: { 'Content-Type': 'text/plain; charset=utf-8' } });
  }
}
