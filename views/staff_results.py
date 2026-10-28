"""Risultati dei concorsi e pesi del punteggio: area staff."""
from __future__ import annotations

from dataclasses import replace

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for

from ..db import get_db
from ..domain.listone import ROLES
from ..domain.scoring import CATEGORY_LABELS, DEFAULT_WEIGHTS, ScoringError, parse_cell
from ..security import staff_guard
from ..services import contests as contests_svc
from ..services import results as R
from ..services import seasons as seasons_svc
from ..services import weights as weights_svc

bp = Blueprint("staff_results", __name__, url_prefix="/staff/concorsi/<int:contest_id>/risultati")
bp.before_request(staff_guard)
weights_bp = Blueprint("staff_weights", __name__, url_prefix="/staff/punteggi")
weights_bp.before_request(staff_guard)


def _cfg(contest_id=None):
    """Configurazione di calcolo della stagione del concorso (o di quella attiva), con il suo voto SV."""
    db = get_db()
    if contest_id is not None:
        season_id = _row(contest_id)["season_id"]
    else:
        season = seasons_svc.active_season(db)
        season_id = season["id"] if season else None
    sv = weights_svc.get_sv_vote(db, season_id, current_app.config) if season_id is not None else None
    return weights_svc.scoring_config_from(current_app.config, sv)


def _row(contest_id):
    row = contests_svc.get_contest(get_db(), contest_id)
    if row is None:
        abort(404)
    return row


def _back(contest_id, endpoint="staff_results.overview"):
    return redirect(url_for(endpoint, contest_id=contest_id))


def _run(contest_id, func, ok_message, *args, endpoint="staff_results.overview", **kwargs):
    _row(contest_id)
    try:
        func(get_db(), contest_id, *args, **kwargs)
        flash(ok_message, "ok")
    except R.ResultsError as exc:
        flash(str(exc), "error")
    return _back(contest_id, endpoint)


# ------------------------------------------------------------------- panoramica


@bp.route("/")
def overview(contest_id):
    db = get_db()
    row = _row(contest_id)
    contest = contests_svc.view(row)
    in_results = row["status"] in ("calculating", "published")
    grid = R.grid(db, contest_id) if in_results else None
    scores = R.contest_scores(db, contest_id) if in_results else None
    return render_template(
        "staff/results.html", contest=contest, in_results=in_results, grid=grid, scores=scores,
        stale=R.is_stale(row), weights=weights_svc.get_weights(db, row["season_id"]),
        runs=R.recent_runs(db, contest_id), changes=R.recent_changes(db, contest_id),
        lineups_count=contests_svc.lineup_count(db, contest_id),
        needed=len(R.needed_players(db, contest_id)))


@bp.route("/avvia", methods=["POST"])
def start(contest_id):
    return _run(contest_id, R.start_results, "Fase risultati avviata: ora si possono inserire i voti.", g.user["id"])


@bp.route("/calcola", methods=["POST"])
def calculate(contest_id):
    _row(contest_id)
    try:
        res = R.calculate(get_db(), contest_id, g.user["id"], _cfg(contest_id))
        if res.complete:
            flash(f"Punteggi calcolati per tutte le {len(res.lineups)} formazioni. Controlla e pubblica.", "ok")
        else:
            flash(f"Calcolo eseguito, ma mancano i dati di {len(res.incomplete)} giocatori: "
                  f"{sum(1 for l in res.lineups if l['total'] is None)} formazioni sono incomplete.", "warn")
    except R.ResultsError as exc:
        flash(str(exc), "error")
    return _back(contest_id)


@bp.route("/pubblica", methods=["POST"])
def publish(contest_id):
    _row(contest_id)
    try:
        result = R.confirm_results(get_db(), contest_id, g.user["id"], _cfg(contest_id),
                                   allow_single=current_app.config["ALLOW_SINGLE_ENTRY"])
        flash(f"Voti confermati e calcolo completato: pubblicati i risultati di {len(result.lineups)} formazioni.", "ok")
    except R.ResultsError as exc:
        flash(str(exc), "error")
    except Exception:
        current_app.logger.exception("Errore durante la conferma dei risultati del concorso %s", contest_id)
        flash("Errore durante il calcolo: la giornata non è stata confermata. I voti salvati sono conservati; riprova o controlla i dati.", "error")
    return _back(contest_id)


@bp.route("/voto-manuale", methods=["POST"])
def override(contest_id):
    _row(contest_id)
    try:
        pid = int(request.form.get("player_id", ""))
        if request.form.get("op") == "clear":
            R.clear_override(get_db(), contest_id, pid, g.user["id"], request.form.get("reason"))
            flash("Voto manuale tolto: il giocatore torna al voto calcolato.", "ok")
        else:
            R.set_override(get_db(), contest_id, pid, request.form.get("vote", ""), request.form.get("reason", ""), g.user["id"])
            flash("Voto finale inserito a mano e dichiarato come tale.", "ok")
    except (ValueError, R.ResultsError) as exc:
        flash(str(exc) if isinstance(exc, R.ResultsError) else "Giocatore non valido.", "error")
    return _back(contest_id)


