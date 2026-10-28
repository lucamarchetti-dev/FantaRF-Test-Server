"""Gestione staff degli strike, con storico e annullamento tracciato."""
from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from ..db import get_db
from ..security import staff_guard
from ..services import seasons as seasons_svc
from ..services import rules as rules_svc
from ..services import strikes as strikes_svc

bp = Blueprint("staff_strikes", __name__, url_prefix="/staff/strike")
bp.before_request(staff_guard)


@bp.route("/", methods=["GET", "POST"])
def index():
    db = get_db()
    season = seasons_svc.active_season(db)
    error = None
    if request.method == "POST":
        if season is None:
            error = "Non c'è una stagione attiva."
        else:
            action = request.form.get("action", "add")
            try:
                if action == "void":
                    strikes_svc.void_strike(int(request.form.get("strike_id", "0")), request.form.get("reason", ""), g.user["id"])
                    flash("Strike annullato; l'operazione resta nello storico.", "ok")
                else:
                    user_id = int(request.form.get("user_id", "0"))
                    contest_id = int(request.form.get("contest_id") or 0) or None
                    result = strikes_svc.add_strike(db, season["id"], user_id, contest_id,
                                                    request.form.get("reason", ""), g.user["id"],
                                                    rules_svc.get_rules(db, season["id"]).max_strikes)
                    flash(f"Strike assegnato a {result['name']}. Conteggio attuale: {result['count']}.", "ok")
                return redirect(url_for("staff_strikes.index"))
            except (ValueError, strikes_svc.StrikeError) as exc:
                error = str(exc) or "Selezione non valida."
    users = db.execute("SELECT id, username, display_name FROM users WHERE status = 'active' ORDER BY display_name COLLATE NOCASE").fetchall()
    contests = db.execute("SELECT id, number, name FROM contests WHERE season_id = ? AND status != 'draft' ORDER BY number", (season["id"],)).fetchall() if season else []
    records = strikes_svc.list_strikes(db, season["id"]) if season else []
    counts = strikes_svc.counts(db, season["id"]) if season else {}
    rules = rules_svc.get_rules(db, season["id"]) if season else None
    return render_template("staff/strikes.html", season=season, users=users, contests=contests,
                           records=records, counts=counts, rules=rules, error=error)
