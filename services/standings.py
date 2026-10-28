"""Classifiche e premi, sempre RICALCOLATI dai punteggi pubblicati (nessuna classifica salvata come dato sorgente).

Scelte dichiarate (vedi docs/DECISIONI.md):
- contano solo i concorsi PUBBLICATI;
- i partecipanti esclusi dalla stagione dallo staff non compaiono nelle classifiche e non hanno premi;
- il premio fedeltà considera ogni concorso GIA' SVOLTO (chiuso, in calcolo o pubblicato), non solo quelli pubblicati.
"""
from __future__ import annotations

from fractions import Fraction

from ..db import transaction
from ..domain.contest import effective_status
from ..domain.ranking import Entry, Rules, best_of_team, general_standings, loyalty_status, rank_contest, team_standings
from ..timeutil import iso, parse_iso, utcnow, utcnow_iso
from . import admission, audit, clubs, strikes
from .contests import view as contest_view


class AwardError(Exception):
    pass


def excluded_ids(db, season_id: int) -> set[int]:
    return set(admission.exclusions_map(db, season_id))


def published_contests(db, season_id: int) -> list[dict]:
    return [dict(r) for r in db.execute("SELECT * FROM contests WHERE season_id = ? AND status = 'published' ORDER BY number", (season_id,))]


def _entries(db, contest_id: int, excluded: set[int]):
    entries, meta = [], {}
    for r in db.execute(
            """SELECT ls.lineup_id, ls.total, ls.version_id, l.user_id, u.display_name, lv.fanta_team_name, lv.submitted_at AS counted_at,
                      (SELECT MIN(submitted_at) FROM lineup_versions WHERE lineup_id = l.id) AS first_at
                 FROM lineup_scores ls JOIN lineups l ON l.id = ls.lineup_id JOIN users u ON u.id = l.user_id
                 JOIN lineup_versions lv ON lv.id = ls.version_id WHERE ls.contest_id = ? AND ls.total IS NOT NULL""", (contest_id,)):
        if r["user_id"] in excluded:
            continue
        entries.append(Entry(r["user_id"], Fraction(r["total"]), r["counted_at"], r["first_at"], r["version_id"]))
        meta[r["user_id"]] = {"name": r["display_name"], "fanta_team": r["fanta_team_name"], "total": r["total"], "lineup_id": r["lineup_id"]}
    return entries, meta


def contest_ranking(db, contest_id: int, rules: Rules, excluded: set[int] | None = None) -> list[dict]:
    row = db.execute("SELECT season_id FROM contests WHERE id = ?", (contest_id,)).fetchone()
    excluded = excluded if excluded is not None else excluded_ids(db, row["season_id"])
    entries, meta = _entries(db, contest_id, excluded)
    return [{"position": r.position, "user_id": r.entry.user_id, "prize": r.prize, "tied_total": r.tied_total,
             "counted_at": r.entry.counted_at, "ranked": r, **meta[r.entry.user_id]} for r in rank_contest(entries, rules)]


def season_rankings(db, season_id: int, rules: Rules) -> list[dict]:
    excl = excluded_ids(db, season_id)
    return [{"contest": c, "rows": contest_ranking(db, c["id"], rules, excl)} for c in published_contests(db, season_id)]


def _ranked_map(rankings: list[dict]) -> dict:
    return {r["contest"]["id"]: [row["ranked"] for row in r["rows"]] for r in rankings}


def _names(db, ids) -> dict[int, str]:
    ids = list(ids)
    if not ids:
        return {}
    return {r["id"]: r["display_name"] for r in db.execute(f"SELECT id, display_name FROM users WHERE id IN ({','.join('?' * len(ids))})", ids)}


