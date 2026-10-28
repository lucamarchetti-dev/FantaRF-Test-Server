from __future__ import annotations

import re

from ..db import transaction
from ..security import generate_password, hash_password
from ..timeutil import utcnow_iso
from . import audit

USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,40}$")


class UserError(Exception):
    """Errore di validazione con messaggio mostrabile."""


def create_user(db, *, username: str, display_name: str, role: str, password: str | None,
                actor_id, must_change: bool = True) -> tuple[int, str]:
    username = (username or "").strip()
    display_name = " ".join((display_name or "").split())
    if not USERNAME_RE.match(username):
        raise UserError("Nome utente non valido: 3-40 caratteri tra lettere, numeri, punto, trattino e underscore.")
    if not display_name or len(display_name) > 60:
        raise UserError("Il nome visualizzato è obbligatorio (massimo 60 caratteri).")
    if role not in ("user", "staff"):
        raise UserError("Ruolo non valido.")
    password = password or generate_password()
    now = utcnow_iso()
    with transaction(db):
        if db.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            raise UserError("Esiste già un utente con questo nome utente.")
        cur = db.execute(
            "INSERT INTO users (username, display_name, password_hash, role, status, must_change_password, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, 'active', ?, ?, ?)",
            (username, display_name, hash_password(password), role, 1 if must_change else 0, now, now),
        )
        audit.log(db, actor_id, "user.create", "user", cur.lastrowid, {"username": username, "role": role})
    return cur.lastrowid, password


def list_users(db):
    return db.execute(
        "SELECT id, username, display_name, role, status, must_change_password, created_at FROM users "
        "ORDER BY role DESC, display_name COLLATE NOCASE"
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
