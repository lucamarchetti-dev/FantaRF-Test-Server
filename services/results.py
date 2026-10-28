"""Risultati di un concorso: immissione indipendente, riconciliazione, calcolo, pubblicazione, correzioni.

Ciclo di vita (campo contests.status):
    closed --avvia--> calculating --pubblica--> published
- in 'calculating' lo staff compila i voti (due persone, in modo indipendente), risolve i conflitti e calcola;
- in 'published' i punteggi sono visibili ai partecipanti; i dati si cambiano SOLO con una correzione motivata,
  e i punteggi pubblicati restano quelli di prima finche' lo staff non conferma il ricalcolo guardandone gli effetti.
Ogni dato che incide sui punteggi incrementa contests.data_version: se i punteggi salvati sono di una versione
precedente, risultano "da ricalcolare" e non si puo' pubblicare.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from fractions import Fraction

from ..db import transaction
from ..domain.contest import effective_status
from ..domain.scoring import (MatchStat, PlayerVote, ScoringConfig, ScoringError, canon, compute_player_vote, explain,
                              fmt, fstr, lineup_total, quantize, to_decimal)
from ..timeutil import iso, parse_iso, utcnow
from . import audit
from .contests import view as contest_view
from .weights import config_for, get_weights

LEGAL_FINAL_MAX = Decimal("30")   # limite di buon senso per un voto finale inserito a mano (non e' una regola)


class ResultsError(Exception):
    """Errore di stato/validazione con messaggio mostrabile."""


# ---------------------------------------------------------------- lettura base


def _contest(db, contest_id: int):
    row = db.execute("SELECT * FROM contests WHERE id = ?", (contest_id,)).fetchone()
    if row is None:
        raise ResultsError("Concorso inesistente.")
    return row


def needed_players(db, contest_id: int) -> list[dict]:
    """I giocatori da valutare: quelli presenti nell'ULTIMA versione di ogni formazione (come il foglio «Giocatori Scelti»).
    Il ruolo usato e' quello dell'istantanea della formazione piu' recente che lo contiene."""
    rows = db.execute(
        """SELECT lp.player_id, lp.full_name, lp.ruolo, lp.serie, lp.team_name, lv.submitted_at, l.id AS lineup_id
             FROM lineups l
             JOIN lineup_versions lv ON lv.lineup_id = l.id
              AND lv.version = (SELECT MAX(version) FROM lineup_versions WHERE lineup_id = l.id)
             JOIN lineup_players lp ON lp.version_id = lv.id
            WHERE l.contest_id = ?
            ORDER BY lv.submitted_at, lp.player_id""", (contest_id,)).fetchall()
    out: dict[int, dict] = {}
    for r in rows:
        p = out.setdefault(r["player_id"], {"player_id": r["player_id"], "roles": set(), "lineups": 0})
        p.update(name=r["full_name"], ruolo=r["ruolo"], serie=r["serie"], team=r["team_name"])   # l'ultima in ordine temporale vince
        p["roles"].add(r["ruolo"])
        p["lineups"] += 1
    from ..domain.lineup import ROLE_ORDER
    return sorted(out.values(), key=lambda p: (ROLE_ORDER[p["ruolo"]], p["name"].casefold(), p["player_id"]))


def _entries(db, contest_id: int) -> dict[tuple[int, int], list[dict]]:
    out: dict[tuple[int, int], list[dict]] = {}
    for r in db.execute(
            "SELECT e.*, u.display_name AS user_name FROM vote_entries e JOIN users u ON u.id = e.entered_by "
            "WHERE e.contest_id = ? ORDER BY e.id", (contest_id,)):
        out.setdefault((r["player_id"], r["match_no"]), []).append(dict(r))
    return out


def _stats(db, contest_id: int) -> dict[tuple[int, int], dict]:
    return {(r["player_id"], r["match_no"]): dict(r)
            for r in db.execute("SELECT * FROM match_stats WHERE contest_id = ?", (contest_id,))}


def _overrides(db, contest_id: int) -> dict[int, dict]:
    return {r["player_id"]: dict(r) for r in db.execute(
        "SELECT o.*, u.display_name AS user_name FROM player_overrides o LEFT JOIN users u ON u.id = o.user_id "
        "WHERE o.contest_id = ?", (contest_id,))}


def _sig(played, base_vote, bonus, malus):
    return (int(played), base_vote, int(bonus), int(malus))


def _entry_sig(e):
    return _sig(e["played"], e["base_vote"], e["bonus_count"], e["malus_count"])


def stat_values(stat: dict) -> dict:
    return {"played": bool(stat["played"]), "base_vote": stat["base_vote"], "bonus": stat["bonus_count"], "malus": stat["malus_count"]}


