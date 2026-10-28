"""Preparazione del database all'avvio del server di test. Idempotente: si può lanciare a ogni avvio.

Cosa fa, solo se manca:
  1. un account staff, dalle variabili FANTAPG_BOOTSTRAP_ADMIN_USER / _PASSWORD (cambio password obbligatorio al primo accesso);
  2. una stagione (FANTAPG_BOOTSTRAP_SEASON, default S13), resa attiva;
  3. il listone, importato da FANTAPG_SEED_FILE (default seed/Elenco_Giocatori_Fantacalcio.xlsx).

Se il database è stato ripristinato da Litestream, trova tutto già presente e non tocca nulla:
nessuna sovrascrittura di concorsi, formazioni o utenti creati dallo staff.

Si lancia dalla cartella che CONTIENE il pacchetto:  python -m FantaRF.deploy.bootstrap
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from .. import create_app
from .. import db as dbm
from ..security import password_problem
from ..services import imports as imports_svc
from ..services import players as players_svc
from ..services import seasons as seasons_svc
from ..services import users as users_svc


def log(msg: str) -> None:
    print(f"[bootstrap] {msg}", flush=True)


def ensure_admin(conn, cfg) -> None:
    if conn.execute("SELECT 1 FROM users WHERE role = 'staff' LIMIT 1").fetchone():
        log("account staff già presente: niente da fare.")
        return
    username = os.environ.get("FANTAPG_BOOTSTRAP_ADMIN_USER", "").strip()
    password = os.environ.get("FANTAPG_BOOTSTRAP_ADMIN_PASSWORD", "")
    if not username or not password:
        log("ATTENZIONE: nessun account staff e FANTAPG_BOOTSTRAP_ADMIN_USER/_PASSWORD non impostate: "
            "nessuno potrà accedere allo staff.")
        return
    problem = password_problem(password, cfg["MIN_PASSWORD_LENGTH"])
    if problem:
        log(f"ERRORE: password dell'admin non valida ({problem}). Account non creato.")
        return
    display = os.environ.get("FANTAPG_BOOTSTRAP_ADMIN_NAME", "").strip() or username
    users_svc.create_user(conn, username=username, display_name=display, role="staff",
                          password=password, actor_id=None, must_change=True)
    log(f"creato l'account staff «{username}» (dovrà cambiare la password al primo accesso).")


def ensure_season(conn) -> dict | None:
    active = seasons_svc.active_season(conn)
    if active is not None:
        log(f"stagione attiva già presente: {active['name']}.")
        return active
    if conn.execute("SELECT 1 FROM seasons LIMIT 1").fetchone():
        log("esistono stagioni ma nessuna è attiva: la scelta è dello staff, non la cambio.")
        return None
    name = os.environ.get("FANTAPG_BOOTSTRAP_SEASON", "S13").strip() or "S13"
    sid = seasons_svc.create_season(conn, name, None, None, None)
    seasons_svc.activate_season(conn, sid, None)
    log(f"creata e attivata la stagione «{name}».")
    return seasons_svc.active_season(conn)


def ensure_listone(conn, cfg, season) -> None:
    if season is None:
        return
    if players_svc.season_stats(conn, season["id"])["total"] > 0:
        log("listone già presente: nessuna importazione.")
        return
    seed = Path(os.environ.get("FANTAPG_SEED_FILE", Path(__file__).resolve().parent.parent / "seed"
                               / "Elenco_Giocatori_Fantacalcio.xlsx"))
    if not seed.is_file():
        log(f"nessun file di partenza ({seed}): importa il listone da Staff → Importa il listone.")
        return
    sheet = os.environ.get("FANTAPG_SEED_SHEET", "Consultazione")
    with open(seed, "rb") as fh:
        path, sha = imports_svc.store_upload(cfg["UPLOAD_DIR"], fh.read())
    batch_id = imports_svc.create_batch(conn, season_id=season["id"], user_id=None, filename=seed.name,
                                        sheet=sheet, path=path, sha256=sha)
    batch = imports_svc.get_batch(conn, batch_id)
    preview = imports_svc.build_preview(conn, batch, cfg["MAX_IMPORT_ROWS"])
    if not preview.ready:
        imports_svc.cancel_batch(conn, batch_id, None)
        log("ERRORE: il file di partenza non ha colonne riconoscibili o righe valide: importa a mano dall'interfaccia.")
        return
    summary = imports_svc.apply_batch(conn, batch_id, None, cfg["MAX_IMPORT_ROWS"])
    log(f"listone importato: {summary['rows_valid']} righe valide, {summary['rows_rejected']} scartate "
        f"({summary['new']} giocatori nuovi).")


def main() -> int:
    app = create_app()          # applica anche le migrazioni
    cfg = app.config
    conn = dbm.connect(cfg["DATABASE"])
    try:
        ensure_admin(conn, cfg)
        season = ensure_season(conn)
        ensure_listone(conn, cfg, season)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
