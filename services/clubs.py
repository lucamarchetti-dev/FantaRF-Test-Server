"""Squadra del GDR di ogni partecipante, per stagione (serve alla classifica a squadre).

- user_teams: associazione CORRENTE partecipante -> squadra, per stagione.
- contest_teams: squadra che il partecipante aveva in un concorso GIA' PUBBLICATO, scritta nel momento in cui
  l'associazione cambia o viene tolta. Cosi' un cambio di squadra vale per i concorsi non ancora pubblicati e non
  riscrive la classifica a squadre di quelli passati.
- Assegnare una squadra a chi non ne aveva NON e' un cambio: completa i dati mancanti, anche dei concorsi passati
  (e' quello che serve per gli account creati prima che esistesse la squadra).
"""
from __future__ import annotations

from ..db import transaction
from ..domain.ranking import normalize_team
from ..timeutil import utcnow_iso
from . import audit

MAX_TEAM_LEN = 40


class ClubError(Exception):
    pass


def team_map(db, season_id: int) -> dict[int, str]:
    """Squadra CORRENTE di ogni partecipante nella stagione."""
    return {r["user_id"]: r["team_name"] for r in db.execute("SELECT user_id, team_name FROM user_teams WHERE season_id = ?", (season_id,))}


def team_map_for_contest(db, season_id: int, contest_id: int) -> dict[int, str]:
    """Squadra di ogni partecipante IN QUEL concorso: l'istantanea (se la squadra e' poi cambiata) altrimenti quella corrente."""
    out = team_map(db, season_id)
    for r in db.execute("SELECT user_id, team_name FROM contest_teams WHERE contest_id = ?", (contest_id,)):
        out[r["user_id"]] = r["team_name"]
    return out


def clean_name(team_name: str | None) -> str:
    """Nome ripulito e validato (puo' essere vuoto: chi chiama decide se e' ammesso)."""
    name = normalize_team(team_name)
    if len(name) > MAX_TEAM_LEN:
        raise ClubError(f"Il nome della squadra può avere al massimo {MAX_TEAM_LEN} caratteri.")
    if any(ord(c) < 32 for c in name):
        raise ClubError("Il nome della squadra contiene caratteri non validi.")
    return name


def canonical_name(db, season_id: int, name: str) -> str:
    """Un nome gia' usato con altra maiuscola/minuscola vale la stessa squadra: si riusa la grafia esistente."""
    existing = db.execute("SELECT team_name FROM user_teams WHERE season_id = ? AND team_name = ? COLLATE NOCASE "
                          "ORDER BY rowid LIMIT 1", (season_id, name)).fetchone()
    return existing["team_name"] if existing else name


def write_team(db, season_id: int, user_id: int, team_name: str | None, actor_id, *, required: bool = False) -> None:
    """Scrive l'associazione. DA CHIAMARE DENTRO UNA TRANSAZIONE GIA' APERTA (la apre set_team; la usa anche create_user).

    required=True (partecipanti): la squadra non puo' essere vuota.
    """
    name = clean_name(team_name)
    if required and not name:
        raise ClubError("La squadra è obbligatoria per i partecipanti.")
    if db.execute("SELECT 1 FROM seasons WHERE id = ?", (season_id,)).fetchone() is None:
        raise ClubError("Stagione inesistente.")
    user = db.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
    if user is None:
        raise ClubError("Utente inesistente.")
    old_row = db.execute("SELECT team_name FROM user_teams WHERE season_id = ? AND user_id = ?", (season_id, user_id)).fetchone()
    old = old_row["team_name"] if old_row else None
    name = canonical_name(db, season_id, name) if name else ""
    if (old or "") == name:
        return
    now = utcnow_iso()
    if old:
        # Cambio o rimozione: prima di toccare, si fissa la squadra di OGGI per i concorsi gia' pubblicati.
        db.execute("INSERT OR IGNORE INTO contest_teams (contest_id, user_id, team_name, frozen_at) "
                   "SELECT id, ?, ?, ? FROM contests WHERE season_id = ? AND status = 'published'", (user_id, old, now, season_id))
    if not name:
        db.execute("DELETE FROM user_teams WHERE season_id = ? AND user_id = ?", (season_id, user_id))
    else:
        db.execute("INSERT INTO user_teams (season_id, user_id, team_name, updated_by, updated_at) VALUES (?, ?, ?, ?, ?) "
                   "ON CONFLICT (season_id, user_id) DO UPDATE SET team_name = excluded.team_name, updated_by = excluded.updated_by, "
                   "updated_at = excluded.updated_at", (season_id, user_id, name, actor_id, now))
    audit.log(db, actor_id, "club.set", "user", user_id, {"username": user["username"], "from": old, "to": name or None})


def set_team(db, season_id: int, user_id: int, team_name: str, actor_id) -> None:
    """Assegna, cambia o (solo per lo staff) toglie la squadra. Per i partecipanti la squadra e' obbligatoria."""
    clean_name(team_name)  # errori di formato prima di aprire la transazione
    with transaction(db):
        user = db.execute("SELECT role FROM users WHERE id = ?", (user_id,)).fetchone()
        if user is None:
            raise ClubError("Utente inesistente.")
        write_team(db, season_id, user_id, team_name, actor_id, required=user["role"] == "user")


def known_team_names(db) -> list[str]:
    """Nomi gia' usati (in qualsiasi stagione), senza doppioni dovuti a maiuscole: per i suggerimenti nei moduli."""
    seen: dict[str, str] = {}
    for r in db.execute("SELECT team_name FROM user_teams ORDER BY updated_at DESC, rowid DESC"):
        seen.setdefault(r["team_name"].casefold(), r["team_name"])
    return sorted(seen.values(), key=str.casefold)


def users_without_team(db, season_id: int) -> list[dict]:
    """Partecipanti attivi senza squadra nella stagione."""
    return [dict(r) for r in db.execute(
        """SELECT u.id, u.display_name, u.username FROM users u
            WHERE u.role = 'user' AND u.status = 'active'
              AND NOT EXISTS (SELECT 1 FROM user_teams t WHERE t.user_id = u.id AND t.season_id = ?)
            ORDER BY u.display_name COLLATE NOCASE""", (season_id,))]


def copy_from_previous_season(db, season_id: int, actor_id) -> dict:
    """Per i partecipanti senza squadra in questa stagione, copia l'ultima squadra che avevano in una stagione precedente.

    Non tocca chi ha gia' una squadra. Restituisce {'copied': n, 'missing': n} (missing = ancora senza squadra).
    """
    with transaction(db):
        if db.execute("SELECT 1 FROM seasons WHERE id = ?", (season_id,)).fetchone() is None:
            raise ClubError("Stagione inesistente.")
        copied = 0
        for u in users_without_team(db, season_id):
            prev = db.execute("SELECT team_name FROM user_teams WHERE user_id = ? AND season_id != ? "
                              "ORDER BY season_id DESC LIMIT 1", (u["id"], season_id)).fetchone()
            if prev:
                write_team(db, season_id, u["id"], prev["team_name"], actor_id, required=True)
                copied += 1
        missing = len(users_without_team(db, season_id))
        audit.log(db, actor_id, "club.copy_previous", "season", season_id, {"copied": copied, "still_missing": missing})
    return {"copied": copied, "missing": missing}
