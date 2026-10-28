"""Area staff. Ogni rotta è protetta lato server dal controllo del ruolo."""
from __future__ import annotations

import json
import os

from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for)

from ..db import get_db
from ..domain.excel_reader import ImportFileError
from ..domain.listone import FIELDS, FIELD_LABELS
from ..security import staff_guard
from ..services import admission
from ..services import imports as imports_svc
from ..services import players as players_svc
from ..services import seasons as seasons_svc
from ..services import users as users_svc

bp = Blueprint("staff", __name__, url_prefix="/staff")


bp.before_request(staff_guard)


def _int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@bp.route("/")
def index():
    db = get_db()
    counts = {
        "users": db.execute("SELECT COUNT(*) AS n FROM users WHERE status = 'active'").fetchone()["n"],
        "seasons": db.execute("SELECT COUNT(*) AS n FROM seasons").fetchone()["n"],
    }
    season = seasons_svc.active_season(db)
    stats = players_svc.season_stats(db, season["id"]) if season else None
    last = imports_svc.recent_batches(db, 1)
    return render_template("staff/index.html", counts=counts, season=season, stats=stats,
                           last_import=last[0] if last else None)


# --------------------------------------------------------------------- stagioni


@bp.route("/stagioni", methods=["GET", "POST"])
def seasons():
    db = get_db()
    error = None
    if request.method == "POST":
        try:
            seasons_svc.create_season(db, request.form.get("name"), request.form.get("starts_on"),
                                      request.form.get("ends_on"), g.user["id"])
            flash("Stagione creata.", "ok")
            return redirect(url_for("staff.seasons"))
        except seasons_svc.SeasonError as exc:
            error = str(exc)
    return render_template("staff/seasons.html", seasons=seasons_svc.list_seasons(db), error=error,
                           form=request.form)


@bp.route("/stagioni/<int:season_id>/attiva", methods=["POST"])
def activate_season(season_id):
    try:
        seasons_svc.activate_season(get_db(), season_id, g.user["id"])
        flash("Stagione attivata. La stagione attiva precedente è stata archiviata.", "ok")
    except seasons_svc.SeasonError as exc:
        flash(str(exc), "error")
    return redirect(url_for("staff.seasons"))


# ----------------------------------------------------------------------- utenti


@bp.route("/utenti", methods=["GET", "POST"])
def users():
    db = get_db()
    error = None
    if request.method == "POST":
        try:
            _id, password = users_svc.create_user(
                db, username=request.form.get("username"), display_name=request.form.get("display_name"),
                role=request.form.get("role", "user"), password=None, actor_id=g.user["id"],
            )
            return render_template(
                "staff/credentials.html", username=request.form.get("username", "").strip(),
                display_name=request.form.get("display_name", ""), password=password, reset=False,
            )
        except users_svc.UserError as exc:
            error = str(exc)
    season = seasons_svc.active_season(db)
    return render_template("staff/users.html", users=users_svc.list_users(db), error=error, form=request.form,
                           season=season, exclusions=admission.exclusions_map(db, season["id"]) if season else {})


@bp.route("/utenti/<int:user_id>/stato", methods=["POST"])
def user_status(user_id):
    status = "disabled" if request.form.get("status") == "disabled" else "active"
    try:
        users_svc.set_status(get_db(), user_id, status, g.user["id"])
        flash("Account disattivato." if status == "disabled" else "Account riattivato.", "ok")
    except users_svc.UserError as exc:
        flash(str(exc), "error")
    return redirect(url_for("staff.users"))


@bp.route("/utenti/<int:user_id>/reset", methods=["POST"])
def user_reset(user_id):
    try:
        user, password = users_svc.reset_password(get_db(), user_id, g.user["id"])
    except users_svc.UserError as exc:
        flash(str(exc), "error")
        return redirect(url_for("staff.users"))
    return render_template("staff/credentials.html", username=user["username"],
                           display_name=user["display_name"], password=password, reset=True)


@bp.route("/utenti/<int:user_id>/esclusione", methods=["GET", "POST"])
def user_exclusion(user_id):
    db = get_db()
    season = seasons_svc.active_season(db)
    user = db.execute("SELECT id, username, display_name FROM users WHERE id = ?", (user_id,)).fetchone()
    if user is None:
        abort(404)
    if season is None:
        flash("Non c'è una stagione attiva.", "error")
        return redirect(url_for("staff.users"))
    error = None
    if request.method == "POST":
        try:
            admission.exclude(db, season["id"], user_id, request.form.get("reason"), g.user["id"])
            flash(f"{user['display_name']} è escluso dalla stagione {season['name']}.", "ok")
            return redirect(url_for("staff.users"))
        except admission.AdmissionError as exc:
            error = str(exc)
    return render_template("staff/exclusion.html", user=user, season=season, error=error, form=request.form)


@bp.route("/utenti/<int:user_id>/riammetti", methods=["POST"])
def user_readmit(user_id):
    season = seasons_svc.active_season(get_db())
    try:
        if season is None:
            raise admission.AdmissionError("Non c'è una stagione attiva.")
        admission.readmit(get_db(), season["id"], user_id, g.user["id"])
        flash("Utente riammesso alla stagione.", "ok")
    except admission.AdmissionError as exc:
        flash(str(exc), "error")
    return redirect(url_for("staff.users"))


# --------------------------------------------------------------------- listone


@bp.route("/giocatori/<int:psd_id>/stato", methods=["POST"])
def player_toggle(psd_id):
    selectable = request.form.get("selectable") == "1"
    row = players_svc.set_selectable(get_db(), psd_id, selectable, g.user["id"])
    if row is None:
        abort(404)
    state = "di nuovo selezionabile" if selectable else "non più selezionabile"
    flash(f"{row['full_name']}: {state}.", "ok")
    back = request.form.get("back") or ""
    return redirect(back if back.startswith("/giocatori") else url_for("players.listing"))


