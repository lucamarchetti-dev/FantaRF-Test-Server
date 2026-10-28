// Salvataggio nel browser (IndexedDB): database dell'app e cookie di sessione.
// Usato sia dalla pagina sia dal worker.
(function (root) {
  'use strict';
  var DB_NAME = 'fantarf-demo', STORE = 'kv';

  function open() {
    return new Promise(function (resolve, reject) {
      if (!root.indexedDB) { reject(new Error('IndexedDB non disponibile')); return; }
      var req = root.indexedDB.open(DB_NAME, 1);
      req.onupgradeneeded = function () { req.result.createObjectStore(STORE); };
      req.onsuccess = function () { resolve(req.result); };
      req.onerror = function () { reject(req.error); };
      req.onblocked = function () { reject(new Error('IndexedDB bloccato da un\'altra scheda')); };
    });
  }

  function run(mode, work) {
    return open().then(function (db) {
      return new Promise(function (resolve, reject) {
        var tx = db.transaction(STORE, mode), result;
        try { result = work(tx.objectStore(STORE)); } catch (e) { db.close(); reject(e); return; }
        tx.oncomplete = function () { db.close(); resolve(result && 'result' in result ? result.result : undefined); };
        tx.onerror = tx.onabort = function () { db.close(); reject(tx.error); };
      });
    });
  }

  root.Persist = {
    get: function (key) { return run('readonly', function (s) { return s.get(key); }); },
    set: function (key, value) { return run('readwrite', function (s) { s.put(value, key); }); },
    del: function (key) { return run('readwrite', function (s) { s.delete(key); }); },
    clear: function () { return run('readwrite', function (s) { s.clear(); }); },
    // true se il browser permette di salvare (falso, ad esempio, in alcune navigazioni private)
    check: function () {
      return run('readwrite', function (s) { s.put(1, '__probe'); }).then(function () { return true; }, function () { return false; });
    }
  };
})(self);
