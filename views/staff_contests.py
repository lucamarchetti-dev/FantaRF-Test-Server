"""Gestione dei concorsi: area staff."""
from __future__ import annotations

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for

from ..db import get_db
from ..domain.contest import default_deadline
from ..security import staff_guard
from ..services import contests as contests_svc
from ..services import lineups as lineups_svc
from ..services import seasons as seasons_svc
from ..timeutil import local_to_utc, utc_to_local_input, utcnow

bp = Blueprint("staff_contests", __name__, url_prefix="/staff/concorsi")
bp.before_request(staff_guard)


def _tz() -> str:
    return current_app.config["TIMEZONE"]


def _matches(text, default=2) -> int:
    try:
        return int(text) if str(text or "").strip() else default
    except ValueError as exc:
        raise contests_svc.ContestError("Il numero di partite deve essere un intero da 1 a 4.") from exc


def _local(text):
    """Campo data/ora del modulo (ora locale) -> UTC; vuoto -> None."""
    text = (text or "").strip()
    return local_to_utc(text, _tz()) if text else None


def _default_close_input() -> str:
    cfg = current_app.config
    hh, mm = cfg["DEFAULT_DEADLINE_TIME"].split(":")
    when = default_deadline(utcnow(), cfg["DEFAULT_DEADLINE_WEEKDAY"], int(hh), int(mm), _tz())
    return utc_to_local_input(when.strftime("%Y-%m-%dT%H:%M:%SZ"), _tz())


@bp.route("/", methods=["GET", "POST"])
def index():
    db = get_db()
    season = seasons_svc.active_season(db)
    if season is None:
        return render_template("staff/contests.html", season=None, contests=[], form={}, error=None)
    error = None
    form = request.form
    if request.method == "POST":
        try:
            try:
                number = int(request.form.get("number", ""))
            except ValueError as exc:
                raise contests_svc.ContestError("Il numero del concorso deve essere un intero positivo.") from exc
            try:
                opens, closes = _local(request.form.get("opens_at")), _local(request.form.get("closes_at"))
            except ValueError as exc:
                raise contests_svc.ContestError(str(exc)) from exc
            contest_id = contests_svc.create_contest(
                db, season_id=season["id"], number=number, name=request.form.get("name"), opens_at=opens,
                closes_at=closes, info=request.form.get("info"), one_per_role=request.form.get("one_per_role") == "1",
                actor_id=g.user["id"], matches_count=_matches(request.form.get("matches_count")))
            flash("Concorso creato in bozza. Quando è pronto, aprilo dalla sua pagina.", "ok")
            return redirect(url_for("staff_contests.detail", contest_id=contest_id))
        except contests_svc.ContestError as exc:
            error = str(exc)
    else:
        form = {"number": contests_svc.next_number(db, season["id"]), "closes_at": _default_close_input(), "matches_count": "2",
                "one_per_role": "1" if current_app.config["DEFAULT_ONE_PER_ROLE"] else ""}
    return render_template("staff/contests.html", season=season, contests=contests_svc.list_contests(db, season["id"], True),
                           form=form, error=error)


def _load(contest_id):
    row = contests_svc.get_contest(get_db(), contest_id)
    if row is None:
        abort(404)
    return row


@bp.route("/<int:contest_id>")
def detail(contest_id):
    db = get_db()
    row = _load(contest_id)
    contest = contests_svc.view(row)
    lineups = lineups_svc.contest_lineups(db, contest_id)
    return render_template(
        "staff/contest_detail.html", contest=contest, lineups=lineups,
        missing=lineups_svc.users_without_lineup(db, contest_id, row["season_id"]),
        reopenings=contests_svc.reopenings(db, contest_id),
        close_input=utc_to_local_input(row["closes_at"], _tz()), open_input=utc_to_local_input(row["opens_at"], _tz()),
        new_deadline_input=_default_close_input(), form=None, error=None)


def _redirect(contest_id):
    return redirect(url_for("staff_contests.detail", contest_id=contest_id))


@bp.route("/<int:contest_id>/modifica", methods=["POST"])
def update(contest_id):
    row = _load(contest_id)
    try:
        try:
            opens, closes = _local(request.form.get("opens_at")), _local(request.form.get("closes_at"))
        except ValueError as exc:
            raise contests_svc.ContestError(str(exc)) from exc
        contests_svc.update_contest(
            get_db(), contest_id, name=request.form.get("name"), opens_at=opens, closes_at=closes,
            info=request.form.get("info"),
            one_per_role=(request.form.get("one_per_role") == "1") if row["status"] != "closed" else bool(row["one_per_role"]),
            actor_id=g.user["id"],
            matches_count=(_matches(request.form.get("matches_count"), row["matches_count"]) if row["status"] != "closed" else None))
        flash("Modifiche salvate.", "ok")
    except contests_svc.ContestError as exc:
        flash(str(exc), "error")
    return _redirect(contest_id)


def _action(contest_id, func, ok_message, **kwargs):
    _load(contest_id)
    try:
        func(get_db(), contest_id, **kwargs)
        flash(ok_message, "ok")
    except contests_svc.ContestError as exc:
        flash(str(exc), "error")
    return _redirect(contest_id)


@bp.route("/<int:contest_id>/apri", methods=["POST"])
def open_(contest_id):
    return _action(contest_id, contests_svc.open_contest, "Concorso aperto: i partecipanti possono inviare la formazione.",
                   actor_id=g.user["id"])


@bp.route("/<int:contest_id>/chiudi", methods=["POST"])
def close(contest_id):
    return _action(contest_id, contests_svc.close_contest, "Concorso chiuso: non si accettano più formazioni.",
                   actor_id=g.user["id"])


@bp.route("/<int:contest_id>/riapri", methods=["POST"])
def reopen(contest_id):
    _load(contest_id)
    try:
        try:
            new_close = _local(request.form.get("new_closes_at"))
        except ValueError as exc:
            raise contests_svc.ContestError(str(exc)) from exc
        if new_close is None:
            raise contests_svc.ContestError("Indica la nuova scadenza.")
        contests_svc.reopen_contest(get_db(), contest_id, new_closes_at=new_close, reason=request.form.get("reason"),
                                    actor_id=g.user["id"])
        flash("Concorso riaperto. La riapertura è stata registrata con il motivo indicato.", "ok")
    except contests_svc.ContestError as exc:
        flash(str(exc), "error")
    return _redirect(contest_id)


@bp.route("/<int:contest_id>/elimina", methods=["POST"])
def delete(contest_id):
    _load(contest_id)
    try:
        contests_svc.delete_draft(get_db(), contest_id, g.user["id"])
        flash("Bozza eliminata.", "ok")
        return redirect(url_for("staff_contests.index"))
    except contests_svc.ContestError as exc:
        flash(str(exc), "error")
        return _redirect(contest_id)


@bp.route("/<int:contest_id>/formazioni/<int:lineup_id>")
def lineup_detail(contest_id, lineup_id):
    db = get_db()
    row = _load(contest_id)
    head = lineups_svc.lineup_header(db, lineup_id)
    if head is None or head["contest_id"] != contest_id:
        abort(404)
    return render_template("staff/lineup_detail.html", contest=contests_svc.view(row), head=head,
                           versions=lineups_svc.versions(db, lineup_id))