def cell_state(entries: list[dict], stat: dict | None) -> str:
    if stat is not None:
        return "corrected" if stat["status"] == "corrected" else "verified"
    if not entries:
        return "missing"
    return "pending" if len(entries) == 1 else "conflict"


def grid(db, contest_id: int) -> dict:
    """Stato di ogni cella (giocatore x partita), per le pagine di immissione e verifica."""
    contest = _contest(db, contest_id)
    n = contest["matches_count"]
    entries, stats = _entries(db, contest_id), _stats(db, contest_id)
    rows, counts = [], {"missing": 0, "pending": 0, "conflict": 0, "verified": 0, "corrected": 0}
    for p in needed_players(db, contest_id):
        cells = []
        for m in range(1, n + 1):
            key = (p["player_id"], m)
            state = cell_state(entries.get(key, []), stats.get(key))
            counts[state] += 1
            cells.append({"match_no": m, "state": state, "entries": entries.get(key, []), "stat": stats.get(key)})
        rows.append({**p, "cells": cells, "complete": all(c["state"] in ("verified", "corrected") for c in cells)})
    return {"rows": rows, "counts": counts, "matches": n, "total_cells": len(rows) * n}


# ------------------------------------------------------------------ transizioni


def _bump(db, contest_id: int) -> None:
    db.execute("UPDATE contests SET data_version = data_version + 1, updated_at = ? WHERE id = ?", (iso(utcnow()), contest_id))


def start_results(db, contest_id: int, actor_id, now=None) -> None:
    now = now or utcnow()
    with transaction(db):
        row = _contest(db, contest_id)
        v = contest_view(row, now)
        if v["effective"] != "closed":
            raise ResultsError("I risultati si avviano solo a concorso chiuso (scaduto o chiuso dallo staff).")
        if db.execute("SELECT COUNT(*) AS n FROM lineups WHERE contest_id = ?", (contest_id,)).fetchone()["n"] == 0:
            raise ResultsError("Nessuna formazione inviata: non c'è nulla da valutare.")
        db.execute("UPDATE contests SET status = 'calculating', updated_at = ? WHERE id = ?", (iso(now), contest_id))
        db.execute("INSERT INTO score_runs (contest_id, kind, user_id, data_version, summary_json, created_at) VALUES (?, 'start', ?, ?, ?, ?)",
                   (contest_id, actor_id, row["data_version"], json.dumps({"players": len(needed_players(db, contest_id))}), iso(now)))
        audit.log(db, actor_id, "results.start", "contest", contest_id)


# --------------------------------------------------------------------- immissione


