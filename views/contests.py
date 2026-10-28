"""Concorsi e formazioni: lato partecipante."""
from __future__ import annotations

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from ..crests import crest_url
from ..db import get_db
from ..services import admission
from ..services import contests as contests_svc
from ..services import lineups as lineups_svc
from ..services import players as players_svc
from ..services import results as results_svc
from ..services import seasons as seasons_svc
from ..security import login_required
from ..timeutil import to_local

bp = Blueprint("contests", __name__)


def _is_staff() -> bool:
    return g.user is not None and g.user["role"] == "staff"


@bp.route("/concorsi")
def listing():
    db = get_db()
    season = seasons_svc.active_season(db)
    contests = contests_svc.list_contests(db, season["id"], include_drafts=_is_staff()) if season else []
    mine = lineups_svc.participation(db, g.user["id"], [c["id"] for c in contests]) if g.user else {}
    scores = results_svc.published_totals(db, g.user["id"], [c["id"] for c in contests]) if g.user else {}
    return render_template("contests/list.html", season=season, contests=contests, mine=mine, scores=scores)


@bp.route("/concorsi/<int:contest_id>")
def detail(contest_id):
    db = get_db()
    row = contests_svc.get_contest(db, contest_id)
    if row is None or (row["status"] == "draft" and not _is_staff()):
        abort(404)
    contest = contests_svc.view(row)
    season = seasons_svc.get_season(db, row["season_id"])
    lineup = lineups_svc.current_lineup(db, contest_id, g.user["id"]) if g.user else None
    excluded = admission.is_excluded(db, row["season_id"], g.user["id"]) if g.user else None
    result = results_svc.user_result(db, contest_id, g.user["id"]) if g.user else None
    return render_template("contests/detail.html", contest=contest, season=season, lineup=lineup, excluded=excluded, result=result)


def _builder_context(db, contest, selected_ids, team_name, errors=(), warnings=()):
    season_id = contest["season_id"]
    players = players_svc.all_selectable(db, season_id)
    # stemmi disponibili, per nome squadra: dove manca il file il selettore mostra lo spazio vuoto
    crests = {t: url for t in {p["t"] for p in players} if (url := crest_url(t))}
    return dict(
        contest=contest,
        players=players,
        crests=crests,
        selected_ids=selected_ids,
        team_name=team_name,
        errors=list(errors),
        warnings=list(warnings),
    )


@bp.route("/concorsi/<int:contest_id>/formazione", methods=["GET", "POST"])
@login_required
def builder(contest_id):
    db = get_db()
    row = contests_svc.get_contest(db, contest_id)
    if row is None or row["status"] == "draft":
        abort(404)
    contest = contests_svc.view(row)

    if request.method == "POST":
        ids, bad = lineups_svc.parse_ids(request.form.getlist("player_id"))
        team_name = request.form.get("team_name", "")
        result = lineups_svc.submit(db, contest_id=contest_id, user_id=g.user["id"], player_ids=ids,
                                    team_name=team_name, had_bad_ids=bad)
        if result.ok:
            when = to_local(result.submitted_at)
            if result.outcome == "created":
                flash(f"Formazione inviata: versione 1, registrata il {when}.", "ok")
            elif result.outcome == "replaced":
                flash(f"Formazione aggiornata: ora vale la versione {result.version}, registrata il {when}. "
                      "Le versioni precedenti non contano più.", "ok")
            else:
                flash(f"Nessuna modifica: la tua giocata registrata è già questa (versione {result.version}, "
                      f"inviata il {when}).", "ok")
            for w in result.warnings:
                flash(w.message, "warn")
            return redirect(url_for("contests.detail", contest_id=contest_id))
        if result.blocking:
            flash(result.errors[0].message, "error")
            return redirect(url_for("contests.detail", contest_id=contest_id))
        ctx = _builder_context(db, contest, [c.player_id for c in result.selected], team_name,
                               result.errors, result.warnings)
        return render_template("contests/builder.html", **ctx), 422

    if contest["effective"] != "open":
        flash("Il concorso non accetta formazioni in questo momento.", "error")
        return redirect(url_for("contests.detail", contest_id=contest_id))
    if admission.is_excluded(db, row["season_id"], g.user["id"]):
        flash("Non sei ammesso a partecipare a questa stagione. Contatta lo staff.", "error")
        return redirect(url_for("contests.detail", contest_id=contest_id))
    current = lineups_svc.current_lineup(db, contest_id, g.user["id"])
    selected = [p["player_id"] for p in current["players"]] if current else []
    team_name = (current["fanta_team_name"] or "") if current else lineups_svc.last_team_name(db, g.user["id"], row["season_id"])
    return render_template("contests/builder.html", **_builder_context(db, contest, selected, team_name),
                           current=current)
