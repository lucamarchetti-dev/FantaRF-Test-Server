// Worker: carica Python (Pyodide), l'app FantaRF e il database, ed esegue le richieste HTTP.
'use strict';
const BUILD = '__BUILD__';
const PYODIDE_VERSION = '0.26.4';
const PYODIDE_URL = 'https://cdn.jsdelivr.net/pyodide/v' + PYODIDE_VERSION + '/full/';
importScripts('persist.js');

let pyodide = null, bridge = null, persistOk = true, lastCookies = '{}', queue = Promise.resolve();

const post = (msg, transfer) => self.postMessage(msg, transfer || []);
const status = (text) => post({ kind: 'status', text });

onmessage = (ev) => {
  const m = ev.data;
  if (m.kind === 'init') {
    boot(m).then((diag) => post({ kind: 'ready', diag, persist: persistOk }),
                 (err) => post({ kind: 'error', text: describe(err) }));
  } else if (m.kind === 'http') {
    queue = queue.then(() => http(m));
  } else if (m.kind === 'reset') {
    Persist.clear().catch(() => {}).then(() => post({ kind: 'reset-done' }));
  }
};

function describe(err) { return (err && (err.stack || err.message)) || String(err); }

async function fetchBuffer(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error('Download fallito (' + r.status + '): ' + url);
  return r.arrayBuffer();
}

async function boot({ origin, base }) {
  status('Scarico Python (WebAssembly): circa 10 MB, solo la prima volta…');
  importScripts(PYODIDE_URL + 'pyodide.js');
  pyodide = await loadPyodide({ indexURL: PYODIDE_URL });

  status('Carico i componenti di Python…');
  await pyodide.loadPackage('micropip');
  await pyodide.loadPackage('markupsafe');
  for (const optional of ['sqlite3', 'hashlib']) {       // a seconda della versione possono essere già inclusi
    try { await pyodide.loadPackage(optional); } catch (e) { /* già integrato o non disponibile */ }
  }

  status('Installo Flask e le librerie dell\'app…');
  const manifest = await (await fetch(base + 'wheels/manifest.json?v=' + BUILD)).json();
  pyodide.globals.set('WHEEL_URLS', pyodide.toPy(manifest.map((f) => new URL(base + 'wheels/' + f, origin).href)));
  await pyodide.runPythonAsync('import micropip\nawait micropip.install(list(WHEEL_URLS), deps=False)');

  status('Carico l\'app Fanta PG…');
  pyodide.unpackArchive(await fetchBuffer(base + 'bundle.zip?v=' + BUILD), 'zip', { extractDir: '/app' });
  pyodide.FS.writeFile('/app/fantarf_bridge.py', await (await fetch(base + 'bridge.py?v=' + BUILD)).text());
  pyodide.runPython('import sys\nsys.path.insert(0, "/app")');

  status('Preparo il database…');
  persistOk = await Persist.check();
  let dbBytes = persistOk ? await Persist.get('db') : null;
  const fresh = !dbBytes;
  if (fresh) dbBytes = new Uint8Array(await fetchBuffer(base + 'demo.sqlite3?v=' + BUILD));
  lastCookies = (persistOk && !fresh && (await Persist.get('cookies'))) || '{}';
  pyodide.FS.mkdirTree('/data');
  pyodide.FS.writeFile('/data/fantapg.sqlite3', dbBytes);

  pyodide.globals.set('JS_ORIGIN', origin);
  pyodide.globals.set('JS_SCRIPT_NAME', base + 'app');
  pyodide.globals.set('JS_COOKIES', lastCookies);
  const diag = pyodide.runPython(
    'import fantarf_bridge as B\nB.init(JS_ORIGIN, JS_SCRIPT_NAME, "/data", JS_COOKIES)');
  bridge = pyodide.pyimport('fantarf_bridge');
  if (fresh) { bridge.first_run(); await saveDb(); }
  return diag;
}

async function saveDb() {
  if (!persistOk) return;
  await Persist.set('db', pyodide.FS.readFile('/data/fantapg.sqlite3'));
}

function bytesOf(proxy) {
  try {
    const buf = proxy.getBuffer();
    const copy = buf.data.slice();
    buf.release();
    return copy;
  } catch (e) {
    return proxy.toJs();
  }
}

async function http(m) {
  let reply;
  try {
    const body = m.body ? new Uint8Array(m.body) : null;
    const t = bridge.handle(m.method, m.path, m.query || '', JSON.stringify(m.headers || {}), body, m.referrer || '');
    const code = t.get(0), headers = JSON.parse(t.get(1)), cookiesChanged = t.get(3);
    const proxy = t.get(2);
    const out = bytesOf(proxy);
    if (proxy.destroy) proxy.destroy();
    t.destroy();
    if (persistOk) {
      if (!['GET', 'HEAD', 'OPTIONS'].includes(m.method.toUpperCase())) await saveDb();
      if (cookiesChanged) { lastCookies = bridge.cookies_json(); await Persist.set('cookies', lastCookies); }
    }
    reply = { kind: 'http', id: m.id, status: code, headers, body: out.buffer };
  } catch (err) {
    const text = new TextEncoder().encode('Errore interno della demo:\n\n' + describe(err));
    reply = { kind: 'http', id: m.id, status: 500,
              headers: [['Content-Type', 'text/plain; charset=utf-8']], body: text.buffer };
  }
  post(reply, [reply.body]);
}