# ----------------------------------------------------------------- immissione


def _sheet_values(grid, mine, form=None):
    """Valori da mostrare nelle caselle: quelli appena inviati (se c'e' stato un errore) oppure le mie compilazioni salvate."""
    values = {}
    for p in grid["rows"]:
        for c in p["cells"]:
            key = f"{p['player_id']}_{c['match_no']}"
            if form is not None:
                values[key] = {"sv": form.get(f"sv_{key}") == "1", "v": form.get(f"v_{key}", ""),
                               "b": form.get(f"b_{key}", ""), "m": form.get(f"m_{key}", "")}
            else:
                e = mine.get((p["player_id"], c["match_no"]))
                values[key] = ({"sv": not e["played"], "v": e["base_vote"] or "", "b": e["bonus_count"] or "", "m": e["malus_count"] or ""}
                               if e else {"sv": False, "v": "", "b": "", "m": ""})
    return values


@bp.route("/inserimento", methods=["GET", "POST"])
def sheet(contest_id):
    db = get_db()
    row = _row(contest_id)
    if row["status"] != "calculating":
        flash("L'immissione dei voti è aperta solo mentre il concorso è «in calcolo».", "error")
        return _back(contest_id)
    grid = R.grid(db, contest_id)
    mine = R.my_entries(db, contest_id, g.user["id"])
    weights = weights_svc.get_weights(db, row["season_id"])
    errors, values, status = [], None, 200
    if request.method == "POST":
        cells, values = {}, {}
        for p in grid["rows"]:
            for c in p["cells"]:
                m, key = c["match_no"], f"{p['player_id']}_{c['match_no']}"
                raw = {"sv": request.form.get(f"sv_{key}") == "1", "v": request.form.get(f"v_{key}", ""),
                       "b": request.form.get(f"b_{key}", ""), "m": request.form.get(f"m_{key}", "")}
                values[key] = raw
                try:
                    cells[(p["player_id"], m)] = parse_cell(raw["sv"], raw["v"], raw["b"], raw["m"])
                except ScoringError as exc:
                    errors.append(f"{p['name']} · partita {m}: {exc}")
        if not errors:
            try:
                s = R.save_sheet(db, contest_id, g.user["id"], cells)
                flash(f"Compilazione salvata: {s['saved']} celle. {s['verified']} confermate dall'accordo con un'altra compilazione, "
                      f"{s['conflict']} in conflitto, {s['pending']} in attesa di un'altra compilazione.", "ok" if not s["conflict"] else "warn")
                return redirect(url_for("staff_results.verify", contest_id=contest_id) if s["conflict"] else url_for("staff_results.overview", contest_id=contest_id))
            except R.ResultsError as exc:
                errors.append(str(exc))
        status = 422
    if values is None:
        values = _sheet_values(grid, mine)
    groups = [(r, CATEGORY_LABELS[r], [p for p in grid["rows"] if p["ruolo"] == r]) for r in ROLES]
    return render_template("staff/results_sheet.html", contest=contests_svc.view(row), grid=grid, groups=[x for x in groups if x[2]],
                           weights=weights, values=values, errors=errors, user=g.user), status


# --------------------------------------------------------------- dati e correzioni


@bp.route("/dati")
def data(contest_id):
    """Mostra i dati registrati, quelli mancanti e lo storico delle correzioni."""
    db = get_db()
    row = _row(contest_id)
    if row["status"] not in ("calculating", "published"):
        flash("I dati dei risultati sono disponibili dopo l'avvio della fase risultati.", "error")
        return _back(contest_id)

    contest = contests_svc.view(row)
    grid = R.grid(db, contest_id)
    entered, missing = [], []
    for player in grid["rows"]:
        for cell in player["cells"]:
            item = {"player": player, "match_no": cell["match_no"], "state": cell["state"], "stat": cell["stat"]}
            if cell["stat"] is None:
                if cell["state"] == "missing":
                    missing.append(item)
            else:
                entered.append(item)

    return render_template(
        "staff/results_data.html", contest=contest, entered=entered, missing=missing,
        weights=weights_svc.get_weights(db, row["season_id"]),
        changes=R.recent_changes(db, contest_id, 100),
    )


# ------------------------------------------------------------------- verifica


