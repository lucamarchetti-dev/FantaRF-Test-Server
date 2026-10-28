"""Strike: assegnati dallo staff, mai cancellati (uno strike sbagliato si annulla e resta nel registro)."""
from __future__ import annotations

from ..db import transaction
from ..timeutil import utcnow_iso
from . import audit


class StrikeError(Exception):
    pass


def _reason(text, minimum=3):
    text = " ".join((text or "").split())
    if len(text) < minimum or len(text) > 300:
        raise StrikeError(f"Indica il motivo (da {minimum} a 300 caratteri).")
    return text


def counts(db, season_id: int) -> dict[int, int]:
    return {r["user_id"]: r["n"] for r in db.execute(
        "SELECT user_id, COUNT(*) AS n FROM strikes WHERE season_id = ? AND voided_at IS NULL GROUP BY user_id", (season_id,))}


def add_strike(db, season_id: int, user_id: int, contest_id: int | None, reason: str, actor_id, max_strikes: int = 3) -> dict:
    reason = _reason(reason)
    with transaction(db):
        user = db.execute("SELECT id, username, display_name FROM users WHERE id = ?", (user_id,)).fetchone()
        if user is None:
            raise StrikeError("Utente inesistente.")
        if contest_id is not None and not db.execute("SELECT 1 FROM contests WHERE id = ? AND season_id = ?", (contest_id, season_id)).fetchone():
            raise StrikeError("Il concorso non appartiene a questa stagione.")
        cur = db.execute("INSERT INTO strikes (season_id, user_id, contest_id, reason, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                         (season_id, user_id, contest_id, reason, actor_id, utcnow_iso()))
        n = db.execute("SELECT COUNT(*) AS n FROM strikes WHERE season_id = ? AND user_id = ? AND voided_at IS NULL", (season_id, user_id)).fetchone()["n"]
        audit.log(db, actor_id, "strike.add", "strike", cur.lastrowid, {"user": user["username"], "contest_id": contest_id, "reason": reason, "count": n})
        return {"id": cur.lastrowid, "count": n, "limit_reached": n >= max_strikes, "name": user["display_name"]}


def void_strike(db, strike_id: int, reason: str, actor_id) -> None:
    reason = _reason(reason)
    with transaction(db):
        row = db.execute("SELECT * FROM strikes WHERE id = ?", (strike_id,)).fetchone()
        if row is None:
            raise StrikeError("Strike inesistente.")
        if row["voided_at"]:
            raise StrikeError("Questo strike è già stato annullato.")
        db.execute("UPDATE strikes SET voided_at = ?, voided_by = ?, void_reason = ? WHERE id = ?", (utcnow_iso(), actor_id, reason, strike_id))
        audit.log(db, actor_id, "strike.void", "strike", strike_id, {"user_id": row["user_id"], "reason": reason})


def list_strikes(db, season_id: int):
    return db.execute(
        """SELECT s.*, u.display_name, u.username, c.name AS contest_name, cb.display_name AS by_name, vb.display_name AS void_by_name
             FROM strikes s JOIN users u ON u.id = s.user_id LEFT JOIN contests c ON c.id = s.contest_id
             LEFT JOIN users cb ON cb.id = s.created_by LEFT JOIN users vb ON vb.id = s.voided_by
            WHERE s.season_id = ? ORDER BY s.id DESC""", (season_id,)).fetchall()


def rejected_attempts(db, season_id: int, limit: int = 30):
    """Tentativi di invio respinti dal sistema (formazioni non valide): un aiuto allo staff per decidere sugli strike."""
    return db.execute(
        """SELECT a.created_at, a.details_json, u.display_name, c.name AS contest_name
             FROM audit_log a JOIN users u ON u.id = a.user_id JOIN contests c ON c.id = CAST(a.entity_id AS INTEGER)
            WHERE a.action = 'lineup.rejected' AND c.season_id = ? ORDER BY a.id DESC LIMIT ?""", (season_id, limit)).fetchall()