# ------------------------------------------------------------------ importazione


@bp.route("/import", methods=["GET", "POST"])
def import_start():
    db = get_db()
    seasons = seasons_svc.list_seasons(db)
    active = seasons_svc.active_season(db)
    error = None
    if request.method == "POST":
        season_id = _int(request.form.get("season_id"))
        sheet = (request.form.get("sheet") or "").strip() or "Consultazione"
        upload = request.files.get("file")
        if seasons_svc.get_season(db, season_id or 0) is None:
            error = "Scegli la stagione a cui appartiene il listone."
        elif upload is None or not upload.filename:
            error = "Seleziona il file Excel da caricare."
        elif not upload.filename.lower().endswith(".xlsx"):
            error = "Sono accettati solo file Excel in formato .xlsx."
        else:
            try:
                data = upload.read()
                upload.close()  # libera subito il file temporaneo creato per gli upload grandi
                path, sha = imports_svc.store_upload(current_app.config["UPLOAD_DIR"], data)
                try:
                    batch_id = imports_svc.create_batch(db, season_id=season_id, user_id=g.user["id"],
                                                        filename=os.path.basename(upload.filename), sheet=sheet,
                                                        path=path, sha256=sha)
                except Exception:
                    if os.path.exists(path):
                        os.remove(path)
                    raise
                return redirect(url_for("staff.import_detail", batch_id=batch_id))
            except ImportFileError as exc:
                error = str(exc)
    return render_template("staff/import_start.html", seasons=seasons, active=active, error=error,
                           batches=imports_svc.recent_batches(db), form=request.form)


@bp.route("/import/<int:batch_id>")
def import_detail(batch_id):
    db = get_db()
    batch = imports_svc.get_batch(db, batch_id)
    if batch is None:
        abort(404)
    if batch["status"] == "applied":
        summary = json.loads(batch["summary_json"])
        return render_template("staff/import_report.html", batch=batch, summary=summary)
    if batch["status"] != "preview":
        return render_template("staff/import_report.html", batch=batch, summary=None)
    try:
        preview = imports_svc.build_preview(db, batch, current_app.config["MAX_IMPORT_ROWS"])
        error = None
    except ImportFileError as exc:
        preview, error = None, str(exc)
    return render_template("staff/import_preview.html", batch=batch, preview=preview, error=error,
                           FIELDS=FIELDS, FIELD_LABELS=FIELD_LABELS)


@bp.route("/import/<int:batch_id>/colonne", methods=["POST"])
def import_mapping(batch_id):
    columns = {}
    for fld in FIELDS:
        idx = _int(request.form.get(f"col_{fld}"))
        if idx is None or idx < 0:
            flash(f"Scegli la colonna per «{FIELD_LABELS[fld]}».", "error")
            return redirect(url_for("staff.import_detail", batch_id=batch_id))
        columns[fld] = idx
    if len(set(columns.values())) != len(columns):
        flash("Ogni campo deve usare una colonna diversa.", "error")
        return redirect(url_for("staff.import_detail", batch_id=batch_id))
    header_row = _int(request.form.get("header_row"), 1)
    if header_row < 1:
        header_row = 1
    try:
        imports_svc.set_mapping(get_db(), batch_id, g.user["id"], header_row, columns)
    except imports_svc.ImportStateError as exc:
        flash(str(exc), "error")
    return redirect(url_for("staff.import_detail", batch_id=batch_id))


@bp.route("/import/<int:batch_id>/conferma", methods=["POST"])
def import_confirm(batch_id):
    try:
        summary = imports_svc.apply_batch(get_db(), batch_id, g.user["id"], current_app.config["MAX_IMPORT_ROWS"])
        flash(f"Importazione completata: {summary['new']} nuovi, {summary['changed']} aggiornati, "
              f"{summary['unchanged']} invariati, {summary['rows_rejected']} righe scartate.", "ok")
    except imports_svc.ImportStateError as exc:
        flash(str(exc), "error")
    except ImportFileError as exc:
        flash(str(exc), "error")
    except Exception:
        current_app.logger.exception("Importazione fallita")
        flash("Importazione non riuscita: nessun dato è stato modificato. Il dettaglio è nel registro operazioni.",
              "error")
    return redirect(url_for("staff.import_detail", batch_id=batch_id))


@bp.route("/import/<int:batch_id>/annulla", methods=["POST"])
def import_cancel(batch_id):
    try:
        imports_svc.cancel_batch(get_db(), batch_id, g.user["id"])
        flash("Importazione annullata. Il listone non è stato modificato.", "ok")
    except imports_svc.ImportStateError as exc:
        flash(str(exc), "error")
    return redirect(url_for("staff.import_start"))


# ------------------------------------------------------------------------ audit


@bp.route("/registro")
def audit_log():
    db = get_db()
    per_page = current_app.config["AUDIT_PER_PAGE"]
    page = max(1, _int(request.args.get("page"), 1))
    total = db.execute("SELECT COUNT(*) AS n FROM audit_log").fetchone()["n"]
    pages = max(1, -(-total // per_page))
    page = min(page, pages)
    rows = db.execute(
        "SELECT a.*, u.display_name AS user_name FROM audit_log a LEFT JOIN users u ON u.id = a.user_id "
        "ORDER BY a.id DESC LIMIT ? OFFSET ?", (per_page, (page - 1) * per_page)).fetchall()
    return render_template("staff/audit.html", rows=rows, page=page, pages=pages, total=total)
