// SOLO PER I TEST: stesso protocollo di worker.js, ma l'app Python gira in CPython dietro /__bridge
// (al posto di Pyodide, che i test non possono scaricare). Tutto il resto è il codice vero della demo.
'use strict';
importScripts('persist.js');
const post = (m, t) => self.postMessage(m, t || []);
const b64 = (buf) => { let s = ''; const u = new Uint8Array(buf); for (let i = 0; i < u.length; i += 0x8000) s += String.fromCharCode.apply(null, u.subarray(i, i + 0x8000)); return btoa(s); };
const unb64 = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0)).buffer;
let queue = Promise.resolve();
onmessage = (ev) => {
  const m = ev.data;
  if (m.kind === 'init') {
    post({ kind: 'status', text: 'Avvio (worker di prova)…' });
    Persist.check().then((ok) => post({ kind: 'ready', diag: 'fake', persist: ok }));
  } else if (m.kind === 'http') {
    queue = queue.then(async () => {
      const r = await fetch('/__bridge', { method: 'POST', body: JSON.stringify({
        method: m.method, path: m.path, query: m.query, headers: m.headers, referrer: m.referrer,
        body: m.body ? b64(m.body) : null }) });
      const j = await r.json();
      const body = unb64(j.body);
      post({ kind: 'http', id: m.id, status: j.status, headers: j.headers, body }, [body]);
    });
  } else if (m.kind === 'reset') {
    fetch('/__reset', { method: 'POST' }).then(() => Persist.clear()).then(() => post({ kind: 'reset-done' }));
  }
};
