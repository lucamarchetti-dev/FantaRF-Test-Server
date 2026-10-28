"""Sicurezza: password, CSRF, decoratori di autorizzazione, limitazione dei tentativi di login."""
from __future__ import annotations

import hmac
import secrets
from functools import wraps
from urllib.parse import urlparse

from flask import abort, current_app, g, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .timeutil import minutes_ago_iso, utcnow_iso

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
# Hash fittizio: serve a spendere lo stesso tempo anche per utenti inesistenti.
_DUMMY_HASH = generate_password_hash("fantapg-dummy-password")


def hash_password(password: str) -> str:
    return generate_password_hash(password)


def verify_password(stored_hash: str | None, password: str) -> bool:
    return check_password_hash(stored_hash or _DUMMY_HASH, password) and stored_hash is not None


def password_problem(password: str, min_length: int) -> str | None:
    if len(password) < min_length:
        return f"La password deve avere almeno {min_length} caratteri."
    if password.strip() != password:
        return "La password non può iniziare o finire con spazi."
    if len(password) > 200:
        return "La password è troppo lunga."
    return None


def generate_password() -> str:
    return secrets.token_urlsafe(9)


# --------------------------------------------------------------------- CSRF


def csrf_token() -> str:
    token = session.get("_csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf"] = token
    return token


def check_csrf() -> None:
    if request.method not in UNSAFE_METHODS:
        return
    sent = request.form.get("_csrf") or request.headers.get("X-CSRF-Token") or ""
    expected = session.get("_csrf") or ""
    if not expected or not hmac.compare_digest(sent, expected):
        abort(400, description="csrf")


# ------------------------------------------------------ autorizzazioni (lato server)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
        return view(*args, **kwargs)
    return wrapped


def staff_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
        if g.user["role"] != "staff":
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def staff_guard():
    """before_request condiviso da tutte le sezioni /staff: nessuna rotta puo' dimenticare il controllo."""
    if g.user is None:
        return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
    if g.user["role"] != "staff":
        abort(403)
    return None


def safe_next(target: str | None) -> str | None:
    """Accetta solo percorsi relativi interni (evita open redirect)."""
    if not target:
        return None
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc or not target.startswith("/") or target.startswith("//"):
        return None
    if "\\" in target:
        return None
    return target


# ------------------------------------------------- limitazione tentativi di login


def too_many_failures(db, username: str, ip: str) -> bool:
    cfg = current_app.config
    since = minutes_ago_iso(cfg["LOGIN_WINDOW_MINUTES"])
    per_user = db.execute(
        "SELECT COUNT(*) AS n FROM login_failures WHERE username = ? COLLATE NOCASE AND created_at >= ?",
        (username, since),
    ).fetchone()["n"]
    per_ip = db.execute(
        "SELECT COUNT(*) AS n FROM login_failures WHERE ip = ? AND created_at >= ?", (ip, since)
    ).fetchone()["n"]
    return per_user >= cfg["LOGIN_MAX_FAILURES"] or per_ip >= cfg["LOGIN_MAX_FAILURES"] * 4


def record_failure(db, username: str, ip: str) -> None:
    db.execute(
        "INSERT INTO login_failures (username, ip, created_at) VALUES (?, ?, ?)",
        (username[:80], ip, utcnow_iso()),
    )
    db.execute("DELETE FROM login_failures WHERE created_at < ?", (minutes_ago_iso(24 * 60),))


def clear_failures(db, username: str) -> None:
    db.execute("DELETE FROM login_failures WHERE username = ? COLLATE NOCASE", (username,))
