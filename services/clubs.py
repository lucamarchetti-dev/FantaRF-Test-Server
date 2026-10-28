"""Squadra del GDR di ogni partecipante, per stagione (serve alla classifica a squadre)."""
from __future__ import annotations

from ..db import transaction
from ..timeutil import utcnow_iso
from . import audit


class ClubError(Exception):
    pass


def team_map(db, season_id: int) -> dict[int, str]:
    return {r["user_id"]: r["team_name"] for r in db.execute("SELECT user_id, team_name FROM user_teams WHERE season_id = ?", (season_id,))}


def set_team(db, season_id: int, user_id: int, team_name: str, actor_id) -> None:
    name = " ".join((team_name or "").split())
    if len(name) > 40:
        raise ClubError("Il nome della squadra può avere al massimo 40 caratteri.")
    with transaction(db):
        user = db.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
        if user is None:
            raise ClubError("Utente inesistente.")
        old = db.execute("SELECT team_name FROM user_teams WHERE season_id = ? AND user_id = ?", (season_id, user_id)).fetchone()
        if not name:
            db.execute("DELETE FROM user_teams WHERE season_id = ? AND user_id = ?", (season_id, user_id))
        else:
            # un nome gia' usato con altra maiuscola/minuscola vale la stessa squadra: si riusa la grafia esistente
            existing = db.execute("SELECT team_name FROM user_teams WHERE season_id = ? AND team_name = ? COLLATE NOCASE LIMIT 1", (season_id, name)).fetchone()
            name = existing["team_name"] if existing else name
            db.execute("INSERT INTO user_teams (season_id, user_id, team_name, updated_by, updated_at) VALUES (?, ?, ?, ?, ?) "
                       "ON CONFLICT (season_id, user_id) DO UPDATE SET team_name = excluded.team_name, updated_by = excluded.updated_by, "
                       "updated_at = excluded.updated_at", (season_id, user_id, name, actor_id, utcnow_iso()))
        if (old["team_name"] if old else "") != name:
            audit.log(db, actor_id, "club.set", "user", user_id, {"username": user["username"], "from": old["team_name"] if old else None, "to": name or None})


def users_with_teams(db, season_id: int):
    return db.execute(
        """SELECT u.id, u.display_name, u.username, t.team_name FROM users u
             LEFT JOIN user_teams t ON t.user_id = u.id AND t.season_id = ?
            WHERE u.status = 'active' ORDER BY t.team_name IS NULL, t.team_name COLLATE NOCASE, u.display_name COLLATE NOCASE""", (season_id,)).fetchall()