@bp.route("/verifica")
def verify(contest_id):
    db = get_db()
    row = _row(contest_id)
    if row["status"] not in ("calculating", "published"):
        flash("La verifica è disponibile quando i risultati sono avviati.", "error")
        return _back(contest_id)
    grid = R.grid(db, contest_id)
    conflicts, pending, verified, missing = [], [], [], []
    for p in grid["rows"]:
        for c in p["cells"]:
            item = {"player": p, **c}
            {"conflict": conflicts, "pending": pending, "verified": verified, "corrected": verified, "missing": missing}[c["state"]].append(item)
    return render_template("staff/results_verify.html", contest=contests_svc.view(row), conflicts=conflicts, pending=pending,
                           verified=verified, missing=missing, counts=grid["counts"], weights=weights_svc.get_weights(db, row["season_id"]),
                           allow_single=current_app.config["ALLOW_SINGLE_ENTRY"], changes=R.recent_changes(db, contest_id, 50))


@bp.route("/cella", methods=["POST"])
def cell(contest_id):
    _row(contest_id)
    f = request.form
    try:
        pid, m = int(f.get("player_id", "")), int(f.get("match_no", ""))
        choice = f.get("choice", "")
        kwargs = dict(action="", actor_id=g.user["id"], note=f.get("note", ""), allow_single=current_app.config["ALLOW_SINGLE_ENTRY"])
        if choice == "single":
            kwargs["action"] = "accept_single"
        elif choice.startswith("entry:"):
            kwargs.update(action="take_entry", entry_id=int(choice.split(":", 1)[1]))
        else:
            kwargs["action"] = "custom"
            try:
                key = f"{pid}_{m}"      # stessi nomi dei campi del modulo (macro cell_fields)
                stat = parse_cell(f.get(f"sv_{key}") == "1", f.get(f"v_{key}", ""), f.get(f"b_{key}", ""), f.get(f"m_{key}", ""))
            except ScoringError as exc:
                raise R.ResultsError(str(exc)) from exc
            if stat is None:
                raise R.ResultsError("Indica i valori da registrare.")
            kwargs["stat"] = stat
        R.decide_cell(get_db(), contest_id, pid, m, **kwargs)
        flash("Dato registrato.", "ok")
    except ValueError:
        flash("Richiesta non valida.", "error")
    except R.ResultsError as exc:
        flash(str(exc), "error")
    return _back(contest_id, "staff_results.verify")


@bp.route("/convalida-singole", methods=["POST"])
def accept_singles(contest_id):
    _row(contest_id)
    try:
        n = R.accept_all_single(get_db(), contest_id, g.user["id"], current_app.config["ALLOW_SINGLE_ENTRY"])
        flash(f"Convalidate {n} compilazioni singole.", "ok")
    except R.ResultsError as exc:
        flash(str(exc), "error")
    return _back(contest_id, "staff_results.verify")


# ----------------------------------------------------------------- ricalcolo


@bp.route("/ricalcolo", methods=["GET", "POST"])
def recalculation(contest_id):
    db = get_db()
    row = _row(contest_id)
    if request.method == "POST":
        try:
            effects = R.apply_recalculation(db, contest_id, g.user["id"], _cfg(contest_id))
            flash(f"Ricalcolo applicato: {len(effects['lineups'])} punteggi di formazione modificati.", "ok")
            return _back(contest_id)
        except R.ResultsError as exc:
            flash(str(exc), "error")
            return _back(contest_id, "staff_results.recalculation")
    if row["status"] != "published":
        flash("Il ricalcolo guidato riguarda i concorsi pubblicati.", "error")
        return _back(contest_id)
    prev = R.preview_recalculation(db, contest_id, _cfg(contest_id))
    return render_template("staff/results_recalc.html", contest=contests_svc.view(row), prev=prev)


# ------------------------------------------------------------------------ pesi


@weights_bp.route("/", methods=["GET", "POST"])
def weights():
    db = get_db()
    season = seasons_svc.active_season(db)
    if season is None:
        flash("Non c'è una stagione attiva.", "error")
        return redirect(url_for("staff.index"))
    error, current = None, weights_svc.get_weights(db, season["id"])
    if request.method == "POST":
        form = {r: {"bonus_label": request.form.get(f"bl_{r}", ""), "bonus_weight": request.form.get(f"bw_{r}", ""),
                    "malus_label": request.form.get(f"ml_{r}", ""), "malus_weight": request.form.get(f"mw_{r}", "")} for r in ROLES}
        try:
            weights_svc.save_weights(db, season["id"], form, g.user["id"],
                                     sv_vote=request.form.get("sv_vote", ""), app_config=current_app.config)
            flash("Punteggi salvati. I concorsi già in calcolo o pubblicati risultano «da ricalcolare».", "ok")
            return redirect(url_for("staff_weights.weights"))
        except weights_svc.WeightsError as exc:
            error = str(exc)
    return render_template("staff/weights.html", season=season, weights=current, defaults=DEFAULT_WEIGHTS, error=error,
                           custom=weights_svc.has_custom_weights(db, season["id"]), categories=CATEGORY_LABELS,
                           sv_vote=weights_svc.get_sv_vote(db, season["id"], current_app.config),
                           sv_default=weights_svc.sv_default(current_app.config), form=request.form)
