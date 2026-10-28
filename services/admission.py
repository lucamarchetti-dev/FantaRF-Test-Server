"""Ammissione alla stagione: lo staff puo' escludere un utente (es. sanzioni) e riammetterlo."""
from __future__ import annotations

from ..db import transaction
from ..timeutil import utcnow_iso
from . import audit


class AdmissionError(Exception):
    pass


def is_excluded(db, season_id: int, user_id: int):
    """Restituisce la riga di esclusione (con il motivo) oppure None."""
    return db.execute("SELECT * FROM season_exclusions WHERE season_id = ? AND user_id = ?",
                      (season_id, user_id)).fetchone()


def exclusions_map(db, season_id: int) -> dict[int, dict]:
    return {r["user_id"]: dict(r) for r in db.execute(
        "SELECT * FROM season_exclusions WHERE season_id = ?", (season_id,))}


def exclude(db, season_id: int, user_id: int, reason: str, actor_id: int) -> None:
    reason = " ".join((reason or "").split())
    if len(reason) < 3 or len(reason) > 300:
        raise AdmissionError("Indica il motivo dell'esclusione (da 3 a 300 caratteri).")
    with transaction(db):
        user = db.execute("SELECT id, username FROM users WHERE id = ?", (user_id,)).fetchone()
        if user is None:
            raise AdmissionError("Utente inesistente.")
        if is_excluded(db, season_id, user_id):
            raise AdmissionError("L'utente è già escluso da questa stagione.")
        db.execute("INSERT INTO season_exclusions (season_id, user_id, reason, created_by, created_at) VALUES (?, ?, ?, ?, ?)",
                   (season_id, user_id, reason, actor_id, utcnow_iso()))
        audit.log(db, actor_id, "season.exclude", "user", user_id,
                  {"username": user["username"], "season_id": season_id, "reason": reason})


def readmit(db, season_id: int, user_id: int, actor_id: int) -> None:
    with transaction(db):
        row = is_excluded(db, season_id, user_id)
        if row is None:
            raise AdmissionError("L'utente non è escluso da questa stagione.")
        db.execute("DELETE FROM season_exclusions WHERE season_id = ? AND user_id = ?", (season_id, user_id))
        audit.log(db, actor_id, "season.readmit", "user", user_id,
                  {"season_id": season_id, "previous_reason": row["reason"]})