def _write_stat(db, contest_id, pid, m, ms: MatchStat, status, note, actor_id, action, old):
    now = iso(utcnow())
    base = canon(ms.base_vote) if ms.played else None
    db.execute(
        """INSERT INTO match_stats (contest_id, player_id, match_no, played, base_vote, bonus_count, malus_count, status, note,
                                    decided_by, decided_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (contest_id, player_id, match_no) DO UPDATE SET played = excluded.played, base_vote = excluded.base_vote,
               bonus_count = excluded.bonus_count, malus_count = excluded.malus_count, status = excluded.status,
               note = excluded.note, decided_by = excluded.decided_by, decided_at = excluded.decided_at""",
        (contest_id, pid, m, int(ms.played), base, ms.bonus, ms.malus, status, note, actor_id, now))
    new = {"played": ms.played, "base_vote": base, "bonus": ms.bonus, "malus": ms.malus, "status": status}
    if action:
        db.execute("INSERT INTO stat_changes (contest_id, player_id, match_no, user_id, action, reason, old_json, new_json, created_at) "
                   "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   (contest_id, pid, m, actor_id, action, note, json.dumps(old) if old else None, json.dumps(new), now))
    _bump(db, contest_id)


def _reconcile_cell(db, contest_id: int, pid: int, m: int) -> str:
    """Se due o piu' compilazioni indipendenti CONCORDANO il dato diventa 'confirmed'; se non concordano resta un conflitto.
    Le decisioni manuali (single/resolved/corrected) non vengono mai sovrascritte. Restituisce lo stato della cella."""
    entries = [dict(r) for r in db.execute(
        "SELECT * FROM vote_entries WHERE contest_id = ? AND player_id = ? AND match_no = ?", (contest_id, pid, m))]
    stat = db.execute("SELECT * FROM match_stats WHERE contest_id = ? AND player_id = ? AND match_no = ?",
                      (contest_id, pid, m)).fetchone()
    if stat is not None and stat["status"] != "confirmed":
        return cell_state(entries, dict(stat))
    sigs = {_entry_sig(e) for e in entries}
    if len(entries) >= 2 and len(sigs) == 1:
        e = entries[0]
        ms = MatchStat(m, bool(e["played"]), Decimal(e["base_vote"]) if e["base_vote"] is not None else None,
                       e["bonus_count"], e["malus_count"])
        if stat is None or _sig(stat["played"], stat["base_vote"], stat["bonus_count"], stat["malus_count"]) != next(iter(sigs)):
            _write_stat(db, contest_id, pid, m, ms, "confirmed", None, None, None, None)
        return "verified"
    if stat is not None:      # le compilazioni non concordano piu': il dato automatico decade
        db.execute("DELETE FROM match_stats WHERE id = ?", (stat["id"],))
        _bump(db, contest_id)
    return cell_state(entries, None)


def save_sheet(db, contest_id: int, user_id: int, cells: dict[tuple[int, int], MatchStat | None]) -> dict:
    """Salva la compilazione di UNA persona. None = cella lasciata vuota (cancella la sua eventuale compilazione precedente)."""
    now = iso(utcnow())
    with transaction(db):
        row = _contest(db, contest_id)
        if row["status"] != "calculating":
            raise ResultsError("L'immissione dei voti è aperta solo mentre il concorso è «in calcolo».")
        valid = {p["player_id"] for p in needed_players(db, contest_id)}
        saved = cleared = 0
        touched = []
        for (pid, m), ms in cells.items():
            if pid not in valid or not (1 <= m <= row["matches_count"]):
                raise ResultsError("Il foglio contiene un giocatore o una partita che non fanno parte di questo concorso.")
            touched.append((pid, m))
            if ms is None:
                cleared += db.execute("DELETE FROM vote_entries WHERE contest_id = ? AND player_id = ? AND match_no = ? AND entered_by = ?",
                                      (contest_id, pid, m, user_id)).rowcount
                continue
            db.execute(
                """INSERT INTO vote_entries (contest_id, player_id, match_no, entered_by, played, base_vote, bonus_count, malus_count,
                                             created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (contest_id, player_id, match_no, entered_by) DO UPDATE SET played = excluded.played,
                       base_vote = excluded.base_vote, bonus_count = excluded.bonus_count, malus_count = excluded.malus_count,
                       updated_at = excluded.updated_at""",
                (contest_id, pid, m, user_id, int(ms.played), canon(ms.base_vote) if ms.played else None, ms.bonus, ms.malus, now, now))
            saved += 1
        states = [_reconcile_cell(db, contest_id, pid, m) for pid, m in touched]
        summary = {"saved": saved, "cleared": cleared, "verified": states.count("verified"), "conflict": states.count("conflict"),
                   "pending": states.count("pending")}
        audit.log(db, user_id, "results.sheet", "contest", contest_id, summary)
        return summary


def my_entries(db, contest_id: int, user_id: int) -> dict[tuple[int, int], dict]:
    return {(r["player_id"], r["match_no"]): dict(r) for r in db.execute(
        "SELECT * FROM vote_entries WHERE contest_id = ? AND entered_by = ?", (contest_id, user_id))}


# ------------------------------------------------------------ decisioni manuali


def _note(note, minimum):
    note = " ".join((note or "").split())
    if len(note) < minimum or len(note) > 300:
        raise ResultsError(f"Indica il motivo (da {minimum} a 300 caratteri).")
    return note


def decide_cell(db, contest_id: int, pid: int, m: int, *, action: str, actor_id, note: str | None = None,
                entry_id: int | None = None, stat: MatchStat | None = None, allow_single: bool = True) -> None:
    """Decisioni dello staff su una cella: accept_single | take_entry | custom."""
    with transaction(db):
        row = _contest(db, contest_id)
        if row["status"] not in ("calculating", "published"):
            raise ResultsError("Il concorso non è in fase di risultati.")
        if pid not in {p["player_id"] for p in needed_players(db, contest_id)} or not (1 <= m <= row["matches_count"]):
            raise ResultsError("Giocatore o partita non validi per questo concorso.")
        published = row["status"] == "published"
        if published and action != "custom":
            raise ResultsError("A concorso pubblicato si può solo correggere un dato, indicando il motivo.")
        entries = [dict(r) for r in db.execute(
            "SELECT * FROM vote_entries WHERE contest_id = ? AND player_id = ? AND match_no = ?", (contest_id, pid, m))]
        old_row = db.execute("SELECT * FROM match_stats WHERE contest_id = ? AND player_id = ? AND match_no = ?",
                             (contest_id, pid, m)).fetchone()
        old = ({**stat_values(dict(old_row)), "status": old_row["status"]}) if old_row else None
        if action == "accept_single":
            if not allow_single:
                raise ResultsError("La convalida di una sola compilazione è disattivata: serve una seconda compilazione.")
            if old_row is not None:
                raise ResultsError("Questo dato è già verificato.")
            if len(entries) != 1:
                raise ResultsError("La convalida singola richiede esattamente una compilazione.")
            e = entries[0]
            ms = MatchStat(m, bool(e["played"]), Decimal(e["base_vote"]) if e["base_vote"] else None, e["bonus_count"], e["malus_count"])
            _write_stat(db, contest_id, pid, m, ms, "single", "convalidata una sola compilazione", actor_id, "accept_single", old)
        elif action == "take_entry":
            e = next((x for x in entries if x["id"] == entry_id), None)
            if e is None:
                raise ResultsError("Compilazione inesistente per questa cella.")
            ms = MatchStat(m, bool(e["played"]), Decimal(e["base_vote"]) if e["base_vote"] else None, e["bonus_count"], e["malus_count"])
            _write_stat(db, contest_id, pid, m, ms, "resolved", _note(note, 3), actor_id, "take_entry", old)
        elif action == "custom":
            if stat is None:
                raise ResultsError("Indica i valori da registrare.")
            reason = _note(note, 5 if published else 3)
            _write_stat(db, contest_id, pid, m, stat, "corrected" if published else "resolved", reason, actor_id, "custom", old)
        else:
            raise ResultsError("Azione non riconosciuta.")
        audit.log(db, actor_id, f"results.cell.{action}", "contest", contest_id,
                  {"player_id": pid, "match_no": m, "published": published})


def accept_all_single(db, contest_id: int, actor_id, allow_single: bool = True) -> int:
    if not allow_single:
        raise ResultsError("La convalida di una sola compilazione è disattivata: serve una seconda compilazione.")
    with transaction(db):
        row = _contest(db, contest_id)
        if row["status"] != "calculating":
            raise ResultsError("La convalida collettiva è disponibile solo mentre il concorso è «in calcolo».")
        entries, stats = _entries(db, contest_id), _stats(db, contest_id)
        done = 0
        for (pid, m), es in entries.items():
            if len(es) == 1 and (pid, m) not in stats:
                e = es[0]
                ms = MatchStat(m, bool(e["played"]), Decimal(e["base_vote"]) if e["base_vote"] else None, e["bonus_count"], e["malus_count"])
                _write_stat(db, contest_id, pid, m, ms, "single", "convalidata una sola compilazione (collettiva)", actor_id,
                            "accept_single_bulk", None)
                done += 1
        audit.log(db, actor_id, "results.accept_all_single", "contest", contest_id, {"cells": done})
        return done


def set_override(db, contest_id: int, pid: int, vote_text: str, reason: str, actor_id) -> None:
    """Voto finale inserito a mano: si dichiara come tale e prevale sul calcolo."""
    try:
        vote = to_decimal(vote_text)
    except ScoringError as exc:
        raise ResultsError(str(exc)) from exc
    if vote < 0 or vote > LEGAL_FINAL_MAX or vote.as_tuple().exponent < -3:
        raise ResultsError(f"Il voto finale deve essere tra 0 e {LEGAL_FINAL_MAX} con al massimo 3 decimali.")
    with transaction(db):
        row = _contest(db, contest_id)
        if row["status"] not in ("calculating", "published"):
            raise ResultsError("Il concorso non è in fase di risultati.")
        if pid not in {p["player_id"] for p in needed_players(db, contest_id)}:
            raise ResultsError("Giocatore non valido per questo concorso.")
        reason = _note(reason, 5)
        old = db.execute("SELECT vote FROM player_overrides WHERE contest_id = ? AND player_id = ?", (contest_id, pid)).fetchone()
        db.execute("INSERT INTO player_overrides (contest_id, player_id, vote, reason, user_id, created_at) VALUES (?, ?, ?, ?, ?, ?) "
                   "ON CONFLICT (contest_id, player_id) DO UPDATE SET vote = excluded.vote, reason = excluded.reason, "
                   "user_id = excluded.user_id, created_at = excluded.created_at",
                   (contest_id, pid, canon(vote), reason, actor_id, iso(utcnow())))
        _bump(db, contest_id)
        audit.log(db, actor_id, "results.override.set", "contest", contest_id,
                  {"player_id": pid, "vote": canon(vote), "from": old["vote"] if old else None, "reason": reason})


def clear_override(db, contest_id: int, pid: int, actor_id, reason: str | None = None) -> None:
    with transaction(db):
        row = _contest(db, contest_id)
        if row["status"] not in ("calculating", "published"):
            raise ResultsError("Il concorso non è in fase di risultati.")
        if row["status"] == "published":
            reason = _note(reason, 5)
        old = db.execute("SELECT vote FROM player_overrides WHERE contest_id = ? AND player_id = ?", (contest_id, pid)).fetchone()
        if old is None:
            raise ResultsError("Non c'è nessun voto manuale da togliere.")
        db.execute("DELETE FROM player_overrides WHERE contest_id = ? AND player_id = ?", (contest_id, pid))
        _bump(db, contest_id)
        audit.log(db, actor_id, "results.override.clear", "contest", contest_id,
                  {"player_id": pid, "vote": old["vote"], "reason": reason})


# ---------------------------------------------------------------------- calcolo


@dataclass
class CalcResult:
    players: list[dict] = field(default_factory=list)       # con voto
    incomplete: list[dict] = field(default_factory=list)    # senza dati completi
    lineups: list[dict] = field(default_factory=list)
    data_version: int = 0

    @property
    def complete(self) -> bool:
        return not self.incomplete and bool(self.lineups) and all(l["total"] is not None for l in self.lineups)


def compute(db, contest_id: int, base_cfg: ScoringConfig) -> CalcResult:
    """Calcola tutto IN MEMORIA, senza scrivere: serve sia al calcolo vero sia all'anteprima degli effetti di una correzione."""
    row = _contest(db, contest_id)
    cfg = config_for(row, base_cfg)
    weights = get_weights(db, row["season_id"])
    stats, overrides = _stats(db, contest_id), _overrides(db, contest_id)
    result = CalcResult(data_version=row["data_version"])
    votes: dict[int, Fraction] = {}
    for p in needed_players(db, contest_id):
        pid = p["player_id"]
        base = {"player_id": pid, "name": p["name"], "ruolo": p["ruolo"], "serie": p["serie"], "team": p["team"],
                "roles_seen": sorted(p["roles"])}
        if pid in overrides:
            ov = overrides[pid]
            vote = quantize(Fraction(ov["vote"]), cfg.decimals)
            breakdown = {"source": "manual", "vote": fstr(vote), "reason": ov["reason"], "by": ov["user_name"],
                         "lines": [f"Voto finale inserito a mano dallo staff: {fmt(vote)} (motivo: {ov['reason']})"]}
            result.players.append({**base, "vote": vote, "source": "manual", "breakdown": breakdown})
            votes[pid] = vote
            continue
        missing = [m for m in range(1, cfg.matches + 1) if (pid, m) not in stats]
        if missing:
            result.incomplete.append({**base, "missing": missing})
            continue
        ms = [MatchStat(m, bool(stats[(pid, m)]["played"]),
                        Decimal(stats[(pid, m)]["base_vote"]) if stats[(pid, m)]["base_vote"] is not None else None,
                        stats[(pid, m)]["bonus_count"], stats[(pid, m)]["malus_count"]) for m in range(1, cfg.matches + 1)]
        pv: PlayerVote = compute_player_vote(p["ruolo"], ms, weights, cfg)
        w = weights[p["ruolo"]]
        breakdown = {
            "source": "computed", "formula": "excel-evidence-v1", "matches_count": cfg.matches,
            "not_played_vote": canon(cfg.not_played_vote), "missing_match_vote": canon(cfg.missing_match_vote), "decimals": cfg.decimals,
            "weights": {"bonus_label": w.bonus_label, "bonus_weight": canon(w.bonus_weight),
                        "malus_label": w.malus_label, "malus_weight": canon(w.malus_weight)},
            "matches": [{"no": b.match_no, "played": b.played, "base": fstr(b.base_vote), "bonus": b.bonus, "malus": b.malus,
                         "match_vote": fstr(b.match_vote)} for b in pv.matches],
            "played_none": pv.played_none, "vote": fstr(pv.vote), "lines": explain(p["ruolo"], pv, weights, cfg),
            "statuses": {str(m): stats[(pid, m)]["status"] for m in range(1, cfg.matches + 1)},
        }
        result.players.append({**base, "vote": pv.vote, "source": "computed", "breakdown": breakdown})
        votes[pid] = pv.vote

    for l in db.execute(
            """SELECT l.id AS lineup_id, l.user_id, u.display_name, lv.id AS version_id, lv.version
                 FROM lineups l JOIN users u ON u.id = l.user_id
                 JOIN lineup_versions lv ON lv.lineup_id = l.id
                  AND lv.version = (SELECT MAX(version) FROM lineup_versions WHERE lineup_id = l.id)
                WHERE l.contest_id = ? ORDER BY u.display_name COLLATE NOCASE""", (contest_id,)).fetchall():
        pl = db.execute("SELECT player_id, full_name, ruolo FROM lineup_players WHERE version_id = ? ORDER BY position",
                        (l["version_id"],)).fetchall()
        lacking = [p["full_name"] for p in pl if p["player_id"] not in votes]
        total = None if lacking else lineup_total([votes[p["player_id"]] for p in pl], len(pl))
        result.lineups.append({"lineup_id": l["lineup_id"], "version_id": l["version_id"], "user_id": l["user_id"],
                               "display_name": l["display_name"], "version": l["version"], "total": total, "lacking": lacking,
                               "player_ids": [p["player_id"] for p in pl]})
    return result


def stored_scores(db, contest_id: int) -> dict:
    return {
        "players": {r["player_id"]: dict(r) for r in db.execute("SELECT * FROM player_scores WHERE contest_id = ?", (contest_id,))},
        "lineups": {r["lineup_id"]: dict(r) for r in db.execute("SELECT * FROM lineup_scores WHERE contest_id = ?", (contest_id,))},
    }


def diff(db, contest_id: int, result: CalcResult) -> dict:
    """Che cosa cambierebbe rispetto ai punteggi attualmente salvati (per verificare gli effetti di una correzione)."""
    old = stored_scores(db, contest_id)
    names = {p["player_id"]: p["name"] for p in result.players + result.incomplete}
    player_changes = []
    for p in result.players:
        before = old["players"].get(p["player_id"])
        b = before["vote"] if before else None
        if b is None or Fraction(b) != p["vote"]:
            player_changes.append({"name": p["name"], "old": b, "new": fstr(p["vote"])})
    for pid, before in old["players"].items():
        if pid not in {p["player_id"] for p in result.players}:
            player_changes.append({"name": names.get(pid, f"#{pid}"), "old": before["vote"], "new": None})
    lineup_changes = []
    for l in result.lineups:
        before = old["lineups"].get(l["lineup_id"])
        b = before["total"] if before else None
        n = fstr(l["total"]) if l["total"] is not None else None
        if b != n and not (b is not None and n is not None and Fraction(b) == Fraction(n)):
            lineup_changes.append({"name": l["display_name"], "old": b, "new": n})
    return {"players": player_changes, "lineups": lineup_changes}


def _persist(db, contest_id: int, result: CalcResult, actor_id, kind: str, extra: dict | None = None) -> None:
    db.execute("DELETE FROM player_scores WHERE contest_id = ?", (contest_id,))
    db.execute("DELETE FROM lineup_scores WHERE contest_id = ?", (contest_id,))
    for p in result.players:
        db.execute("INSERT INTO player_scores (contest_id, player_id, ruolo, vote, source, breakdown_json) VALUES (?, ?, ?, ?, ?, ?)",
                   (contest_id, p["player_id"], p["ruolo"], fstr(p["vote"]), p["source"], json.dumps(p["breakdown"], ensure_ascii=False)))
    for l in result.lineups:
        db.execute("INSERT INTO lineup_scores (contest_id, lineup_id, version_id, total) VALUES (?, ?, ?, ?)",
                   (contest_id, l["lineup_id"], l["version_id"], fstr(l["total"]) if l["total"] is not None else None))
    db.execute("UPDATE contests SET calculated_version = ?, updated_at = ? WHERE id = ?", (result.data_version, iso(utcnow()), contest_id))
    summary = {"players_scored": len(result.players), "players_incomplete": len(result.incomplete),
               "lineups_complete": sum(1 for l in result.lineups if l["total"] is not None), "lineups": len(result.lineups), **(extra or {})}
    db.execute("INSERT INTO score_runs (contest_id, kind, user_id, data_version, summary_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
               (contest_id, kind, actor_id, result.data_version, json.dumps(summary, ensure_ascii=False), iso(utcnow())))
    audit.log(db, actor_id, f"results.{kind}", "contest", contest_id, summary)


def calculate(db, contest_id: int, actor_id, base_cfg: ScoringConfig = ScoringConfig()) -> CalcResult:
    """Calcola e salva i punteggi in bozza (non ancora visibili ai partecipanti)."""
    with transaction(db):
        row = _contest(db, contest_id)
        if row["status"] != "calculating":
            raise ResultsError("Il calcolo si esegue mentre il concorso è «in calcolo».")
        result = compute(db, contest_id, base_cfg)
        _persist(db, contest_id, result, actor_id, "calculate")
        return result


def is_stale(row) -> bool:
    return row["calculated_version"] is None or row["calculated_version"] != row["data_version"]


def publish(db, contest_id: int, actor_id, base_cfg: ScoringConfig = ScoringConfig()) -> None:
    with transaction(db):
        row = _contest(db, contest_id)
        if row["status"] != "calculating":
            raise ResultsError("Si può pubblicare solo un concorso «in calcolo».")
        if is_stale(row):
            raise ResultsError("I punteggi non sono aggiornati: esegui «Calcola» prima di pubblicare.")
        result = compute(db, contest_id, base_cfg)   # ricontrollo: non ci si fida di quanto salvato
        if not result.complete:
            raise ResultsError("Mancano dati: tutti i giocatori delle formazioni devono avere un voto prima della pubblicazione.")
        now = iso(utcnow())
        db.execute("UPDATE contests SET status = 'published', published_at = ?, updated_at = ? WHERE id = ?", (now, now, contest_id))
        db.execute("INSERT INTO score_runs (contest_id, kind, user_id, data_version, summary_json, created_at) VALUES (?, 'publish', ?, ?, ?, ?)",
                   (contest_id, actor_id, row["data_version"], json.dumps({"lineups": len(result.lineups)}), now))
        audit.log(db, actor_id, "results.publish", "contest", contest_id, {"lineups": len(result.lineups)})


def _accept_pending_singles(db, contest_id: int, actor_id) -> int:
    """Convalida le celle compilate da UNA sola persona e non ancora verificate (stessa logica di accept_all_single).
    Va chiamata dentro una transazione gia' aperta. Non tocca i conflitti ne' le celle gia' verificate."""
    entries, stats = _entries(db, contest_id), _stats(db, contest_id)
    done = 0
    for (pid, m), es in entries.items():
        if len(es) == 1 and (pid, m) not in stats:
            e = es[0]
            ms = MatchStat(m, bool(e["played"]), Decimal(e["base_vote"]) if e["base_vote"] else None, e["bonus_count"], e["malus_count"])
            _write_stat(db, contest_id, pid, m, ms, "single", "convalidata una sola compilazione (alla conferma)", actor_id,
                        "accept_single_bulk", None)
            done += 1
    return done


def confirm_results(db, contest_id: int, actor_id, base_cfg: ScoringConfig = ScoringConfig(),
                    allow_single: bool = True) -> CalcResult:
    """Calcola e pubblica in un'unica transazione, solo se tutti i punteggi sono completi.

    BEGIN IMMEDIATE serializza le conferme concorrenti; il controllo dello stato rende
    l'operazione idempotente rispetto a doppi click/richieste ripetute. In caso di errore
    la transazione annulla punteggi, storico e cambio di stato, preservando i voti salvati.

    Prima del calcolo, se allow_single e' attivo, le celle compilate da una sola persona vengono convalidate
    (premere «Conferma» equivale a convalidarle): senza questo passo il calcolo vedeva solo i dati gia' verificati
    e rifiutava la conferma anche con tutti i voti inseriti. I conflitti tra compilazioni restano bloccanti.
    """
    with transaction(db):
        row = _contest(db, contest_id)
        if row["status"] != "calculating":
            raise ResultsError("La giornata non è più in fase di calcolo: potrebbe essere già stata confermata.")
        if allow_single:
            n_single = _accept_pending_singles(db, contest_id, actor_id)
            if n_single:
                audit.log(db, actor_id, "results.accept_all_single", "contest", contest_id, {"cells": n_single, "on_confirm": True})
        result = compute(db, contest_id, base_cfg)
        if not result.lineups:
            raise ResultsError("Non ci sono formazioni da calcolare.")
        if not result.complete:
            missing_players = len(result.incomplete)
            missing_lineups = sum(1 for lineup in result.lineups if lineup["total"] is None)
            raise ResultsError(
                f"Conferma non possibile: mancano dati verificati per {missing_players} giocatori "
                f"e {missing_lineups} formazioni. I voti già salvati sono conservati; completa o correggi i dati."
            )
        _persist(db, contest_id, result, actor_id, "calculate")
        now = iso(utcnow())
        db.execute("UPDATE contests SET status = 'published', published_at = ?, updated_at = ? WHERE id = ? AND status = 'calculating'",
                   (now, now, contest_id))
        if db.execute("SELECT changes() AS n").fetchone()["n"] != 1:
            raise ResultsError("Lo stato della giornata è cambiato durante la conferma. Ricarica la pagina.")
        db.execute("INSERT INTO score_runs (contest_id, kind, user_id, data_version, summary_json, created_at) VALUES (?, 'publish', ?, ?, ?, ?)",
                   (contest_id, actor_id, result.data_version, json.dumps({"lineups": len(result.lineups), "confirmed_atomically": True}), now))
        audit.log(db, actor_id, "results.publish", "contest", contest_id, {"lineups": len(result.lineups), "confirmed_atomically": True})
        return result


def preview_recalculation(db, contest_id: int, base_cfg: ScoringConfig = ScoringConfig()) -> dict:
    row = _contest(db, contest_id)
    result = compute(db, contest_id, base_cfg)
    return {"stale": is_stale(row), "result": result, "diff": diff(db, contest_id, result)}


def apply_recalculation(db, contest_id: int, actor_id, base_cfg: ScoringConfig = ScoringConfig()) -> dict:
    """Dopo una correzione: conferma esplicita del ricalcolo, con registrazione degli effetti."""
    with transaction(db):
        row = _contest(db, contest_id)
        if row["status"] != "published":
            raise ResultsError("Il ricalcolo guidato riguarda i concorsi pubblicati.")
        if not is_stale(row):
            raise ResultsError("Nessuna modifica da ricalcolare.")
        result = compute(db, contest_id, base_cfg)
        if not result.complete:
            raise ResultsError("Dopo la correzione mancano dati: il ricalcolo non può essere applicato.")
        effects = diff(db, contest_id, result)
        _persist(db, contest_id, result, actor_id, "recalculate", {"effects": effects})
        return effects


# ----------------------------------------------------------------- lettura risultati


def contest_scores(db, contest_id: int) -> dict:
    """Punteggi salvati, per le pagine staff (ordine provvisorio: totale decrescente, SENZA spareggi: Q7 e' aperta)."""
    lineups = [dict(r) for r in db.execute(
        """SELECT ls.lineup_id, ls.total, lv.version, u.display_name, u.id AS user_id FROM lineup_scores ls
             JOIN lineup_versions lv ON lv.id = ls.version_id JOIN lineups l ON l.id = ls.lineup_id
             JOIN users u ON u.id = l.user_id WHERE ls.contest_id = ?""", (contest_id,))]
    lineups.sort(key=lambda l: (l["total"] is None, -Fraction(l["total"]) if l["total"] else 0, l["display_name"].casefold()))
    players = []
    for r in db.execute("SELECT ps.*, p.full_name FROM player_scores ps JOIN players p ON p.id = ps.player_id WHERE ps.contest_id = ? "
                        "ORDER BY ps.ruolo, p.search_name", (contest_id,)):
        d = dict(r)
        d["breakdown"] = json.loads(d.pop("breakdown_json"))
        players.append(d)
    return {"lineups": lineups, "players": players}


def user_result(db, contest_id: int, user_id: int) -> dict | None:
    """Il punteggio del partecipante con il dettaglio di calcolo. Solo a concorso pubblicato."""
    row = db.execute("SELECT status FROM contests WHERE id = ?", (contest_id,)).fetchone()
    if row is None or row["status"] != "published":
        return None
    ls = db.execute("SELECT ls.*, lv.version FROM lineup_scores ls JOIN lineups l ON l.id = ls.lineup_id "
                    "JOIN lineup_versions lv ON lv.id = ls.version_id WHERE ls.contest_id = ? AND l.user_id = ?",
                    (contest_id, user_id)).fetchone()
    if ls is None or ls["total"] is None:
        return None
    players = []
    for lp in db.execute("SELECT * FROM lineup_players WHERE version_id = ? ORDER BY position", (ls["version_id"],)):
        ps = db.execute("SELECT * FROM player_scores WHERE contest_id = ? AND player_id = ?", (contest_id, lp["player_id"])).fetchone()
        players.append({"name": lp["full_name"], "ruolo": lp["ruolo"], "serie": lp["serie"], "team": lp["team_name"],
                        "vote": ps["vote"] if ps else None, "source": ps["source"] if ps else None,
                        "lines": json.loads(ps["breakdown_json"])["lines"] if ps else []})
    return {"total": ls["total"], "version": ls["version"], "players": players}


def published_totals(db, user_id: int, contest_ids: list[int]) -> dict[int, str]:
    if not contest_ids:
        return {}
    marks = ",".join("?" * len(contest_ids))
    return {r["contest_id"]: r["total"] for r in db.execute(
        f"SELECT ls.contest_id, ls.total FROM lineup_scores ls JOIN lineups l ON l.id = ls.lineup_id "
        f"JOIN contests c ON c.id = ls.contest_id WHERE l.user_id = ? AND c.status = 'published' AND ls.total IS NOT NULL "
        f"AND ls.contest_id IN ({marks})", [user_id, *contest_ids])}


def recent_runs(db, contest_id: int, limit: int = 20):
    return db.execute("SELECT r.*, u.display_name AS user_name FROM score_runs r LEFT JOIN users u ON u.id = r.user_id "
                      "WHERE r.contest_id = ? ORDER BY r.id DESC LIMIT ?", (contest_id, limit)).fetchall()


def recent_changes(db, contest_id: int, limit: int = 30):
    return db.execute(
        "SELECT c.*, u.display_name AS user_name, p.full_name FROM stat_changes c JOIN players p ON p.id = c.player_id "
        "LEFT JOIN users u ON u.id = c.user_id WHERE c.contest_id = ? ORDER BY c.id DESC LIMIT ?", (contest_id, limit)).fetchall()
