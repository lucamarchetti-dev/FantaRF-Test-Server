from __future__ import annotations

import re

from ..db import transaction
from ..security import generate_password, hash_password
from ..timeutil import utcnow_iso
from . import audit, clubs

USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,40}$")


class UserError(Exception):
    """Errore di validazione con messaggio mostrabile."""


def create_user(db, *, username: str, display_name: str, role: str, password: str | None,
                actor_id, must_change: bool = True, team_name: str | None = None,
                season_id: int | None = None) -> tuple[int, str]:
    """Crea l'account. Per i partecipanti (role 'user') la squadra e' OBBLIGATORIA e viene salvata, nella stessa
    transazione, per la stagione indicata (default: quella attiva). Lo staff non ha obbligo di squadra."""
    username = (username or "").strip()
    display_name = " ".join((display_name or "").split())
    if not USERNAME_RE.match(username):
        raise UserError("Nome utente non valido: 3-40 caratteri tra lettere, numeri, punto, trattino e underscore.")
    if not display_name or len(display_name) > 60:
        raise UserError("Il nome visualizzato è obbligatorio (massimo 60 caratteri).")
    if role not in ("user", "staff"):
        raise UserError("Ruolo non valido.")
    try:
        team_name = clubs.clean_name(team_name)
    except clubs.ClubError as exc:
        raise UserError(str(exc)) from exc
    if role == "user" and not team_name:
        raise UserError("La squadra è obbligatoria per i partecipanti.")
    password = password or generate_password()
    now = utcnow_iso()
    with transaction(db):
        if db.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            raise UserError("Esiste già un utente con questo nome utente.")
        if team_name:
            if season_id is None:
                active = db.execute("SELECT id FROM seasons WHERE status = 'active'").fetchone()
                season_id = active["id"] if active else None
            if season_id is None:
                raise UserError("La squadra è legata alla stagione: crea e attiva prima una stagione, poi i partecipanti.")
        cur = db.execute(
            "INSERT INTO users (username, display_name, password_hash, role, status, must_change_password, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, 'active', ?, ?, ?)",
            (username, display_name, hash_password(password), role, 1 if must_change else 0, now, now),
        )
        audit.log(db, actor_id, "user.create", "user", cur.lastrowid, {"username": username, "role": role})
        if team_name:
            try:
                clubs.write_team(db, season_id, cur.lastrowid, team_name, actor_id, required=role == "user")
            except clubs.ClubError as exc:
                raise UserError(str(exc)) from exc   # l'eccezione annulla l'intera transazione: nessun account a meta'
    return cur.lastrowid, password


def list_users(db, season_id: int | None = None):
    """Tutti gli account; con season_id, anche la squadra (team_name, NULL se manca) nella stagione indicata."""
    return db.execute(
        "SELECT u.id, u.username, u.display_name, u.role, u.status, u.must_change_password, u.created_at, "
        "       t.team_name AS team_name "
        "  FROM users u LEFT JOIN user_teams t ON t.user_id = u.id AND t.season_id = ? "
        " ORDER BY u.role DESC, u.display_name COLLATE NOCASE", (season_id,)
    ).fetchall()


def set_status(db, user_id: int, status: str, actor_id: int) -> None:
    if status not in ("active", "disabled"):
        raise UserError("Stato non valido.")
    with transaction(db):
        user = db.execute("SELECT id, username, role, status FROM users WHERE id = ?", (user_id,)).fetchone()
        if user is None:
            raise UserError("Utente inesistente.")
        if status == "disabled":
            if user_id == actor_id:
                raise UserError("Non puoi disattivare il tuo stesso account.")
            if user["role"] == "staff":
                others = db.execute(
                    "SELECT COUNT(*) AS n FROM users WHERE role = 'staff' AND status = 'active' AND id != ?",
                    (user_id,),
                ).fetchone()["n"]
                if others == 0:
                    raise UserError("Deve restare almeno un account staff attivo.")
        db.execute("UPDATE users SET status = ?, updated_at = ? WHERE id = ?", (status, utcnow_iso(), user_id))
        audit.log(db, actor_id, "user.status", "user", user_id, {"username": user["username"], "status": status})


def reset_password(db, user_id: int, actor_id: int) -> tuple[dict, str]:
    password = generate_password()
    with transaction(db):
        user = db.execute("SELECT id, username, display_name FROM users WHERE id = ?", (user_id,)).fetchone()
        if user is None:
            raise UserError("Utente inesistente.")
        db.execute(
            "UPDATE users SET password_hash = ?, must_change_password = 1, updated_at = ? WHERE id = ?",
            (hash_password(password), utcnow_iso(), user_id),
        )
        audit.log(db, actor_id, "user.reset_password", "user", user_id, {"username": user["username"]})
    return dict(user), password
