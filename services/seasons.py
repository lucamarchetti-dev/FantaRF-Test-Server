from __future__ import annotations

import re
from datetime import date

from ..db import transaction
from ..timeutil import utcnow_iso
from . import audit


class SeasonError(Exception):
    pass


def list_seasons(db):
    return db.execute(
        """SELECT s.*, (SELECT COUNT(*) FROM player_season_data psd
                         WHERE psd.season_id = s.id AND psd.valid_to IS NULL) AS players
             FROM seasons s ORDER BY s.id DESC"""
    ).fetchall()


def active_season(db):
    return db.execute("SELECT * FROM seasons WHERE status = 'active'").fetchone()


def get_season(db, season_id: int):
    return db.execute("SELECT * FROM seasons WHERE id = ?", (season_id,)).fetchone()


def _valid_date(text: str | None) -> str | None:
    text = (text or "").strip()
    if not text:
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise SeasonError("Le date devono avere il formato AAAA-MM-GG.")
    try:
        date.fromisoformat(text)
    except ValueError as exc:
        raise SeasonError("Data non valida.") from exc
    return text


def create_season(db, name: str, starts_on: str | None, ends_on: str | None, actor_id: int) -> int:
    name = " ".join((name or "").split())
    if not name or len(name) > 40:
        raise SeasonError("Il nome della stagione è obbligatorio (massimo 40 caratteri).")
    starts, ends = _valid_date(starts_on), _valid_date(ends_on)
    if starts and ends and ends < starts:
        raise SeasonError("La data di fine non può precedere quella di inizio.")
    with transaction(db):
        if db.execute("SELECT 1 FROM seasons WHERE name = ?", (name,)).fetchone():
            raise SeasonError("Esiste già una stagione con questo nome.")
        cur = db.execute(
            "INSERT INTO seasons (name, status, starts_on, ends_on, created_at) VALUES (?, 'draft', ?, ?, ?)",
            (name, starts, ends, utcnow_iso()),
        )
        audit.log(db, actor_id, "season.create", "season", cur.lastrowid, {"name": name})
    return cur.lastrowid


def activate_season(db, season_id: int, actor_id: int) -> None:
    """Rende attiva una stagione; l'eventuale stagione attiva precedente viene archiviata."""
    with transaction(db):
        season = db.execute("SELECT id, name, status FROM seasons WHERE id = ?", (season_id,)).fetchone()
        if season is None:
            raise SeasonError("Stagione inesistente.")
        if season["status"] == "active":
            return
        previous = db.execute("SELECT id, name FROM seasons WHERE status = 'active'").fetchone()
        if previous:
            db.execute("UPDATE seasons SET status = 'archived' WHERE id = ?", (previous["id"],))
        db.execute("UPDATE seasons SET status = 'active' WHERE id = ?", (season_id,))
        audit.log(db, actor_id, "season.activate", "season", season_id,
                  {"name": season["name"], "archived_previous": previous["name"] if previous else None})
