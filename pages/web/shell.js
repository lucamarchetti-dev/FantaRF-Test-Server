// Pagina principale della demo: attiva il service worker, avvia il worker Python e inoltra le richieste.
(function () {
  'use strict';
  var BASE = location.pathname.replace(/[^/]*$/, '');
  var BUILD = '__BUILD__';
  var $ = function (id) { return document.getElementById(id); };
  var worker = null, pending = new Map(), nextId = 1;

  function status(text) { $('status').textContent = text; }
  function fatal(text, detail) {
    $('loading').hidden = true; $('app').hidden = true; $('fatal').hidden = false;
    $('fatal-text').textContent = text;
    $('fatal-detail').textContent = detail || '';
    $('fatal-detail').hidden = !detail;
  }

  $('btn-cred').addEventListener('click', function () {
    var box = $('cred'), open = box.hidden;
    box.hidden = !open;
    this.setAttribute('aria-expanded', String(open));
  });
  $('btn-reset').addEventListener('click', function () {
    if (!window.confirm('Azzerare tutti i dati della demo in questo browser? Si riparte dal listone iniziale.')) return;
    if (!worker) { Persist.clear().catch(function () {}).then(function () { location.href = BASE; }); return; }
    worker.postMessage({ kind: 'reset' });
  });

  // Una sola scheda alla volta: ogni scheda avrebbe il proprio database in memoria.
  // Al ricaricamento la scheda precedente può impiegare un attimo a rilasciare il lucchetto: si riprova.
  function singleTab() {
    if (!navigator.locks) return Promise.resolve(true);
    function attempt(left) {
      return new Promise(function (resolve) {
        navigator.locks.request('fantarf-demo-shell', { ifAvailable: true }, function (lock) {
          resolve(!!lock);
          if (lock) return new Promise(function () {});   // tenuto finché la scheda resta aperta
        });
      }).then(function (ok) {
        if (ok || left <= 0) return ok;
        return new Promise(function (r) { setTimeout(r, 300); }).then(function () { return attempt(left - 1); });
      });
    }
    return attempt(10);
  }

  function startServiceWorker() {
    if (!('serviceWorker' in navigator)) {
      return Promise.reject(new Error('Questo browser non supporta i service worker (serve HTTPS).'));
    }
    return navigator.serviceWorker.register('sw.js?v=' + BUILD, { scope: BASE })
      .then(function () { return navigator.serviceWorker.ready; })
      .then(function () {
        if (navigator.serviceWorker.controller) return;
        return new Promise(function (resolve) {
          navigator.serviceWorker.addEventListener('controllerchange', resolve, { once: true });
          setTimeout(resolve, 4000);
        });
      })
      .then(function () {
        if (!navigator.serviceWorker.controller) {
          throw new Error('Il service worker non controlla la pagina. Ricarica normalmente (senza Maiusc).');
        }
      });
  }

  // Richieste che arrivano dal service worker (una per ogni clic o modulo dell'app) -> worker Python.
  function forward(m) {
    return new Promise(function (resolve) {
      var id = nextId++;
      pending.set(id, resolve);
      worker.postMessage({ kind: 'http', id: id, method: m.method, path: m.path, query: m.query,
                           headers: m.headers, body: m.body, referrer: m.referrer }, m.body ? [m.body] : []);
    });
  }
  function listenToServiceWorker() {
    navigator.serviceWorker.addEventListener('message', function (ev) {
      var m = ev.data;
      if (!m || m.kind !== 'http' || !ev.ports[0]) return;
      var port = ev.ports[0];
      forward(m).then(function (r) { port.postMessage(r, r.body ? [r.body] : []); });
    });
  }

  function chooseWorkerScript() {
    var p = new URLSearchParams(location.search).get('worker');
    var local = /^(localhost|127\.0\.0\.1)$/.test(location.hostname);
    if (local && p && /^[\w.-]+\.js$/.test(p)) return p;        // solo per i test in locale
    return 'worker.js?v=' + BUILD;
  }

  function startWorker() {
    return new Promise(function (resolve, reject) {
      worker = new Worker(chooseWorkerScript());
      worker.onerror = function (e) { reject(new Error(e.message || 'Errore del worker')); };
      worker.onmessage = function (ev) {
        var m = ev.data;
        if (m.kind === 'status') status(m.text);
        else if (m.kind === 'ready') resolve(m);
        else if (m.kind === 'error') reject(new Error(m.text));
        else if (m.kind === 'http') { var r = pending.get(m.id); pending.delete(m.id); if (r) r(m); }
        else if (m.kind === 'reset-done') location.href = BASE;
      };
      worker.postMessage({ kind: 'init', origin: location.origin, base: BASE });
    });
  }

  function showApp() {
    var go = new URLSearchParams(location.search).get('go') || '';
    var rel = (go.charAt(0) === '/' && go.charAt(1) !== '/') ? go.slice(1) : '';
    var frame = $('app');
    frame.addEventListener('load', function () {
      try {
        var w = frame.contentWindow.location;
        var inner = w.pathname.slice((BASE + 'app').length) + w.search;
        var keep = new URLSearchParams(location.search).get('worker');
        history.replaceState(null, '', BASE + '?go=' + encodeURIComponent(inner) + (keep ? '&worker=' + encodeURIComponent(keep) : ''));
        document.title = (frame.contentDocument.title || 'Fanta PG') + ' · demo';
      } catch (e) { /* ignora */ }
    });
    frame.src = BASE + 'app/' + rel;
    $('loading').hidden = true; frame.hidden = false;
  }

  singleTab().then(function (ok) {
    if (!ok) throw new Error('La demo è già aperta in un\'altra scheda di questo browser. Usa quella, oppure chiudila e ricarica.');
    status('Attivo il service worker…');
    return startServiceWorker();
  }).then(function () {
    listenToServiceWorker();
    return startWorker();
  }).then(function (ready) {
    if (window.console) console.info('Diagnostica della demo:', ready.diag);
    if (!ready.persist) {
      $('cred').hidden = false;
      var warn = document.createElement('p');
      warn.textContent = 'Attenzione: questo browser non permette di salvare i dati (navigazione privata?). Le modifiche si perdono alla chiusura.';
      $('cred').appendChild(warn);
    }
    try { if (navigator.storage && navigator.storage.persist) navigator.storage.persist(); } catch (e) { /* ignora */ }
    showApp();
  }).catch(function (err) {
    fatal('Non è stato possibile avviare l\'app nel browser.', (err && (err.stack || err.message)) || String(err));
  });
})();
