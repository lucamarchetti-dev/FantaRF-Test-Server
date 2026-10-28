from flask import Blueprint, current_app, flash, g, redirect, render_template, request, session, url_for

from ..db import get_db, transaction
from ..security import (clear_failures, hash_password, login_required, password_problem, record_failure,
                        safe_next, too_many_failures, verify_password)
from ..services import audit
from ..timeutil import utcnow_iso

bp = Blueprint("auth", __name__)
GENERIC_ERROR = "Nome utente o password non corretti, oppure account non attivo."


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user is not None:
        return redirect(url_for("main.home"))
    db = get_db()
    next_url = safe_next(request.values.get("next"))
    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        ip = request.remote_addr or "unknown"
        if too_many_failures(db, username, ip):
            return render_template("auth/login.html", error="Troppi tentativi falliti. Riprova tra qualche minuto.",
                                   next_url=next_url, username=username), 429
        user = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        ok = verify_password(user["password_hash"] if user else None, password)
        if ok and user["status"] == "active":
            clear_failures(db, username)
            session.clear()  # nuova sessione al login (previene session fixation)
            session["uid"] = user["id"]
            session.permanent = True
            return redirect(next_url or url_for("main.home"))
        record_failure(db, username, ip)
        error = GENERIC_ERROR
        return render_template("auth/login.html", error=error, next_url=next_url, username=username), 401
    return render_template("auth/login.html", error=None, next_url=next_url, username="")


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("main.home"))


@bp.route("/password", methods=["GET", "POST"])
@login_required
def change_password():
    db = get_db()
    error = None
    if request.method == "POST":
        current = request.form.get("current") or ""
        new = request.form.get("new") or ""
        confirm = request.form.get("confirm") or ""
        row = db.execute("SELECT password_hash FROM users WHERE id = ?", (g.user["id"],)).fetchone()
        if not verify_password(row["password_hash"], current):
            error = "La password attuale non è corretta."
        elif new != confirm:
            error = "Le due nuove password non coincidono."
        elif new == current:
            error = "La nuova password deve essere diversa da quella attuale."
        else:
            error = password_problem(new, current_app.config["MIN_PASSWORD_LENGTH"])
        if error is None:
            with transaction(db):
                db.execute(
                    "UPDATE users SET password_hash = ?, must_change_password = 0, updated_at = ? WHERE id = ?",
                    (hash_password(new), utcnow_iso(), g.user["id"]),
                )
                audit.log(db, g.user["id"], "user.change_password", "user", g.user["id"])
            flash("Password aggiornata.", "ok")
            return redirect(url_for("main.home"))
    return render_template("auth/password.html", error=error,
                           min_length=current_app.config["MIN_PASSWORD_LENGTH"])