def loyalty(db, season_id: int, rules: Rules) -> list[dict]:
    now = utcnow()
    contests = [c for c in (contest_view(r, now) for r in db.execute("SELECT * FROM contests WHERE season_id = ? AND status != 'draft' ORDER BY number", (season_id,)))
                if c["effective"] in ("closed", "calculating", "published")]
    excl = excluded_ids(db, season_id)
    played: dict[int, set[int]] = {}
    for r in db.execute("SELECT l.user_id, l.contest_id FROM lineups l JOIN contests c ON c.id = l.contest_id WHERE c.season_id = ?", (season_id,)):
        played.setdefault(r["user_id"], set()).add(r["contest_id"])
    participants = [u for u in played if u not in excl]
    st = {l.user_id: l for l in loyalty_status([c["id"] for c in contests], played, strikes.counts(db, season_id), participants, rules.max_strikes)}
    names = _names(db, participants)
    cname = {c["id"]: c["name"] for c in contests}
    out = [{"user_id": u, "name": names[u], "eligible": st[u].eligible, "strikes": st[u].strikes, "reason": st[u].reason,
            "missed": [cname[c] for c in st[u].missed]} for u in participants]
    return sorted(out, key=lambda x: (not x["eligible"], x["name"].casefold()))


def general(db, season_id: int, rules: Rules) -> dict:
    rankings = season_rankings(db, season_id, rules)
    standings = general_standings(_ranked_map(rankings), rules)
    teams, strike_n = clubs.team_map(db, season_id), strikes.counts(db, season_id)
    names = _names(db, [s.key for s in standings])
    loy = {l["user_id"]: l for l in loyalty(db, season_id, rules)}
    rows = [{"rank": s.rank, "tied": s.tied, "user_id": s.key, "name": names[s.key], "points": s.points, "positions": s.positions,
             "prize": s.prize, "prize_pending": s.prize_pending, "club": teams.get(s.key), "strikes": strike_n.get(s.key, 0),
             "loyalty": loy.get(s.key)} for s in standings]
    return {"rankings": rankings, "rows": rows, "contests": [r["contest"] for r in rankings]}


def teams(db, season_id: int, rules: Rules) -> dict:
    rankings = season_rankings(db, season_id, rules)
    team_of = clubs.team_map(db, season_id)
    standings, _ = team_standings(_ranked_map(rankings), team_of, rules)
    best = {r["contest"]["id"]: best_of_team([row["ranked"] for row in r["rows"]], team_of) for r in rankings}
    names = _names(db, {rk.entry.user_id for per in best.values() for rk in per.values()})
    rows = [{"rank": s.rank, "tied": s.tied, "team": s.key, "points": s.points, "positions": s.positions,
             "best": {cid: names[best[cid][s.key].entry.user_id] for cid in s.positions}} for s in standings]
    played = {u for r in rankings for row in r["rows"] for u in [row["user_id"]]}
    return {"rows": rows, "contests": [r["contest"] for r in rankings], "confirmed": rules.team_scale_confirmed,
            "without_team": sorted(_names(db, [u for u in played if u not in team_of]).values(), key=str.casefold)}


def user_summary(db, season_id: int, user_id: int, rules: Rules) -> dict:
    g = general(db, season_id, rules)
    row = next((r for r in g["rows"] if r["user_id"] == user_id), None)
    last = None
    for r in reversed(g["rankings"]):
        mine = next((x for x in r["rows"] if x["user_id"] == user_id), None)
        if mine:
            last = {"contest": r["contest"], "position": mine["position"], "total": mine["total"], "prize": mine["prize"]}
            break
    mine_loy = next((l for l in loyalty(db, season_id, rules) if l["user_id"] == user_id), None)
    return {"general": row, "last": last, "strikes": strikes.counts(db, season_id).get(user_id, 0), "loyalty": mine_loy,
            "club": clubs.team_map(db, season_id).get(user_id), "max_strikes": rules.max_strikes, "participants": len(g["rows"])}


# ------------------------------------------------------------------------- premi


