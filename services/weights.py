"""Pesi bonus/malus per stagione e parametri di calcolo."""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from ..db import transaction
from ..domain.listone import ROLES
from ..domain.scoring import (DEFAULT_WEIGHTS, MAX_VOTE_DECIMALS, VOTE_MAX, VOTE_MIN, RoleWeights, ScoringConfig,
                              ScoringError, canon, fmt, to_decimal)
from . import audit

MAX_WEIGHT = Decimal("10")


class WeightsError(Exception):
    pass


def sv_default(app_config) -> Decimal:
    """Voto SV predefinito (5,5), usato finché lo staff non ne sceglie un altro per la stagione."""
    return to_decimal(app_config["SCORE_MISSING_MATCH_VOTE"])


def get_sv_vote(db, season_id: int, app_config) -> Decimal:
    """Voto SV della stagione: quello scelto dallo staff, altrimenti il predefinito."""
    row = db.execute("SELECT sv_vote FROM seasons WHERE id = ?", (season_id,)).fetchone()
    if row is None or row["sv_vote"] in (None, ""):
        return sv_default(app_config)
    return Decimal(row["sv_vote"])


def scoring_config_from(app_config, sv_vote: Decimal | None = None) -> ScoringConfig:
    """Con `sv_vote` (voto SV della stagione) lo stesso valore vale per una partita SV e per chi non gioca
    nessuna partita (cioè è SV in tutte): senza, restano i due valori predefiniti dell'ambiente."""
    return ScoringConfig(
        matches=2,
        not_played_vote=sv_vote if sv_vote is not None else to_decimal(app_config["SCORE_NOT_PLAYED_VOTE"]),
        missing_match_vote=sv_vote if sv_vote is not None else to_decimal(app_config["SCORE_MISSING_MATCH_VOTE"]),
        decimals=(int(app_config["SCORE_DECIMALS"]) if str(app_config["SCORE_DECIMALS"]).strip() != "" else None),
    )


def config_for(contest_row, base: ScoringConfig) -> ScoringConfig:
    return replace(base, matches=contest_row["matches_count"])


def get_weights(db, season_id: int) -> dict[str, RoleWeights]:
    """Pesi salvati per la stagione; se non ci sono (tutti e sei) valgono quelli del regolamento."""
    rows = db.execute("SELECT * FROM scoring_weights WHERE season_id = ?", (season_id,)).fetchall()
    if {r["ruolo"] for r in rows} != set(ROLES):
        return dict(DEFAULT_WEIGHTS)
    return {r["ruolo"]: RoleWeights(r["bonus_label"], Decimal(r["bonus_weight"]), r["malus_label"], Decimal(r["malus_weight"]))
            for r in rows}


def has_custom_weights(db, season_id: int) -> bool:
    return db.execute("SELECT COUNT(*) AS n FROM scoring_weights WHERE season_id = ?", (season_id,)).fetchone()["n"] > 0


def _parse(text: str, what: str) -> Decimal:
    try:
        d = to_decimal(text)
    except ScoringError as exc:
        raise WeightsError(f"{what}: {exc}") from exc
    if d < 0 or d > MAX_WEIGHT:
        raise WeightsError(f"{what}: il peso deve essere tra 0 e {MAX_WEIGHT}.")
    if d.as_tuple().exponent < -3:
        raise WeightsError(f"{what}: al massimo 3 decimali.")
    return d


def _parse_sv(text: str) -> Decimal:
    try:
        d = to_decimal(text)
    except ScoringError as exc:
        raise WeightsError(f"Voto SV: {exc}") from exc
    if d.as_tuple().exponent < -MAX_VOTE_DECIMALS:
        raise WeightsError(f"Voto SV: al massimo {MAX_VOTE_DECIMALS} decimali.")
    if not (VOTE_MIN <= d <= VOTE_MAX):
        raise WeightsError(f"Voto SV: deve essere tra {fmt(VOTE_MIN)} e {fmt(VOTE_MAX)}.")
    return d


def save_weights(db, season_id: int, form: dict[str, dict[str, str]], actor_id, *, sv_vote: str | None = None,
                 app_config=None) -> None:
    """form: {ruolo: {bonus_label, bonus_weight, malus_label, malus_weight}}. Sostituisce i sei pesi in blocco.

    `sv_vote` (testo, opzionale): voto SV della stagione; salvato nella stessa transazione dei pesi.
    """
    new_sv = _parse_sv(sv_vote) if sv_vote is not None else None
    parsed: dict[str, RoleWeights] = {}
    for r in ROLES:
        f = form.get(r) or {}
        bl, ml = " ".join((f.get("bonus_label") or "").split()), " ".join((f.get("malus_label") or "").split())
        if not (1 <= len(bl) <= 40 and 1 <= len(ml) <= 40):
            raise WeightsError(f"Ruolo {r}: i nomi di bonus e malus sono obbligatori (massimo 40 caratteri).")
        parsed[r] = RoleWeights(bl, _parse(f.get("bonus_weight", ""), f"Ruolo {r}, bonus"), ml,
                                _parse(f.get("malus_weight", ""), f"Ruolo {r}, malus"))
    with transaction(db):
        if not db.execute("SELECT 1 FROM seasons WHERE id = ?", (season_id,)).fetchone():
            raise WeightsError("Stagione inesistente.")
        before = get_weights(db, season_id)
        old_sv = get_sv_vote(db, season_id, app_config) if app_config is not None else None
        if new_sv is not None:
            db.execute("UPDATE seasons SET sv_vote = ? WHERE id = ?", (canon(new_sv), season_id))
        db.execute("DELETE FROM scoring_weights WHERE season_id = ?", (season_id,))
        for r, w in parsed.items():
            db.execute("INSERT INTO scoring_weights (season_id, ruolo, bonus_label, bonus_weight, malus_label, malus_weight) "
                       "VALUES (?, ?, ?, ?, ?, ?)", (season_id, r, w.bonus_label, canon(w.bonus_weight), w.malus_label, canon(w.malus_weight)))
        changed = {r: {"from": [canon(before[r].bonus_weight), canon(before[r].malus_weight)],
                       "to": [canon(w.bonus_weight), canon(w.malus_weight)]}
                   for r, w in parsed.items()
                   if (before[r].bonus_weight, before[r].malus_weight, before[r].bonus_label, before[r].malus_label)
                   != (w.bonus_weight, w.malus_weight, w.bonus_label, w.malus_label)}
        # i concorsi gia' in calcolo o pubblicati hanno punteggi che dipendono dai pesi: diventano "da ricalcolare"
        stale = db.execute("UPDATE contests SET data_version = data_version + 1 WHERE season_id = ? "
                           "AND status IN ('calculating', 'published')", (season_id,)).rowcount
        details = {"changed": changed, "contests_to_recalculate": stale}
        if new_sv is not None and old_sv != new_sv:
            details["sv_vote"] = {"from": canon(old_sv) if old_sv is not None else None, "to": canon(new_sv)}
        audit.log(db, actor_id, "weights.save", "season", season_id, details)
