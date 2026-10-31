from flask import Blueprint, abort, current_app, g, render_template, request

from ..db import get_db
from ..domain.listone import ROLES, SERIES
from ..services import player_votes as votes_svc
from ..services import players as players_svc
from ..services import seasons as seasons_svc

bp = Blueprint("players", __name__)


def _int(value, default=1):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@bp.route("/giocatori")
def listing():
    db = get_db()
    is_staff = g.user is not None and g.user["role"] == "staff"
    season = None
    if is_staff and request.args.get("stagione"):
        season = seasons_svc.get_season(db, _int(request.args.get("stagione"), 0))
    if season is None:
        season = seasons_svc.active_season(db)
    if season is None:
        return render_template("players/list.html", season=None, result=None, filters={}, teams=[], seasons=[])

    serie = request.args.get("serie", "").upper()
    ruolo = request.args.get("ruolo", "").upper()
    filters = {
        "q": request.args.get("q", "").strip()[:80],
        "serie": serie if serie in SERIES else "",
        "ruolo": ruolo if ruolo in ROLES else "",
        "squadra": request.args.get("squadra", "").strip()[:80],
        # Gli utenti vedono solo i giocatori selezionabili; lo staff può vedere anche gli altri.
        "stato": (request.args.get("stato") if is_staff and request.args.get("stato") in ("all", "inactive") else "active"),
    }
    result = players_svc.search_players(
        db, season["id"], q=filters["q"], serie=filters["serie"], ruolo=filters["ruolo"],
        team=filters["squadra"], state=filters["stato"], page=_int(request.args.get("page")),
        per_page=current_app.config["PLAYERS_PER_PAGE"],
    )
    query_args = {k: v for k, v in {
        "q": filters["q"], "serie": filters["serie"], "ruolo": filters["ruolo"], "squadra": filters["squadra"],
        "stato": filters["stato"] if filters["stato"] != "active" else "",
        "stagione": str(season["id"]) if is_staff and request.args.get("stagione") else "",
    }.items() if v}
    # medie dei soli voti ufficiali, in una query per tutta la pagina (nessuna query per riga)
    averages = votes_svc.display_averages(db, season["id"], [r["player_id"] for r in result["rows"]])
    return render_template(
        "players/list.html", season=season, result=result, filters=filters, is_staff=is_staff, query_args=query_args,
        averages=averages,
        teams=players_svc.teams_in_season(db, season["id"]),
        seasons=seasons_svc.list_seasons(db) if is_staff else [],
    )


@bp.route("/giocatori/<int:player_id>")
def detail(player_id):
    """Scheda del giocatore: media e andamento dei voti ufficiali (solo concorsi pubblicati)."""
    db = get_db()
    is_staff = g.user is not None and g.user["role"] == "staff"
    season = None
    if is_staff and request.args.get("stagione"):
        season = seasons_svc.get_season(db, _int(request.args.get("stagione"), 0))
    if season is None:
        season = seasons_svc.active_season(db)
    if season is None:
        abort(404)
    player = players_svc.get_player(db, season["id"], player_id)
    # come nel listone: chi non e' staff non vede i giocatori non selezionabili
    if player is None or (not player["selectable"] and not is_staff):
        abort(404)
    hist = votes_svc.history(db, season["id"], player_id)
    return render_template(
        "players/detail.html", season=season, player=player, hist=hist, is_staff=is_staff,
        chart=votes_svc.trend_chart(hist["entries"], hist["avg"]),
        stagione_arg={"stagione": season["id"]} if is_staff and request.args.get("stagione") else {},
    )
