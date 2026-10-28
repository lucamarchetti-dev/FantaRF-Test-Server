"""Regole della stagione (scale di punti, premi, strike, spareggio): i default sono quelli del regolamento S13."""
from __future__ import annotations

import json
import re

from ..db import transaction
from ..domain.ranking import TIEBREAK_MODES, Rules
from ..timeutil import utcnow_iso
from . import audit

MAX_LIST, MAX_AMOUNT = 20, 10_000_000
LIST_FIELDS = ("weekly_prizes", "points_scale", "general_prizes", "team_scale")


class RulesError(Exception):
    pass


def get_rules(db, season_id: int) -> Rules:
    row = db.execute("SELECT rules_json FROM season_rules WHERE season_id = ?", (season_id,)).fetchone()
    base = Rules()
    if row is None:
        return base
    data = json.loads(row["rules_json"])
    kw = {k: tuple(data[k]) for k in LIST_FIELDS if k in data}
    for k in ("loyalty_prize", "max_strikes", "team_scale_confirmed", "tiebreak"):
        if k in data:
            kw[k] = data[k]
    return Rules(**{**base.__dict__, **kw})


def has_custom_rules(db, season_id: int) -> bool:
    return db.execute("SELECT 1 FROM season_rules WHERE season_id = ?", (season_id,)).fetchone() is not None


def to_form(rules: Rules) -> dict:
    d = {k: ", ".join(str(x) for x in getattr(rules, k)) for k in LIST_FIELDS}
    d.update(loyalty_prize=str(rules.loyalty_prize), max_strikes=str(rules.max_strikes), tiebreak=rules.tiebreak,
             team_scale_confirmed="1" if rules.team_scale_confirmed else "")
    return d


def _numbers(text: str, label: str, allow_empty: bool) -> tuple[int, ...]:
    text = (text or "").strip()
    if not text:
        if allow_empty:
            return ()
        raise RulesError(f"{label}: indica almeno un valore.")
    parts = [p for p in re.split(r"[,;\s]+", text) if p]
    if len(parts) > MAX_LIST:
        raise RulesError(f"{label}: al massimo {MAX_LIST} valori.")
    out = []
    for p in parts:
        if not p.isdigit() or int(p) > MAX_AMOUNT:
            raise RulesError(f"{label}: «{p}» non è un numero intero valido (0–{MAX_AMOUNT}). Separa i valori con la virgola, senza punti delle migliaia.")
        out.append(int(p))
    return tuple(out)


def _int(text, label, low, high) -> int:
    t = (text or "").strip()
    if not t.isdigit() or not (low <= int(t) <= high):
        raise RulesError(f"{label}: serve un numero intero tra {low} e {high}.")
    return int(t)


def save_rules(db, season_id: int, form: dict, actor_id) -> None:
    rules = Rules(
        weekly_prizes=_numbers(form.get("weekly_prizes"), "Premi settimanali", True),
        points_scale=_numbers(form.get("points_scale"), "Punti della generale", False),
        general_prizes=_numbers(form.get("general_prizes"), "Premi finali", True),
        team_scale=_numbers(form.get("team_scale"), "Punti delle squadre", False),
        loyalty_prize=_int(form.get("loyalty_prize"), "Premio fedeltà", 0, MAX_AMOUNT),
        max_strikes=_int(form.get("max_strikes"), "Strike massimi", 1, 20),
        team_scale_confirmed=form.get("team_scale_confirmed") == "1",
        tiebreak=form.get("tiebreak", ""))
    if rules.tiebreak not in TIEBREAK_MODES:
        raise RulesError("Criterio di spareggio non valido.")
    with transaction(db):
        if not db.execute("SELECT 1 FROM seasons WHERE id = ?", (season_id,)).fetchone():
            raise RulesError("Stagione inesistente.")
        before = to_form(get_rules(db, season_id))
        data = {k: list(getattr(rules, k)) for k in LIST_FIELDS}
        data.update(loyalty_prize=rules.loyalty_prize, max_strikes=rules.max_strikes, team_scale_confirmed=rules.team_scale_confirmed,
                    tiebreak=rules.tiebreak)
        db.execute("INSERT INTO season_rules (season_id, rules_json, updated_by, updated_at) VALUES (?, ?, ?, ?) "
                   "ON CONFLICT (season_id) DO UPDATE SET rules_json = excluded.rules_json, updated_by = excluded.updated_by, "
                   "updated_at = excluded.updated_at", (season_id, json.dumps(data), actor_id, utcnow_iso()))
        after = to_form(rules)
        audit.log(db, actor_id, "season.rules.save", "season", season_id, {k: {"from": before[k], "to": after[k]} for k in after if before[k] != after[k]})