def derived_awards(db, season_id: int, rules: Rules) -> list[dict]:
    """Tutti i premi che SPETTANO ora: settimanali (per concorso pubblicato), finali della generale e fedeltà."""
    season = db.execute("SELECT status FROM seasons WHERE id = ?", (season_id,)).fetchone()
    final = season is not None and season["status"] == "archived"
    out = []
    for r in season_rankings(db, season_id, rules):
        for row in r["rows"]:
            if row["prize"]:
                out.append({"kind": "weekly", "contest_id": r["contest"]["id"], "contest_name": r["contest"]["name"], "user_id": row["user_id"],
                            "name": row["name"], "position": row["position"], "amount": row["prize"], "final": True, "pending": False})
    g = general(db, season_id, rules)
    for row in g["rows"]:
        if row["prize_pending"] or row["prize"]:
            out.append({"kind": "general", "contest_id": 0, "contest_name": "Classifica generale", "user_id": row["user_id"], "name": row["name"],
                        "position": row["rank"], "amount": row["prize"], "final": final, "pending": row["prize_pending"]})
    if rules.loyalty_prize:
        for l in loyalty(db, season_id, rules):
            if l["eligible"]:
                out.append({"kind": "loyalty", "contest_id": 0, "contest_name": "Premio fedeltà", "user_id": l["user_id"], "name": l["name"],
                            "position": None, "amount": rules.loyalty_prize, "final": final, "pending": False})
    return out


def awards_overview(db, season_id: int, rules: Rules) -> list[dict]:
    """I premi che spettano, ciascuno con il suo stato di accredito; piu' gli accrediti che non sono piu' dovuti."""
    credited = {(r["kind"], r["contest_id"], r["user_id"]): dict(r) for r in db.execute("SELECT * FROM credited_awards WHERE season_id = ?", (season_id,))}
    items, seen = [], set()
    for a in derived_awards(db, season_id, rules):
        key = (a["kind"], a["contest_id"], a["user_id"])
        seen.add(key)
        c = credited.get(key)
        if a["pending"]:
            state = "pending"
        elif c is None:
            state = "to_credit" if a["final"] else "provisional"
        else:
            state = "credited" if c["amount"] == a["amount"] else "changed"
        items.append({**a, "credited": c, "state": state})
    names = _names(db, [k[2] for k in credited if k not in seen])
    for key, c in credited.items():
        if key not in seen:
            items.append({"kind": key[0], "contest_id": key[1], "contest_name": "—", "user_id": key[2], "name": names.get(key[2], f"#{key[2]}"),
                          "position": c["position"], "amount": 0, "final": True, "pending": False, "credited": c, "state": "not_due"})
    return sorted(items, key=lambda i: (i["kind"] != "weekly", i["contest_id"], i["position"] or 99, i["name"].casefold()))


def credit_award(db, season_id: int, kind: str, contest_id: int, user_id: int, rules: Rules, actor_id, note: str = "") -> None:
    """Segna un premio come accreditato. Si ricalcola ADESSO cio' che spetta: non ci si fida dei valori del modulo."""
    with transaction(db):
        match = next((a for a in derived_awards(db, season_id, rules)
                      if (a["kind"], a["contest_id"], a["user_id"]) == (kind, contest_id, user_id)), None)
        if match is None:
            raise AwardError("Questo premio non risulta dovuto.")
        if match["pending"]:
            raise AwardError("Pari merito su posizioni che pagano un premio: il regolamento non dice come dividerlo, decidi con lo staff.")
        if not match["final"]:
            raise AwardError("Questo premio è provvisorio: si accredita quando la stagione è conclusa (archiviata).")
        note = " ".join((note or "").split())[:200]
        db.execute("INSERT INTO credited_awards (season_id, contest_id, user_id, kind, position, amount, credited_by, credited_at, note) "
                   "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (season_id, contest_id, user_id, kind) DO UPDATE SET "
                   "position = excluded.position, amount = excluded.amount, credited_by = excluded.credited_by, "
                   "credited_at = excluded.credited_at, note = excluded.note",
                   (season_id, contest_id, user_id, kind, match["position"], match["amount"], actor_id, utcnow_iso(), note))
        audit.log(db, actor_id, "award.credit", "user", user_id, {"kind": kind, "contest_id": contest_id, "amount": match["amount"], "position": match["position"]})


def uncredit_award(db, award_id: int, actor_id) -> None:
    with transaction(db):
        row = db.execute("SELECT * FROM credited_awards WHERE id = ?", (award_id,)).fetchone()
        if row is None:
            raise AwardError("Accredito inesistente.")
        db.execute("DELETE FROM credited_awards WHERE id = ?", (award_id,))
        audit.log(db, actor_id, "award.uncredit", "user", row["user_id"], {"kind": row["kind"], "contest_id": row["contest_id"], "amount": row["amount"]})
