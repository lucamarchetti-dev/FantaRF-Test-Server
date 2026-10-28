"""Costruisce la cartella `site/` pubblicata su GitHub Pages (demo che gira interamente nel browser).

Si lancia dalla cartella che CONTIENE il pacchetto (il repository si chiama FantaRF):

    python -m FantaRF.pages.build --out site            # in CI: scarica anche le librerie Python
    python -m FantaRF.pages.build --out site --skip-wheels   # senza rete (prove in locale)

Cosa produce:
    index.html, shell.js, sw.js, worker.js, persist.js, style.css, 404.html   pagina e service worker
    bridge.py        ponte Python <-> browser
    bundle.zip       il pacchetto FantaRF (codice, template, migrazioni)
    static/          CSS, JS e stemmi serviti direttamente dal sito
    Elenco_Giocatori_Fantacalcio.xlsx   listone d'esempio scaricabile dal banner
    demo.sqlite3     database iniziale: stagione S13, listone importato, utenti demo
    wheels/          librerie Python pure (Flask, Werkzeug, Jinja2, openpyxl…) + manifest.json
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent          # radice del repository = pacchetto FantaRF
WEB = Path(__file__).resolve().parent / "web"
PY_VERSION_IN_BROWSER = "3.12"                          # Pyodide 0.26.x

# Librerie pure Python (wheel "any") che Pyodide non include. MarkupSafe viene da Pyodide stesso.
WHEELS = [
    "Flask==3.1.3", "Werkzeug==3.1.7", "Jinja2==3.1.6", "itsdangerous==2.2.0", "click==8.3.1",
    "blinker==1.9.0", "openpyxl==3.1.5", "et_xmlfile==2.0.0", "tzdata",
]

EXCLUDE_DIRS = {".git", ".github", "pages", "deploy", "seed", "__pycache__", "instance", ".venv", "venv", "node_modules"}
EXCLUDE_SUFFIXES = {".pyc", ".sqlite3", ".sqlite", ".db", ".xlsx", ".md", ".zip"}
EXCLUDE_NAMES = {"Dockerfile", ".dockerignore", ".gitattributes", "render.yaml", ".gitignore"}


def log(msg: str) -> None:
    print(f"[build] {msg}", flush=True)


# ------------------------------------------------------------------ database demo

def build_demo_db(dest: Path) -> None:
    from . import bridge
    tmp = Path(tempfile.mkdtemp(prefix="fantarf-demo-"))
    os.environ.update({
        "FANTAPG_DATABASE": str(tmp / bridge.DB_NAME), "FANTAPG_UPLOAD_DIR": str(tmp / "uploads"),
        "FANTAPG_INSTANCE_PATH": str(tmp / "instance"), "FANTAPG_SECRET_KEY": "build",
        "FANTAPG_JOURNAL_MODE": "DELETE",
    })
    bridge.patch_crypto()          # hash pbkdf2: l'unico verificabile nel browser

    from .. import create_app
    from .. import db as dbm
    from ..deploy import bootstrap
    from ..services import users as users_svc

    app = create_app()
    conn = dbm.connect(app.config["DATABASE"])
    try:
        season = bootstrap.ensure_season(conn)
        bootstrap.ensure_listone(conn, app.config, season)
        for username, display, password in (bridge.DEMO_ADMIN, bridge.DEMO_TESTER):
            role = "staff" if (username, display, password) == bridge.DEMO_ADMIN else "user"
            users_svc.create_user(conn, username=username, display_name=display, role=role,
                                  password=password, actor_id=None, must_change=False)
        conn.execute("VACUUM")
    finally:
        conn.close()
    path = tmp / bridge.DB_NAME
    header = path.read_bytes()[:100]
    if header[18:20] != b"\x01\x01":
        raise SystemExit("Il database demo è in modalità WAL: nel browser non è supportata.")
    shutil.copy(path, dest)
    log(f"database demo: {dest.stat().st_size // 1024} KB")


# ------------------------------------------------------------------ pacchetto dell'app

def build_bundle(dest: Path) -> None:
    count = 0
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for root, dirs, files in os.walk(PKG):
            dirs[:] = sorted(d for d in dirs if d not in EXCLUDE_DIRS)
            for name in sorted(files):
                p = Path(root) / name
                if p.suffix in EXCLUDE_SUFFIXES or name in EXCLUDE_NAMES:
                    continue
                z.write(p, Path("FantaRF") / p.relative_to(PKG))
                count += 1
    log(f"bundle.zip: {count} file, {dest.stat().st_size // 1024} KB")


def fetch_wheels(out: Path, skip: bool) -> None:
    out.mkdir(parents=True, exist_ok=True)
    if skip:
        (out / "manifest.json").write_text("[]")
        log("librerie Python: saltate (--skip-wheels). La demo NON partirà nel browser.")
        return
    cmd = [sys.executable, "-m", "pip", "download", "--only-binary=:all:", "--no-deps", "--platform", "any",
           "--python-version", PY_VERSION_IN_BROWSER, "-d", str(out), *WHEELS]
    subprocess.run(cmd, check=True)
    names = sorted(p.name for p in out.glob("*.whl"))
    expected = {"flask", "werkzeug", "jinja2", "itsdangerous", "click", "blinker", "openpyxl", "et_xmlfile", "tzdata"}
    found = {n.split("-")[0].lower().replace("-", "_") for n in names}
    missing = expected - found
    if missing:
        raise SystemExit(f"Librerie mancanti nella cartella wheels: {sorted(missing)}")
    (out / "manifest.json").write_text(json.dumps(names))
    log(f"librerie Python: {len(names)} wheel")


# ------------------------------------------------------------------ assemblaggio

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="site", help="cartella di destinazione (viene svuotata)")
    ap.add_argument("--skip-wheels", action="store_true", help="non scaricare le librerie (senza rete)")
    args = ap.parse_args()

    out = Path(args.out).resolve()
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    build_id = (os.environ.get("GITHUB_SHA", "")[:7] or "local") + "-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
    for src in WEB.iterdir():
        text = src.read_text(encoding="utf-8").replace("__BUILD__", build_id)
        (out / src.name).write_text(text, encoding="utf-8")
    shutil.copy(Path(__file__).with_name("bridge.py"), out / "bridge.py")
    shutil.copytree(PKG / "static", out / "static")
    seed = PKG / "seed" / "Elenco_Giocatori_Fantacalcio.xlsx"
    if seed.is_file():
        shutil.copy(seed, out / seed.name)         # per provare l'importazione dello staff
    (out / ".nojekyll").write_text("")
    (out / "build.json").write_text(json.dumps({"build": build_id}))

    build_bundle(out / "bundle.zip")
    build_demo_db(out / "demo.sqlite3")
    fetch_wheels(out / "wheels", args.skip_wheels)
    total = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    log(f"sito pronto in {out} ({total // 1024} KB, build {build_id})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
