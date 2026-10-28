"""Consultazione personale degli strike."""
from flask import Blueprint, g, render_template

from ..db import get_db
from ..services import rules as rules_svc
from ..services import seasons as seasons_svc
from ..services import strikes as strikes_svc
from ..security import login_required

bp = Blueprint("strikes", __name__, url_prefix="/strike")


@bp.route("/")
@login_required
def index():
    db = get_db()
    season = seasons_svc.active_season(db)
    if season is None:
        return render_template("strikes/index.html", season=None, count=0, max_strikes=3, records=[])
    all_records = strikes_svc.list_strikes(db, season["id"])
    records = [r for r in all_records if r["user_id"] == g.user["id"]]
    count = strikes_svc.counts(db, season["id"]).get(g.user["id"], 0)
    rules = rules_svc.get_rules(db, season["id"])
    return render_template("strikes/index.html", season=season, count=count,
                           max_strikes=rules.max_strikes, records=records)
