"""Ponte tra il browser e l'app Flask FantaRF.

Gira dentro Pyodide (Python compilato in WebAssembly) nella demo su GitHub Pages, ma è puro Python:
la stessa identica logica si prova anche con CPython (vedi pages/test_bridge.py).

Il service worker del browser trasforma ogni richiesta HTTP in una chiamata a `handle()`, che esegue l'app
come una normale applicazione WSGI e restituisce stato, intestazioni e corpo della risposta.

Perché serve un ponte e non basta "avviare Flask":
  - il browser non permette a un service worker di impostare i cookie: la sessione è tenuta qui, in un
    piccolo "barattolo" di cookie salvato insieme al database;
  - l'app vieta di essere inserita in un iframe: nella demo lo consentiamo solo alla stessa origine;
  - in WebAssembly mancano scrypt e (a volte) pbkdf2 di OpenSSL: per la demo l'hash delle password è
    pbkdf2 con poche iterazioni (i dati restano nel browser di chi prova, non su un server).
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

DEMO_PBKDF2_ITERATIONS = 20_000
DB_NAME = "fantapg.sqlite3"

DEMO_ADMIN = ("admin", "Amministratore demo", "DemoAdmin-2026")
DEMO_TESTER = ("tester", "Partecipante demo", "DemoTester-2026")

_S: dict = {}          # stato del ponte: app, client, cookie, percorsi


# ------------------------------------------------------------------ crittografia adattata alla demo

def _pbkdf2_py(hash_name, password, salt, iterations, dklen=None):
    """PBKDF2 in puro Python, usata solo se hashlib non offre pbkdf2_hmac (WebAssembly)."""
    import hmac
    mac = hmac.new(password, digestmod=hash_name)
    size = mac.digest_size
    dklen = dklen or size
    out, block = b"", 1
    while len(out) < dklen:
        h = mac.copy()
        h.update(salt + block.to_bytes(4, "big"))
        u = h.digest()
        t = int.from_bytes(u, "big")
        for _ in range(iterations - 1):
            h = mac.copy()
            h.update(u)
            u = h.digest()
            t ^= int.from_bytes(u, "big")
        out += t.to_bytes(size, "big")
        block += 1
    return out[:dklen]


def patch_crypto(iterations: int = DEMO_PBKDF2_ITERATIONS) -> None:
    """Va chiamata PRIMA di importare l'app (security.py calcola un hash all'import)."""
    import werkzeug.security as ws
    if not hasattr(hashlib, "pbkdf2_hmac"):
        hashlib.pbkdf2_hmac = _pbkdf2_py
    if getattr(ws, "_fantarf_patched", False):
        return
    original = ws.generate_password_hash

    def generate_password_hash(password, method=f"pbkdf2:sha256:{iterations}", salt_length=16):
        return original(password, method, salt_length)

    ws.generate_password_hash = generate_password_hash
    ws._fantarf_patched = True


# ------------------------------------------------------------------ cookie (sessione)

class CookieJar:
    def __init__(self, data: dict | None = None):
        self.items: dict[str, str] = dict(data or {})

    def header(self) -> str:
        return "; ".join(f"{k}={v}" for k, v in self.items.items())

    def update(self, set_cookie_headers) -> bool:
        changed = False
        for raw in set_cookie_headers:
            parts = [p.strip() for p in raw.split(";")]
            name, _, value = parts[0].partition("=")
            attrs = {}
            for p in parts[1:]:
                k, _, v = p.partition("=")
                attrs[k.strip().lower()] = v.strip()
            expired = attrs.get("max-age", "") in ("0",) or value == ""
            if "expires" in attrs:
                try:
                    expired = expired or parsedate_to_datetime(attrs["expires"]) <= datetime.now(timezone.utc)
                except (TypeError, ValueError):
                    pass
            if expired:
                changed |= self.items.pop(name, None) is not None
            elif self.items.get(name) != value:
                self.items[name] = value
                changed = True
        return changed

    def dumps(self) -> str:
        return json.dumps(self.items)


# ------------------------------------------------------------------ avvio

def init(origin: str, script_name: str, state_dir: str, cookies_json: str = "{}", embed: bool = True) -> str:
    """Prepara l'app. `state_dir` contiene fantapg.sqlite3 (già scritto dal chiamante).

    origin       es. "https://utente.github.io";  script_name  es. "/FantaRF/app" (senza slash finale).
    """
    os.makedirs(state_dir, exist_ok=True)
    db_path = os.path.join(state_dir, DB_NAME)
    os.environ.update({
        "FANTAPG_DATABASE": db_path,
        "FANTAPG_UPLOAD_DIR": os.path.join(state_dir, "uploads"),
        "FANTAPG_INSTANCE_PATH": os.path.join(state_dir, "instance"),
        "FANTAPG_SECRET_KEY": "demo-nel-browser-nessun-segreto",
        "FANTAPG_JOURNAL_MODE": "DELETE",
    })
    os.environ.pop("FANTAPG_SECURE_COOKIES", None)
    os.environ.pop("FANTAPG_TRUST_PROXY", None)
    patch_crypto()

    from werkzeug.test import Client
    from FantaRF import create_app

    app = create_app()
    _S.update(app=app, client=Client(app, use_cookies=False), origin=origin.rstrip("/"),
              script_name=script_name.rstrip("/"), db_path=db_path, embed=embed,
              jar=CookieJar(json.loads(cookies_json or "{}")))
    return diagnostics()


def diagnostics() -> str:
    import sqlite3
    return json.dumps({
        "python": sys.version.split()[0],
        "sqlite": sqlite3.sqlite_version,
        "hashlib.scrypt": hasattr(hashlib, "scrypt"),
        "hashlib.pbkdf2_hmac": hasattr(hashlib, "pbkdf2_hmac"),
        "pbkdf2_fallback": hashlib.pbkdf2_hmac is _pbkdf2_py,
    })


def first_run() -> bool:
    """Alla prima apertura (nessuno stato salvato) crea un concorso aperto, così si può provare subito."""
    from FantaRF import db as dbm
    from FantaRF.services import contests as contests_svc
    from FantaRF.services import seasons as seasons_svc
    from FantaRF.timeutil import utcnow

    conn = dbm.connect(_S["db_path"])
    try:
        if conn.execute("SELECT 1 FROM contests LIMIT 1").fetchone():
            return False
        season = seasons_svc.active_season(conn)
        if season is None:
            return False
        now = utcnow()
        cid = contests_svc.create_contest(
            conn, season_id=season["id"], number=1, name="Concorso 1 (demo)",
            opens_at=now - timedelta(hours=1), closes_at=now + timedelta(days=14),
            info="Concorso creato automaticamente per la demo.", one_per_role=True, actor_id=None)
        contests_svc.open_contest(conn, cid, None, now=now)
        return True
    finally:
        conn.close()


# ------------------------------------------------------------------ richieste

_DROP_RESPONSE = {"set-cookie", "content-length", "transfer-encoding", "connection", "keep-alive"}
_DROP_REQUEST = {"cookie", "content-length", "host", "connection"}


def _to_bytes(body) -> bytes:
    if body is None:
        return b""
    if hasattr(body, "to_py"):           # JsProxy (Uint8Array) in Pyodide
        body = body.to_py()
    return bytes(body)


def handle(method: str, path: str, query: str, headers_json: str, body=None, referrer: str = ""):
    """Esegue una richiesta. `path` è relativo all'app (es. "/giocatori"), senza il prefisso della demo.

    Ritorna (stato:int, intestazioni_json:str, corpo:bytes, cookie_cambiati:bool).
    """
    from werkzeug.test import EnvironBuilder

    S = _S
    data = _to_bytes(body)
    headers = [(k, v) for k, v in json.loads(headers_json or "{}").items() if k.lower() not in _DROP_REQUEST]
    cookie = S["jar"].header()
    if cookie:
        headers.append(("Cookie", cookie))
    if referrer:
        headers.append(("Referer", referrer))
    if method.upper() not in ("GET", "HEAD", "OPTIONS"):
        headers.append(("Origin", S["origin"]))

    builder = EnvironBuilder(
        path=path or "/", base_url=f"{S['origin']}{S['script_name']}/", query_string=query or "",
        method=method.upper(), headers=headers,
        input_stream=io.BytesIO(data) if data else None, content_length=len(data) if data else None,
        environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
    resp = S["client"].open(builder, buffered=True)

    changed = S["jar"].update(resp.headers.getlist("Set-Cookie"))
    out = []
    for k, v in resp.headers:
        lk = k.lower()
        if lk in _DROP_RESPONSE:
            continue
        if S["embed"]:     # la demo vive in un iframe della stessa origine
            if lk == "x-frame-options":
                v = "SAMEORIGIN"
            elif lk == "content-security-policy":
                v = v.replace("frame-ancestors 'none'", "frame-ancestors 'self'")
        out.append([k, v])
    return resp.status_code, json.dumps(out), resp.get_data(), changed


def cookies_json() -> str:
    return _S["jar"].dumps()


def reset_cookies() -> None:
    _S["jar"] = CookieJar()
