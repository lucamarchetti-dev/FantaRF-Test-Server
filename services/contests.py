"""Gestione dei concorsi settimanali (staff) e lettura per gli utenti."""
from __future__ import annotations

import re
from datetime import datetime

from ..db import transaction
from ..domain.contest import effective_status
from ..timeutil import iso, parse_iso, utcnow
from . import audit

MAX_INFO = 5000


class ContestError(Exception):
    """Errore di validazione/stato con messaggio mostrabile."""


def view(row, now: datetime | None = None) -> dict:
    """Riga del database + date interpretate + stato effettivo in questo istante."""
    now = now or utcnow()
    d = dict(row)
    d["opens_dt"] = parse_iso(row["opens_at"]) if row["opens_at"] else None
    d["closes_dt"] = parse_iso(row["closes_at"])
    d["effective"] = effective_status(row["status"], d["opens_dt"], d["closes_dt"], now)
    return d


def get_contest(db, contest_id: int):
    return db.execute("SELECT * FROM contests WHERE id = ?", (contest_id,)).fetchone()


def list_contests(db, season_id: int, include_drafts: bool, now: datetime | None = None) -> list[dict]:
    sql = "SELECT * FROM contests WHERE season_id = ?"
    if not include_drafts:
        sql += " AND status != 'draft'"
    rows = db.execute(sql + " ORDER BY number DESC", (season_id,)).fetchall()
    counts = {r["contest_id"]: r["n"] for r in db.execute(
        "SELECT l.contest_id, COUNT(*) AS n FROM lineups l JOIN contests c ON c.id = l.contest_id "
        "WHERE c.season_id = ? GROUP BY l.contest_id", (season_id,))}
    out = []
    for r in rows:
        d = view(r, now)
        d["lineups"] = counts.get(r["id"], 0)
        out.append(d)
    return out


def next_number(db, season_id: int) -> int:
    return (db.execute("SELECT COALESCE(MAX(number), 0) AS n FROM contests WHERE season_id = ?",
                       (season_id,)).fetchone()["n"]) + 1


def lineup_count(db, contest_id: int) -> int:
    return db.execute("SELECT COUNT(*) AS n FROM lineups WHERE contest_id = ?", (contest_id,)).fetchone()["n"]


# ------------------------------------------------------------------ validazione


def _clean_name(name: str | None, number: int) -> str:
    name = " ".join((name or "").split())
    if not name:
        return f"Concorso #{number:02d}"
    if len(name) > 60:
        raise ContestError("Il nome del concorso può avere al massimo 60 caratteri.")
    return name


