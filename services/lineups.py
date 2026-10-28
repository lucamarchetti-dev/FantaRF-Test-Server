"""Invio e lettura delle formazioni.

Principi:
- si salvano SOLO formazioni valide: una giocata non valida viene rifiutata con la spiegazione
  e non compare mai come valida;
- ogni invio valido crea una nuova VERSIONE immutabile; ai fini del concorso conta l'ultima;
- scadenza, ammissione e validazione si controllano DENTRO la transazione che scrive,
  quindi non si puo' inviare tardi per una corsa a ridosso della scadenza.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from datetime import datetime

from ..db import transaction
from ..domain.contest import effective_status
from ..domain.lineup import Candidate, Issue, Rules, sort_key, validate
from ..timeutil import iso, parse_iso, to_local_long, utcnow
from . import admission, audit
from .contests import view as contest_view

MAX_TEAM_NAME = 40
MAX_REQUESTED_IDS = 20


@dataclass
class SubmitResult:
    ok: bool
    outcome: str = ""            # created | replaced | unchanged | rejected
    version: int | None = None
    submitted_at: str | None = None
    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)
    selected: list[Candidate] = field(default_factory=list)
    blocking: bool = False       # True se il problema non dipende dalla scelta dei giocatori (concorso chiuso, ecc.)


def clean_team_name(text: str | None) -> tuple[str | None, str | None]:
    name = " ".join((text or "").split())
    if not name:
        return None, None
    if len(name) > MAX_TEAM_NAME:
        return None, f"Il nome del FantaTeam può avere al massimo {MAX_TEAM_NAME} caratteri."
    if any(unicodedata.category(ch).startswith("C") for ch in name):
        return None, "Il nome del FantaTeam contiene caratteri non ammessi."
    return name, None


def parse_ids(raw_values) -> tuple[list[int], bool]:
    """Converte gli ID inviati dal modulo. Restituisce (ID validi, c'era qualcosa di non numerico)."""
    ids, bad = [], False
    for raw in list(raw_values)[:MAX_REQUESTED_IDS]:
        try:
            value = int(str(raw).strip())
            if value <= 0:
                raise ValueError
            ids.append(value)
        except ValueError:
            bad = True
    return ids, bad


def load_candidates(db, season_id: int, ids: list[int]) -> dict[int, Candidate]:
    """I giocatori richiesti, con i dati CORRENTI della stagione (non quelli visti dall'utente caricando la pagina)."""
    ids = list(dict.fromkeys(ids))
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    rows = db.execute(
        f"""SELECT p.id AS player_id, psd.id AS psd_id, psd.full_name, psd.serie, psd.ruolo, psd.selectable, t.name AS team
              FROM player_season_data psd
              JOIN players p ON p.id = psd.player_id
              JOIN teams t ON t.id = psd.team_id
             WHERE psd.season_id = ? AND psd.valid_to IS NULL AND p.id IN ({marks})""",
        [season_id, *ids]).fetchall()
    return {r["player_id"]: Candidate(r["player_id"], r["psd_id"], r["full_name"], r["serie"], r["ruolo"],
                                      r["team"], bool(r["selectable"])) for r in rows}


def rules_for(contest: dict) -> Rules:
    return Rules(one_per_role=bool(contest["one_per_role"]))


def _reject(code: str, message: str, selected=None) -> SubmitResult:
    return SubmitResult(False, "rejected", errors=[Issue(code, message)], selected=selected or [], blocking=True)


def submit(db, *, contest_id: int, user_id: int, player_ids: list[int], team_name: str | None,
           had_bad_ids: bool = False, now: datetime | None = None) -> SubmitResult:
    name, name_error = clean_team_name(team_name)
    result: SubmitResult
    with transaction(db):
        now_ = now or utcnow()   # letto dopo aver preso il lock di scrittura
        row = db.execute("SELECT * FROM contests WHERE id = ?", (contest_id,)).fetchone()
        if row is None:
            return _reject("contest_missing", "Il concorso non esiste.")
        contest = contest_view(row, now_)
        if contest["effective"] == "scheduled":
            return _reject("not_open", f"Il concorso non è ancora aperto: apre {to_local_long(row['opens_at'])}.")
        if contest["effective"] == "draft":
            return _reject("not_open", "Il concorso non è aperto.")
        if contest["effective"] != "open":
            msg = "Il concorso è chiuso: la formazione non è stata registrata."
            if row["status"] == "open":     # chiuso per scadenza
                msg = (f"La scadenza è passata ({to_local_long(row['closes_at'])}): la formazione non è stata registrata.")
            current = _latest(db, contest_id, user_id)
            if current:
                msg += f" Resta valida la tua giocata precedente (versione {current['version']})."
            return _reject("closed", msg)
        user = db.execute("SELECT id, status FROM users WHERE id = ?", (user_id,)).fetchone()
        if user is None or user["status"] != "active":
            return _reject("account", "Il tuo account non è attivo.")
        excl = admission.is_excluded(db, row["season_id"], user_id)
        if excl:
            return _reject("not_admitted", "Non sei ammesso a partecipare a questa stagione. Contatta lo staff.")

        known = load_candidates(db, row["season_id"], player_ids)
        v = validate(player_ids, known, rules_for(contest))
        if had_bad_ids:
            v.errors.append(Issue("unknown_player", "Uno dei giocatori scelti non è valido. Ricarica la pagina e riprova."))
        if name_error:
            v.errors.append(Issue("team_name", name_error))
        selected = sorted((known[i] for i in dict.fromkeys(player_ids) if i in known), key=sort_key)
        if v.errors:
            result = SubmitResult(False, "rejected", errors=v.errors, warnings=v.warnings, selected=selected)
        else:
            lineup = db.execute("SELECT id FROM lineups WHERE contest_id = ? AND user_id = ?", (contest_id, user_id)).fetchone()
            if lineup is None:
                cur = db.execute("INSERT INTO lineups (contest_id, user_id, created_at) VALUES (?, ?, ?)",
                                 (contest_id, user_id, iso(now_)))
                lineup_id = cur.lastrowid
            else:
                lineup_id = lineup["id"]
            last = db.execute("SELECT id, version, fanta_team_name, submitted_at FROM lineup_versions "
                              "WHERE lineup_id = ? ORDER BY version DESC LIMIT 1", (lineup_id,)).fetchone()
            same = False
            if last is not None:
                last_ids = {r["player_id"] for r in db.execute(
                    "SELECT player_id FROM lineup_players WHERE version_id = ?", (last["id"],))}
                same = last_ids == {c.player_id for c in selected} and (last["fanta_team_name"] or None) == name
            if same:
                result = SubmitResult(True, "unchanged", last["version"], last["submitted_at"],
                                      warnings=v.warnings, selected=selected)
            else:
                version = (last["version"] if last else 0) + 1
                submitted_at = iso(now_)
                cur = db.execute("INSERT INTO lineup_versions (lineup_id, version, fanta_team_name, submitted_at) "
                                 "VALUES (?, ?, ?, ?)", (lineup_id, version, name, submitted_at))
                for pos, c in enumerate(selected, start=1):
                    db.execute(
                        "INSERT INTO lineup_players (version_id, position, psd_id, player_id, full_name, serie, ruolo, team_name) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (cur.lastrowid, pos, c.psd_id, c.player_id, c.name, c.serie, c.ruolo, c.team))
                audit.log(db, user_id, "lineup.submit", "lineup", lineup_id,
                          {"contest_id": contest_id, "version": version, "players": [c.player_id for c in selected],
                           "team_name": name})
                result = SubmitResult(True, "created" if version == 1 else "replaced", version, submitted_at,
                                      warnings=v.warnings, selected=selected)
    if not result.ok:
        _log_rejection(db, user_id, contest_id, result, now)
    return result


def _log_rejection(db, user_id: int, contest_id: int, result: SubmitResult, now) -> None:
    """Le giocate rifiutate non si salvano come giocate, ma il tentativo resta tracciato (utile allo staff)."""
    try:
        with transaction(db):
            audit.log(db, user_id, "lineup.rejected", "contest", contest_id,
                      {"errors": [e.code for e in result.errors]})
    except Exception:  # il log non deve mai trasformare un rifiuto in un errore 500
        pass


# ------------------------------------------------------------------- lettura


def _latest(db, contest_id: int, user_id: int):
    return db.execute(
        """SELECT lv.id, lv.version, lv.fanta_team_name, lv.submitted_at, l.id AS lineup_id
             FROM lineups l JOIN lineup_versions lv ON lv.lineup_id = l.id
            WHERE l.contest_id = ? AND l.user_id = ?
            ORDER BY lv.version DESC LIMIT 1""", (contest_id, user_id)).fetchone()


def _players_for(db, version_ids: list[int]) -> dict[int, list[dict]]:
    if not version_ids:
        return {}
    marks = ",".join("?" * len(version_ids))
    out: dict[int, list[dict]] = {i: [] for i in version_ids}
    for r in db.execute(f"SELECT * FROM lineup_players WHERE version_id IN ({marks}) ORDER BY version_id, position", version_ids):
        out[r["version_id"]].append(dict(r))
    return out


def current_lineup(db, contest_id: int, user_id: int) -> dict | None:
    latest = _latest(db, contest_id, user_id)
    if latest is None:
        return None
    d = dict(latest)
    d["players"] = _players_for(db, [latest["id"]])[latest["id"]]
    d["versions_count"] = latest["version"]
    return d


def versions(db, lineup_id: int) -> list[dict]:
    rows = db.execute("SELECT * FROM lineup_versions WHERE lineup_id = ? ORDER BY version DESC", (lineup_id,)).fetchall()
    players = _players_for(db, [r["id"] for r in rows])
    return [{**dict(r), "players": players[r["id"]]} for r in rows]


def contest_lineups(db, contest_id: int) -> list[dict]:
    """Ultima versione di ogni giocata del concorso (vista staff)."""
    rows = db.execute(
        """SELECT l.id AS lineup_id, l.user_id, u.display_name, u.username, lv.id AS version_id, lv.version,
                  lv.fanta_team_name, lv.submitted_at
             FROM lineups l
             JOIN users u ON u.id = l.user_id
             JOIN lineup_versions lv ON lv.lineup_id = l.id
              AND lv.version = (SELECT MAX(version) FROM lineup_versions WHERE lineup_id = l.id)
            WHERE l.contest_id = ? ORDER BY u.display_name COLLATE NOCASE""", (contest_id,)).fetchall()
    players = _players_for(db, [r["version_id"] for r in rows])
    return [{**dict(r), "players": players[r["version_id"]]} for r in rows]


def lineup_header(db, lineup_id: int):
    return db.execute(
        "SELECT l.id, l.contest_id, l.user_id, u.display_name, u.username FROM lineups l JOIN users u ON u.id = l.user_id "
        "WHERE l.id = ?", (lineup_id,)).fetchone()


def users_without_lineup(db, contest_id: int, season_id: int) -> list[dict]:
    rows = db.execute(
        """SELECT u.id, u.display_name, u.username FROM users u
            WHERE u.status = 'active'
              AND NOT EXISTS (SELECT 1 FROM lineups l WHERE l.contest_id = ? AND l.user_id = u.id)
              AND NOT EXISTS (SELECT 1 FROM season_exclusions e WHERE e.season_id = ? AND e.user_id = u.id)
            ORDER BY u.display_name COLLATE NOCASE""", (contest_id, season_id)).fetchall()
    return [dict(r) for r in rows]


def participation(db, user_id: int, contest_ids: list[int]) -> dict[int, dict]:
    """{contest_id: {version, submitted_at}} per le giocate gia' inviate dall'utente."""
    if not contest_ids:
        return {}
    marks = ",".join("?" * len(contest_ids))
    rows = db.execute(
        f"""SELECT l.contest_id, MAX(lv.version) AS version,
                   (SELECT submitted_at FROM lineup_versions x WHERE x.lineup_id = l.id ORDER BY x.version DESC LIMIT 1) AS submitted_at
              FROM lineups l JOIN lineup_versions lv ON lv.lineup_id = l.id
             WHERE l.user_id = ? AND l.contest_id IN ({marks}) GROUP BY l.id""", [user_id, *contest_ids]).fetchall()
    return {r["contest_id"]: dict(r) for r in rows}


def last_team_name(db, user_id: int, season_id: int) -> str:
    row = db.execute(
        """SELECT lv.fanta_team_name FROM lineup_versions lv
             JOIN lineups l ON l.id = lv.lineup_id JOIN contests c ON c.id = l.contest_id
            WHERE l.user_id = ? AND c.season_id = ? AND lv.fanta_team_name IS NOT NULL
            ORDER BY lv.submitted_at DESC, lv.id DESC LIMIT 1""", (user_id, season_id)).fetchone()
    return row["fanta_team_name"] if row else ""
