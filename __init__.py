"""FantaRF - webapp per il fantacalcio del GDR RoleFootball / Progetto Football Manager."""
from __future__ import annotations

import os

from flask import Flask, g, redirect, render_template, request, session, url_for
from werkzeug.exceptions import HTTPException

from . import crests
from . import db as dbm
from .config import load_config
from .domain.listone import ROLE_LABELS
from .security import check_csrf, csrf_token
from .domain.contest import STATUS_LABELS, time_left_text
from .domain.scoring import fmt as fmt_number
from .timeutil import to_local, to_local_long, utcnow

__version__ = "0.1.0"


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__, instance_path=os.environ.get("FANTAPG_INSTANCE_PATH") or None)
    app.config.update(load_config(app.instance_path, test_config))
    app.config.update(
        MAX_CONTENT_LENGTH=app.config["MAX_UPLOAD_MB"] * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=app.config["SECURE_COOKIES"],
        PERMANENT_SESSION_LIFETIME=14 * 24 * 3600,
    )
    if app.config["TRUST_PROXY"]:
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    os.makedirs(app.instance_path, exist_ok=True)
    os.makedirs(app.config["UPLOAD_DIR"], exist_ok=True)
    conn = dbm.connect(app.config["DATABASE"])
    try:
        dbm.migrate(conn)  # idempotente e atomico per ogni migrazione
    finally:
        conn.close()
    app.teardown_appcontext(dbm.close_db)

    @app.before_request
    def load_user():
        g.user = None
        if request.endpoint == "static":
            return None
        uid = session.get("uid")
        if uid is not None:
            row = dbm.get_db().execute(
                "SELECT id, username, display_name, role, must_change_password FROM users "
                "WHERE id = ? AND status = 'active'",
                (uid,),
            ).fetchone()
            if row is None:
                session.clear()  # account disattivato o eliminato: l'accesso decade subito
            else:
                g.user = row
        return None

    @app.before_request
    def protect():
        if request.endpoint == "static":
            return None
        check_csrf()
        if (g.user is not None and g.user["must_change_password"]
                and request.endpoint not in ("auth.change_password", "auth.logout")):
            return redirect(url_for("auth.change_password"))
        return None

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
            "base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
        )
        if app.config["SECURE_COOKIES"]:
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        if g.get("user") is not None:
            resp.headers["Cache-Control"] = "no-store"
        return resp

    app.jinja_env.filters["localtime"] = lambda v: to_local(v, app.config["TIMEZONE"])
    app.jinja_env.filters["num"] = lambda v: fmt_number(v, app.config["SCORE_DISPLAY_DECIMALS"])
    app.jinja_env.filters["datelong"] = lambda v: to_local_long(v, app.config["TIMEZONE"])
    app.jinja_env.filters["timeleft"] = lambda dt: time_left_text(dt - utcnow())
    app.jinja_env.globals.update(csrf_token=csrf_token, ROLE_LABELS=ROLE_LABELS, CONTEST_STATUS=STATUS_LABELS, app_version=__version__)
    crests.init_app(app)   # stemmi delle squadre (static/crests/)

    from .views import auth, contests, main, players, staff, staff_contests, staff_results,staff_strikes, standings, strikes
    app.register_blueprint(main.bp)
    app.register_blueprint(auth.bp)
    app.register_blueprint(players.bp)
    app.register_blueprint(staff.bp)
    app.register_blueprint(contests.bp)
    app.register_blueprint(staff_contests.bp)
    app.register_blueprint(staff_results.bp)
    app.register_blueprint(staff_results.weights_bp)
    app.register_blueprint(standings.bp)
    app.register_blueprint(staff_strikes.bp)
    app.register_blueprint(strikes.bp)

    def render_error(code: int, title: str, message: str):
        return render_template("error.html", code=code, title=title, message=message), code

    @app.errorhandler(400)
    def bad_request(err):
        if getattr(err, "description", "") == "csrf":
            return render_error(400, "Sessione scaduta", "Il modulo non è più valido. Ricarica la pagina e riprova.")
        return render_error(400, "Richiesta non valida", "La richiesta non è corretta.")

    @app.errorhandler(403)
    def forbidden(_err):
        return render_error(403, "Accesso negato", "Non hai i permessi per questa pagina.")

    @app.errorhandler(404)
    def not_found(_err):
        return render_error(404, "Pagina non trovata", "La pagina cercata non esiste.")

    @app.errorhandler(413)
    def too_large(_err):
        return render_error(413, "File troppo grande",
                            f"Il file supera il limite di {app.config['MAX_UPLOAD_MB']} MB.")

    @app.errorhandler(Exception)
    def internal(err):
        if isinstance(err, HTTPException):
            return render_error(err.code or 500, err.name, "Si è verificato un errore.")
        app.logger.exception("Errore non gestito")
        return render_error(500, "Errore interno", "Qualcosa non ha funzionato. Nessun dato è stato perso.")

    from . import cli
    cli.register(app)
    return app