def _clean_info(info: str | None) -> str:
    info = (info or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    info = re.sub(r"[^\S\n]+\n", "\n", info)
    if len(info) > MAX_INFO:
        raise ContestError(f"Le informazioni possono avere al massimo {MAX_INFO} caratteri.")
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", info):
        raise ContestError("Le informazioni contengono caratteri non ammessi.")
    return info


def _check_window(opens: datetime | None, closes: datetime | None) -> None:
    if closes is None:
        raise ContestError("La scadenza è obbligatoria.")
    if opens is not None and opens >= closes:
        raise ContestError("L'apertura deve precedere la scadenza.")


def ready_for_contest(db, season_id: int) -> bool:
    """Il listone ha abbastanza giocatori selezionabili per comporre una formazione?"""
    row = db.execute(
        """SELECT SUM(serie = 'A') AS a, SUM(serie = 'B') AS b FROM player_season_data
            WHERE season_id = ? AND valid_to IS NULL AND selectable = 1""", (season_id,)).fetchone()
    return (row["a"] or 0) >= 3 and (row["b"] or 0) >= 3


# ---------------------------------------------------------------------- azioni


def create_contest(db, *, season_id: int, number: int, name: str | None, opens_at: datetime | None,
                   closes_at: datetime | None, info: str | None, one_per_role: bool, actor_id,
                   matches_count: int = 2) -> int:
    if number is None or number < 1:
        raise ContestError("Il numero del concorso deve essere un intero positivo.")
    _check_window(opens_at, closes_at)
    name, info = _clean_name(name, number), _clean_info(info)
    if matches_count not in (1, 2, 3, 4):
        raise ContestError("Le partite del concorso devono essere da 1 a 4.")
    now = iso(utcnow())
    with transaction(db):
        season = db.execute("SELECT id, status FROM seasons WHERE id = ?", (season_id,)).fetchone()
        if season is None:
            raise ContestError("Stagione inesistente.")
        if season["status"] == "archived":
            raise ContestError("La stagione è archiviata: non si possono creare nuovi concorsi.")
        if db.execute("SELECT 1 FROM contests WHERE season_id = ? AND number = ?", (season_id, number)).fetchone():
            raise ContestError(f"Esiste già il concorso numero {number} in questa stagione.")
        cur = db.execute(
            "INSERT INTO contests (season_id, number, name, status, opens_at, closes_at, info, one_per_role, "
            "matches_count, created_at, updated_at) VALUES (?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?)",
            (season_id, number, name, iso(opens_at) if opens_at else None, iso(closes_at), info,
             1 if one_per_role else 0, matches_count, now, now))
        audit.log(db, actor_id, "contest.create", "contest", cur.lastrowid,
                  {"number": number, "name": name, "closes_at": iso(closes_at), "one_per_role": bool(one_per_role),
                   "matches_count": matches_count})
        return cur.lastrowid


def update_contest(db, contest_id: int, *, name: str | None, opens_at: datetime | None, closes_at: datetime | None,
                   info: str | None, one_per_role: bool, actor_id, matches_count: int | None = None) -> None:
    _check_window(opens_at, closes_at)
    info = _clean_info(info)
    with transaction(db):
        row = get_contest(db, contest_id)
        if row is None:
            raise ContestError("Concorso inesistente.")
        if row["status"] in ("calculating", "published"):
            raise ContestError("Il concorso è in calcolo o pubblicato: non è più modificabile da qui.")
        name = _clean_name(name, row["number"])
        changes: dict = {}
        if row["status"] == "closed":
            # Un concorso chiuso cambia solo nome e informazioni; per la scadenza serve la riapertura tracciata.
            if iso(closes_at) != row["closes_at"] or bool(one_per_role) != bool(row["one_per_role"]):
                raise ContestError("Un concorso chiuso non può cambiare scadenza o regole: usa «Riapri».")
            opens_at_iso = row["opens_at"]
        else:
            opens_at_iso = iso(opens_at) if opens_at else None
            if bool(one_per_role) != bool(row["one_per_role"]) and lineup_count(db, contest_id):
                raise ContestError("Esistono già formazioni inviate: le regole non si possono cambiare a concorso avviato.")
            if iso(closes_at) != row["closes_at"]:
                changes["closes_at"] = {"from": row["closes_at"], "to": iso(closes_at)}
            if bool(one_per_role) != bool(row["one_per_role"]):
                changes["one_per_role"] = {"from": bool(row["one_per_role"]), "to": bool(one_per_role)}
        if matches_count is None:
            matches_count = row["matches_count"]
        if matches_count not in (1, 2, 3, 4):
            raise ContestError("Le partite del concorso devono essere da 1 a 4.")
        if matches_count != row["matches_count"]:
            if row["status"] == "closed":
                raise ContestError("Un concorso chiuso non può cambiare il numero di partite.")
            changes["matches_count"] = {"from": row["matches_count"], "to": matches_count}
        if name != row["name"]:
            changes["name"] = {"from": row["name"], "to": name}
        if info != row["info"]:
            changes["info"] = "modificate"
        db.execute(
            "UPDATE contests SET name = ?, opens_at = ?, closes_at = ?, info = ?, one_per_role = ?, matches_count = ?, "
            "updated_at = ? WHERE id = ?",
            (name, opens_at_iso, iso(closes_at), info, 1 if one_per_role else 0, matches_count, iso(utcnow()), contest_id))
        if changes:
            audit.log(db, actor_id, "contest.update", "contest", contest_id, changes)


def open_contest(db, contest_id: int, actor_id, now: datetime | None = None) -> None:
    now = now or utcnow()
    with transaction(db):
        row = get_contest(db, contest_id)
        if row is None:
            raise ContestError("Concorso inesistente.")
        if row["status"] != "draft":
            raise ContestError("Solo un concorso in bozza può essere aperto.")
        if parse_iso(row["closes_at"]) <= now:
            raise ContestError("La scadenza è già passata: modifica la scadenza prima di aprire il concorso.")
        if not ready_for_contest(db, row["season_id"]):
            raise ContestError("Il listone di questa stagione non ha abbastanza giocatori selezionabili "
                               "(servono almeno 3 di Serie A e 3 di Serie B). Importa il listone prima.")
        db.execute("UPDATE contests SET status = 'open', updated_at = ? WHERE id = ?", (iso(now), contest_id))
        audit.log(db, actor_id, "contest.open", "contest", contest_id, {"closes_at": row["closes_at"]})


def close_contest(db, contest_id: int, actor_id, now: datetime | None = None) -> None:
    now = now or utcnow()
    with transaction(db):
        row = get_contest(db, contest_id)
        if row is None:
            raise ContestError("Concorso inesistente.")
        if row["status"] != "open":
            raise ContestError("Solo un concorso aperto può essere chiuso.")
        db.execute("UPDATE contests SET status = 'closed', updated_at = ? WHERE id = ?", (iso(now), contest_id))
        audit.log(db, actor_id, "contest.close", "contest", contest_id,
                  {"manual": parse_iso(row["closes_at"]) > now})


def reopen_contest(db, contest_id: int, *, new_closes_at: datetime, reason: str, actor_id,
                   now: datetime | None = None) -> None:
    """Riapertura ECCEZIONALE: richiede motivo e nuova scadenza futura, ed e' sempre registrata."""
    now = now or utcnow()
    reason = " ".join((reason or "").split())
    if len(reason) < 5 or len(reason) > 300:
        raise ContestError("Indica il motivo della riapertura (da 5 a 300 caratteri).")
    with transaction(db):
        row = get_contest(db, contest_id)
        if row is None:
            raise ContestError("Concorso inesistente.")
        v = view(row, now)
        if v["effective"] != "closed":
            raise ContestError("Si può riaprire solo un concorso chiuso.")
        if new_closes_at <= now:
            raise ContestError("La nuova scadenza deve essere nel futuro.")
        db.execute("UPDATE contests SET status = 'open', closes_at = ?, updated_at = ? WHERE id = ?",
                   (iso(new_closes_at), iso(now), contest_id))
        db.execute("INSERT INTO contest_reopenings (contest_id, user_id, old_closes_at, new_closes_at, reason, created_at) "
                   "VALUES (?, ?, ?, ?, ?, ?)",
                   (contest_id, actor_id, row["closes_at"], iso(new_closes_at), reason, iso(now)))
        audit.log(db, actor_id, "contest.reopen", "contest", contest_id,
                  {"from": row["closes_at"], "to": iso(new_closes_at), "reason": reason})


def delete_draft(db, contest_id: int, actor_id) -> None:
    with transaction(db):
        row = get_contest(db, contest_id)
        if row is None:
            raise ContestError("Concorso inesistente.")
        if row["status"] != "draft" or lineup_count(db, contest_id):
            raise ContestError("Si può eliminare solo una bozza senza formazioni.")
        db.execute("DELETE FROM contests WHERE id = ?", (contest_id,))
        audit.log(db, actor_id, "contest.delete", "contest", contest_id, {"number": row["number"], "name": row["name"]})


def reopenings(db, contest_id: int):
    return db.execute(
        "SELECT r.*, u.display_name AS user_name FROM contest_reopenings r LEFT JOIN users u ON u.id = r.user_id "
        "WHERE r.contest_id = ? ORDER BY r.id DESC", (contest_id,)).fetchall()
