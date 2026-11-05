"""Classifiche pubbliche calcolate dai risultati pubblicati."""
from flask import Blueprint, abort, render_template, request

from ..db import get_db
from ..services import seasons as seasons_svc
from ..services import rules as rules_svc
from ..services import standings as standings_svc

bp = Blueprint("standings", __name__, url_prefix="/classifica")


@bp.route("/")
def index():
    db = get_db()
    seasons = seasons_svc.list_seasons(db)
    active = seasons_svc.active_season(db)
    selected_id = request.args.get("stagione", type=int)
    season = seasons_svc.get_season(db, selected_id) if selected_id else active
    if selected_id and season is None:
        abort(404)
    if season is None and seasons:
        season = seasons[0]
    data = None
    teams_data = None
    if season:
        rules = rules_svc.get_rules(db, season["id"])
        data = standings_svc.general(db, season["id"], rules)
        teams_data = standings_svc.teams(db, season["id"], rules)
    return render_template("standings/index.html", seasons=seasons, season=season,
                           data=data, teams_data=teams_data)


@bp.route("/squadre")
def teams():
    """Classifica a squadre: generale cumulativa, punti per concorso e partecipante che li ha portati."""
    db = get_db()
    seasons = seasons_svc.list_seasons(db)
    selected_id = request.args.get("stagione", type=int)
    season = seasons_svc.get_season(db, selected_id) if selected_id else seasons_svc.active_season(db)
    if selected_id and season is None:
        abort(404)
    if season is None and seasons:
        season = seasons[0]
    teams_data = None
    if season:
        teams_data = standings_svc.teams(db, season["id"], rules_svc.get_rules(db, season["id"]))
    return render_template("standings/teams.html", seasons=seasons, season=season, teams_data=teams_data)
