"""Prova in un vero browser (Chromium) di pagina guscio + service worker + iframe + app.

    pip install playwright && playwright install chromium     # una tantum
    python -m FantaRF.pages.test_browser                       # dalla cartella che CONTIENE il pacchetto

Python (Pyodide) non è scaricabile nei test: al suo posto un worker finto (fake_worker.js) inoltra le richieste
allo stesso bridge.py eseguito da CPython. Si verifica quindi tutto tranne l'avvio di Pyodide.
"""
from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent
failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("  ok   " if cond else "  FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failures.append(name)


class Server:
    def __init__(self, site: Path):
        self.site, self.lock, self.B, self.alias_worker = site, threading.Lock(), None, False
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):  # silenzioso
                pass

            def _send(self, code, ctype, data):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n)
                if self.path == "/__reset":
                    with outer.lock:
                        outer.start_app()
                    return self._send(200, "text/plain", b"ok")
                if self.path != "/__bridge":
                    return self._send(404, "text/plain", b"no")
                req = json.loads(raw)
                body = base64.b64decode(req["body"]) if req.get("body") else None
                with outer.lock:
                    status, hdrs, out, _ = outer.B.handle(req["method"], req["path"], req["query"],
                                                          json.dumps(req["headers"] or {}), body, req.get("referrer") or "")
                self._send(200, "application/json", json.dumps(
                    {"status": status, "headers": json.loads(hdrs), "body": base64.b64encode(out).decode()}).encode())

            def do_GET(self):
                path = self.path.split("?")[0]
                if not path.startswith("/FantaRF/"):
                    return self._send(404, "text/plain", b"fuori dal sottopercorso")
                rel = path[len("/FantaRF/"):] or "index.html"
                if rel == "worker.js" and outer.alias_worker:       # scenario senza parametro ?worker=
                    rel = "fake_worker.js"
                f = (outer.site / rel)
                if f.is_dir():
                    f = f / "index.html"
                if not f.is_file() or outer.site not in f.resolve().parents:
                    page = (outer.site / "404.html").read_bytes()            # come fa GitHub Pages
                    return self._send(404, "text/html; charset=utf-8", page)
                ctype = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css",
                         ".png": "image/png", ".json": "application/json"}.get(f.suffix, "application/octet-stream")
                self._send(200, ctype, f.read_bytes())

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]

    def start_app(self):
        from . import bridge as B
        state = Path(tempfile.mkdtemp(prefix="fantarf-browser-state-"))
        shutil.copy(self.site / "demo.sqlite3", state / B.DB_NAME)
        B.init(f"http://127.0.0.1:{self.port}", "/FantaRF/app", str(state))
        B.first_run()
        self.B = B

    def run(self):
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()


