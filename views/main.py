from flask import Blueprint, g, render_template

from ..db import get_db
from ..services import contests as contests_svc
from ..services import lineups as lineups_svc
from ..services import players as players_svc
from ..services import seasons as seasons_svc

bp = Blueprint("main", __name__)


@bp.route("/")
def home():
    db = get_db()
    season = seasons_svc.active_season(db)
    stats = players_svc.season_stats(db, season["id"]) if season else None
    open_contests, mine = [], {}
    if season:
        open_contests = [c for c in contests_svc.list_contests(db, season["id"], include_drafts=False)
                         if c["effective"] in ("open", "scheduled")]
        if g.user:
            mine = lineups_svc.participation(db, g.user["id"], [c["id"] for c in open_contests])
    return render_template("home.html", season=season, stats=stats, open_contests=open_contests, mine=mine)


@bp.route("/healthz")
def healthz():
    get_db().execute("SELECT 1")
    return "ok", 200, {"Content-Type": "text/plain; charset=utf-8"}
