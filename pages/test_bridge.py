"""Prove del ponte con CPython (senza browser). Si lancia dalla cartella che CONTIENE il pacchetto:

    python -m FantaRF.pages.test_bridge

Verifica: hash di riserva, sessione via cookie, redirect con prefisso, intestazioni per l'iframe, upload Excel
e, soprattutto, che nessuna pagina contenga link o moduli che escono dal sottopercorso della demo.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import uuid
from collections import deque
from pathlib import Path

ORIGIN = "https://utente.github.io"
PREFIX = "/FantaRF/app"
failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("  ok   " if cond else "  FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failures.append(name)


class Browser:
    """Simula ciò che fanno service worker e pagina: inoltra richieste al ponte e segue i redirect."""

    def __init__(self, B):
        self.B = B

    def request(self, method, url, data=None, headers=None):
        assert url.startswith(PREFIX), url
        rel = url[len(PREFIX):]
        path, _, query = rel.partition("?")
        status, hdrs, body, _changed = self.B.handle(method, path or "/", query, json.dumps(headers or {}), data)
        return status, dict((k.lower(), v) for k, v in json.loads(hdrs)), body

    def get(self, url, follow=True):
        for _ in range(10):
            status, h, body = self.request("GET", url)
            if follow and status in (301, 302, 303, 307, 308):
                loc = h["location"]
                url = loc[len(ORIGIN):] if loc.startswith(ORIGIN) else loc
                continue
            return status, h, body.decode("utf-8", "replace"), url
        raise AssertionError("troppi redirect")

    def form_post(self, url, fields):
        from urllib.parse import urlencode
        status, h, body = self.request("POST", url, urlencode(fields).encode(),
                                       {"content-type": "application/x-www-form-urlencoded"})
        if status in (301, 302, 303):
            loc = h["location"]
            return self.get(loc[len(ORIGIN):] if loc.startswith(ORIGIN) else loc) + (status,)
        return status, h, body.decode("utf-8", "replace"), url, status

    def csrf(self, url):
        _, _, html, _ = self.get(url)
        m = re.search(r'name="_csrf" value="([^"]+)"', html)
        return m.group(1) if m else ""

    def login(self, user, pw):
        return self.form_post(PREFIX + "/login", {"username": user, "password": pw, "_csrf": self.csrf(PREFIX + "/login")})


def main() -> int:
    from . import bridge as B
    from . import build

    print("Hash di riserva (pbkdf2 in puro Python)")
    for hn, pw, salt, it in [("sha256", b"password", b"salt", 1), ("sha256", b"Demo-2026", b"x" * 16, 500)]:
        check(f"identico a hashlib ({hn}, {it} iterazioni)",
              B._pbkdf2_py(hn, pw, salt, it) == hashlib.pbkdf2_hmac(hn, pw, salt, it))

    print("Database demo e avvio")
    tmp = tempfile.mkdtemp(prefix="fantarf-bridge-test-")
    demo = Path(tmp) / "demo.sqlite3"
    build.build_demo_db(demo)
    state = Path(tmp) / "state"
    state.mkdir()
    shutil.copy(demo, state / B.DB_NAME)
    diag = json.loads(B.init(ORIGIN, PREFIX, str(state)))
    check("l'app parte con il database demo", diag["sqlite"] != "")
    check("primo avvio: crea il concorso demo aperto", B.first_run() is True)
    check("secondo avvio: non ne crea un altro", B.first_run() is False)

    br = Browser(B)
    print("Pagine pubbliche e sessione")
    status, h, html, _ = br.get(PREFIX + "/")
    check("home → 200", status == 200)
    check("X-Frame-Options consentito alla stessa origine", h.get("x-frame-options") == "SAMEORIGIN", str(h.get("x-frame-options")))
    check("CSP: frame-ancestors 'self'", "frame-ancestors 'self'" in h.get("content-security-policy", ""))
    check("nessun Set-Cookie esce dal ponte (lo gestisce il barattolo)", "set-cookie" not in h)
    status, h, _, _ = br.get(PREFIX + "/staff/", follow=False)
    check("area staff senza login → redirect con prefisso", status == 302 and h["location"].startswith(PREFIX + "/login"), str(h.get("location")))
    status, h, body = br.request("GET", PREFIX + "/classifica")
    check("redirect di Flask (slash finale) → URL assoluto con prefisso",
          status == 308 and h["location"] == ORIGIN + PREFIX + "/classifica/", str(h.get("location")))

    print("Login con cookie")
    bad = br.login("admin", "password-sbagliata")
    check("password errata rifiutata", bad[0] == 401, str(bad[0]))
    ok = br.login("admin", "DemoAdmin-2026")
    check("login corretto → redirect alla home con prefisso", ok[0] == 200 and ok[3] == PREFIX + "/", f"{ok[0]} {ok[3]}")
    check("…e ora l'area staff è accessibile", br.get(PREFIX + "/staff/", follow=False)[0] == 200)
    check("il barattolo contiene la sessione", "session" in json.loads(B.cookies_json()))
    saved = B.cookies_json()
    B.reset_cookies()
    check("senza cookie si è disconnessi", br.get(PREFIX + "/staff/", follow=False)[0] == 302)
    B._S["jar"] = B.CookieJar(json.loads(saved))
    check("ripristinando i cookie salvati si è di nuovo dentro", br.get(PREFIX + "/staff/", follow=False)[0] == 200)

    print("Funzioni dello staff")
    status, _, html, _ = br.get(PREFIX + "/staff/punteggi/")
    check("pagina Punteggi con il campo SV", status == 200 and 'name="sv_vote"' in html)
    status, _, html, url, _ = br.form_post(PREFIX + "/staff/utenti", {
        "_csrf": br.csrf(PREFIX + "/staff/utenti"), "username": "nuovo-utente", "display_name": "Nuovo", "role": "user",
        })
    temp = re.search(r"Password provvisoria</dt>\s*<dd><code>([^<]+)</code>", html)
    check("creazione utente dalla demo: mostra la password provvisoria",
          status == 200 and "nuovo-utente" in html and temp is not None, f"{status}")
    temp_pw = temp.group(1) if temp else ""
    other = Browser(B)
    saved_jar = B.cookies_json()
    B.reset_cookies()
    r = other.login("nuovo-utente", temp_pw)
    check("il nuovo utente accede con la password provvisoria (hash pbkdf2 verificato)",
          "session" in json.loads(B.cookies_json()) and "/password" in r[3], f"{r[0]} {r[3]}")
    B.reset_cookies()
    r = Browser(B).login("nuovo-utente", "password-diversa-xx")
    check("…e una password diversa no", r[0] == 401, f"{r[0]}")
    B._S["jar"] = B.CookieJar(json.loads(saved_jar))

    print("Upload di un file Excel (multipart)")
    seed = Path(__file__).resolve().parent.parent / "seed" / "Elenco_Giocatori_Fantacalcio.xlsx"
    boundary = "----" + uuid.uuid4().hex
    token = br.csrf(PREFIX + "/staff/import")
    parts = []
    for k, v in {"_csrf": token, "season_id": "1", "sheet": "Consultazione"}.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="listone.xlsx"\r\n'
                  "Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n\r\n").encode()
                 + seed.read_bytes() + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    status, h, body = br.request("POST", PREFIX + "/staff/import", b"".join(parts),
                                 {"content-type": f"multipart/form-data; boundary={boundary}"})
    check("upload accettato → redirect all'anteprima", status == 302 and re.search(r"/staff/import/\d+$", h["location"] or ""), f"{status} {h.get('location')}")
    if status == 302:
        loc = h["location"]
        st, _, html, _ = br.get(loc[len(ORIGIN):] if loc.startswith(ORIGIN) else loc)
        check("anteprima dell'importazione → 200", st == 200 and "righe" in html.lower())

    print("Percorsi: nessun link deve uscire dal sottopercorso della demo")
    for who, creds in (("staff", ("admin", "DemoAdmin-2026")), ("partecipante", ("tester", "DemoTester-2026"))):
        b2 = Browser(B)
        B.reset_cookies()
        r = b2.login(*creds)
        check(f"accesso come {who}", r[0] == 200, f"{r[0]} {r[3]}")
        seen, queue, escapes, errors = set(), deque([PREFIX + "/"]), [], []
        while queue and len(seen) < 250:
            url = queue.popleft().split("#")[0]
            if url in seen:
                continue
            seen.add(url)
            status, h, html, final = b2.get(url)
            if status >= 500:
                errors.append(f"{status} {url}")
            if "text/html" not in h.get("content-type", ""):
                continue
            for attr, val in re.findall(r'\b(href|src|action)="([^"]*)"', html):
                if val.startswith("/") and not val.startswith(PREFIX):
                    escapes.append(f"{attr}={val} in {url}")
                elif attr == "href" and val.startswith(PREFIX + "/") and "/static/" not in val and "logout" not in val:
                    queue.append(val)
        print(f"       ({who}: {len(seen)} pagine visitate)")
        check(f"{who}: nessuna pagina risponde con errore 5xx", not errors, "; ".join(errors[:3]))
        check(f"{who}: nessun link/modulo/risorsa fuori prefisso", not escapes, "; ".join(sorted(set(escapes))[:5]))

    print()
    if failures:
        print(f"{len(failures)} controlli falliti: " + "; ".join(failures))
        return 1
    print("Tutti i controlli superati.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