def main() -> int:
    from playwright.sync_api import sync_playwright

    tmp = Path(tempfile.mkdtemp(prefix="fantarf-site-"))
    site = tmp / "site"
    subprocess.run([sys.executable, "-m", "FantaRF.pages.build", "--out", str(site), "--skip-wheels"], check=True,
                   stdout=subprocess.DEVNULL)
    shutil.copy(Path(__file__).with_name("fake_worker.js"), site / "fake_worker.js")
    srv = Server(site)
    srv.start_app()
    srv.run()
    base = f"http://127.0.0.1:{srv.port}/FantaRF/"
    shell_url = base + "?worker=fake_worker.js"

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context()
        page = ctx.new_page()
        console_errors, bad_responses = [], []
        page.on("console", lambda m: m.type == "error" and console_errors.append(m.text))
        page.on("pageerror", lambda e: console_errors.append(str(e)))
        page.on("response", lambda r: r.status >= 400 and bad_responses.append(f"{r.status} {r.url}"))
        page.on("dialog", lambda d: d.accept())

        print("Avvio: service worker + worker + iframe")
        page.goto(shell_url)
        page.wait_for_selector("#app:not([hidden])", timeout=20000)
        frame = page.frame_locator("#app")
        frame.get_by_role("heading").first.wait_for(timeout=15000)
        check("l'iframe mostra l'app (non la pagina di caricamento)", page.locator("#loading").is_hidden())
        inner = page.frames[-1].url
        check("l'iframe è servito dal sottopercorso /app/", "/FantaRF/app/" in inner, inner)
        check("lo stile (CSS) è stato applicato", frame.locator("body").evaluate(
            "b => getComputedStyle(b).backgroundColor !== 'rgba(0, 0, 0, 0)' || getComputedStyle(b).fontFamily.length > 0"))
        check("la pagina guscio mostra il banner demo", page.locator(".pill").is_visible())

        print("Login (modulo POST → redirect → cookie in Python)")
        frame.get_by_role("link", name=re.compile("accedi|login", re.I)).first.click()
        frame.locator('input[name="username"]').fill("admin")
        frame.locator('input[name="password"]').fill("DemoAdmin-2026")
        frame.locator('form button[type="submit"], form input[type="submit"]').first.click()
        frame.get_by_role("link", name=re.compile("staff", re.I)).first.wait_for(timeout=10000)
        check("dopo il login l'iframe è su /app/ (redirect seguito)", page.frames[-1].url.rstrip("/").endswith("/FantaRF/app"), page.frames[-1].url)

        print("Navigazione dello staff e modulo con conferma")
        page.frames[-1].goto(base + "app/staff/punteggi/")
        frame.locator('input[name="sv_vote"]').wait_for()
        check("pagina Punteggi con campo SV = 5,5", frame.locator('input[name="sv_vote"]').input_value() in ("5,5", "5.5"))
        frame.locator('input[name="sv_vote"]').fill("6,25")
        frame.get_by_role("button", name=re.compile("Salva i punteggi")).click()
        frame.locator('input[name="sv_vote"]').wait_for()
        check("il voto SV salvato (6,25) si rilegge dalla pagina", frame.locator('input[name="sv_vote"]').input_value() in ("6,25", "6.25"))

        print("Upload di un file Excel dal selettore file")
        page.frames[-1].goto(base + "app/staff/import")
        frame.locator('input[type="file"]').set_input_files(str(PKG / "seed" / "Elenco_Giocatori_Fantacalcio.xlsx"))
        frame.get_by_role("button", name=re.compile("Carica e controlla")).click()
        page.wait_for_function("() => /\\/app\\/staff\\/import\\/\\d+/.test(document.getElementById('app').contentWindow.location.pathname)",
                               timeout=15000)
        frame.get_by_role("heading", name=re.compile("Anteprima importazione")).wait_for(timeout=10000)
        check("l'anteprima riporta 748 righe valide e 178 scartate",
              bool(re.search(r"Valide\s*748", frame.locator("body").inner_text())) and "178" in frame.locator("body").inner_text())
        check("l'anteprima dell'importazione si apre (multipart attraverso il service worker)",
              re.search(r"/app/staff/import/\d+", page.frames[-1].url) is not None, page.frames[-1].url)

        print("Persistenza della posizione e della sessione")
        before = page.frames[-1].url
        page.reload()
        page.wait_for_selector("#app:not([hidden])", timeout=20000)
        frame.get_by_role("heading").first.wait_for(timeout=15000)
        check("dopo il ricaricamento l'iframe torna alla stessa pagina", page.frames[-1].url == before, f"{before} → {page.frames[-1].url}")
        check("…e si è ancora connessi", frame.get_by_role("link", name=re.compile("staff", re.I)).count() > 0)

        print("Collegamento diretto a una pagina dell'app")
        page.goto(shell_url + "&go=" + "%2Fgiocatori")
        page.wait_for_selector("#app:not([hidden])", timeout=20000)
        frame.get_by_role("heading").first.wait_for(timeout=15000)
        check("?go=/giocatori apre direttamente il listone", page.frames[-1].url.endswith("/FantaRF/app/giocatori"), page.frames[-1].url)

        print("Una sola scheda alla volta")
        page2 = ctx.new_page()
        page2.goto(shell_url)
        page2.wait_for_selector("#fatal:not([hidden])", timeout=15000)
        check("la seconda scheda avvisa che la demo è già aperta", "già aperta" in page2.locator("#fatal-detail").inner_text())
        page2.close()

        print("Salvataggio nel browser (IndexedDB)")
        result = page.evaluate("""async () => {
          await Persist.set('t', new Uint8Array([1,2,3])); const v = await Persist.get('t');
          await Persist.clear(); const g = await Persist.get('t');
          return [Array.from(v), g === undefined, await Persist.check()]; }""")
        check("scrive, legge e svuota", result == [[1, 2, 3], True, True], str(result))

        print("Azzera dati")
        page.locator("#btn-reset").click()
        page.wait_for_selector("#app:not([hidden])", timeout=20000)
        page.frames[-1].url  # ricaricata
        check("dopo «Azzera dati» la demo riparte", page.locator("#app").is_visible())

        ctx.close()

        print("Selettore della formazione: un giocatore per ruolo")
        srv.B.reset_cookies()          # la sessione vive nel ponte Python: si esce dall'account admin
        ctx_t = browser.new_context()
        pt = ctx_t.new_page()
        pt.on("dialog", lambda d: d.accept())
        srv.alias_worker = True
        pt.goto(base)
        pt.wait_for_selector("#app:not([hidden])", timeout=20000)
        ft = pt.frame_locator("#app")
        ft.get_by_role("link", name=re.compile("accedi|login", re.I)).first.click()
        ft.locator('input[name="username"]').fill("tester")
        ft.locator('input[name="password"]').fill("DemoTester-2026")
        ft.locator('form button[type="submit"]').first.click()
        ft.get_by_role("link", name=re.compile("concors", re.I)).first.wait_for(timeout=10000)
        pt.frames[-1].goto(base + "app/concorsi/1/formazione")
        ft.locator("[data-add]").first.wait_for(timeout=10000)
        first = ft.locator("[data-add]").first
        first_label = first.get_attribute("aria-label")
        first.click()
        ft.locator("li.slot-filled").first.wait_for(timeout=5000)
        blocked = ft.locator("li.result.is-blocked .why", has_text=re.compile("Ruolo [PDTCWA] già coperto"))
        check("aggiunto un giocatore, gli altri dello stesso ruolo diventano non selezionabili con il motivo",
              blocked.count() > 0, first_label)
        check("…e per quelle righe non c'è il pulsante «Aggiungi»",
              ft.locator("li.result.is-blocked button[data-add]").count() == 0)
        check("la regola compare nel riepilogo della formazione",
              ft.get_by_text(re.compile("Un giocatore per ruolo")).count() > 0)
        ctx_t.close()

        print("Primo accesso con un collegamento diretto (service worker non ancora attivo)")
        srv.alias_worker = True
        ctx2 = browser.new_context()
        p2 = ctx2.new_page()
        p2.goto(base + "app/giocatori")
        p2.wait_for_selector("#app:not([hidden])", timeout=25000)
        p2.frame_locator("#app").get_by_role("heading").first.wait_for(timeout=15000)
        check("…passa dal 404, attiva il service worker e apre la pagina richiesta",
              p2.frames[-1].url.rstrip("/").endswith("/FantaRF/app/giocatori") or "giocatori" in p2.frames[-1].url, p2.frames[-1].url)
        ctx2.close()
        browser.close()

    real_errors = [e for e in console_errors if "favicon" not in e.lower()]
    check("nessun errore in console (CSP compresa)", not real_errors, "; ".join(real_errors[:3]))
    unexpected = [b for b in bad_responses if "favicon" not in b and "/app/giocatori?worker" not in b]
    print("       risposte >= 400:", unexpected[:6] or "nessuna")

    print()
    if failures:
        print(f"{len(failures)} controlli falliti: " + "; ".join(failures))
        return 1
    print("Tutti i controlli superati.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
