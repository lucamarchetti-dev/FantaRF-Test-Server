"""Classifiche, punti, premi, premio fedeltà. Modulo puro: niente web, niente database.

Confermato dal regolamento (PDF): premi settimanali 20.000/10.000/5.000; punti della generale 25-20-16-13-10-8-6-4-2-1 dal
1° al 10° posto settimanale; premi finali dei primi otto; premio fedeltà 50.000 con tre strike di esclusione.
NON definito dal regolamento (parametri, mai costanti nascoste): spareggio, scala della classifica a squadre, trattamento
dei pari merito nella generale. Le scelte di default sono dichiarate nei commenti e in docs/DECISIONI.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from fractions import Fraction

TIEBREAK_MODES = ("latest_version", "first_submission")
TIEBREAK_LABELS = {
    "latest_version": "ora dell'invio della formazione che conta (l'ultima versione): chi l'ha inviata prima passa avanti",
    "first_submission": "ora del primo invio valido: chi ha giocato per primo passa avanti",
}


@dataclass(frozen=True)
class Rules:
    weekly_prizes: tuple[int, ...] = (20000, 10000, 5000)
    points_scale: tuple[int, ...] = (25, 20, 16, 13, 10, 8, 6, 4, 2, 1)
    general_prizes: tuple[int, ...] = (200000, 150000, 100000, 70000, 60000, 50000, 40000, 30000)
    loyalty_prize: int = 50000
    max_strikes: int = 3
    # Classifica a squadre: la tabella manca nel PDF. Valore iniziale = quello che l'Excel sembra usare (stessa scala dei
    # punti individuali); resta PROVVISORIA finche' lo staff non la conferma.
    team_scale: tuple[int, ...] = (25, 20, 16, 13, 10, 8, 6, 4, 2, 1)
    team_scale_confirmed: bool = False
    tiebreak: str = "latest_version"


# --------------------------------------------------------------- classifica settimanale


@dataclass(frozen=True)
class Entry:
    user_id: int
    total: Fraction
    counted_at: str       # orario (ISO UTC) dell'invio della versione che conta
    first_at: str         # orario del primo invio valido della giocata
    version_id: int = 0


@dataclass(frozen=True)
class Ranked:
    entry: Entry
    position: int
    tied_total: bool      # c'e' almeno un altro con lo stesso punteggio: l'ordine e' deciso dallo spareggio
    prize: int


def _time_key(e: Entry, tiebreak: str) -> str:
    return e.first_at if tiebreak == "first_submission" else e.counted_at


def rank_contest(entries: list[Entry], rules: Rules) -> list[Ranked]:
    """Ordine: punteggio decrescente; a pari punteggio avanti chi ha inviato prima (criterio configurabile); come ultima
    risorsa, per identificativo della versione, cosi' l'ordine e' sempre deterministico. Posizioni 1..n tutte diverse."""
    if rules.tiebreak not in TIEBREAK_MODES:
        raise ValueError(f"Spareggio sconosciuto: {rules.tiebreak}")
    ordered = sorted(entries, key=lambda e: (-e.total, _time_key(e, rules.tiebreak), e.version_id, e.user_id))
    totals = [e.total for e in ordered]
    out = []
    for i, e in enumerate(ordered):
        tied = totals.count(e.total) > 1
        out.append(Ranked(e, i + 1, tied, rules.weekly_prizes[i] if i < len(rules.weekly_prizes) else 0))
    return out


def points_for(position: int, scale: tuple[int, ...]) -> int:
    return scale[position - 1] if 1 <= position <= len(scale) else 0


# ------------------------------------------------------------------ classifica generale


@dataclass
class Standing:
    key: object                       # user_id (generale) o nome squadra
    points: int = 0
    positions: dict[int, int] = field(default_factory=dict)     # id concorso -> posizione ottenuta
    rank: int = 0                     # posizione in classifica (a pari merito: la stessa)
    tied: bool = False
    prize: int | None = 0             # None = pari merito su posizioni che pagano un premio: decide lo staff
    prize_pending: bool = False


def _sort_and_rank(standings: list[Standing], depth: int) -> list[Standing]:
    """Ordina per punti e poi per 'countback' (quanti 1°, quanti 2°, ... posti), come nella Formula 1; stessi valori = pari merito."""
    def countback(s: Standing):
        counts = [0] * depth
        for pos in s.positions.values():
            if 1 <= pos <= depth:
                counts[pos - 1] += 1
        return tuple(-c for c in counts)

    keys = {id(s): (-s.points, countback(s)) for s in standings}
    ordered = sorted(standings, key=lambda s: (keys[id(s)], str(s.key)))
    i = 0
    while i < len(ordered):
        j = i
        while j + 1 < len(ordered) and keys[id(ordered[j + 1])] == keys[id(ordered[i])]:
            j += 1
        for k in range(i, j + 1):
            ordered[k].rank = i + 1
            ordered[k].tied = j > i
        i = j + 1
    return ordered


def general_standings(rankings: dict[int, list[Ranked]], rules: Rules) -> list[Standing]:
    """rankings: {id concorso: classifica settimanale}. Chi salta un concorso prende 0 punti per quel concorso, senza perdere il resto."""
    by_user: dict[int, Standing] = {}
    depth = max([len(r) for r in rankings.values()] + [len(rules.points_scale)])
    for cid, ranking in rankings.items():
        for r in ranking:
            s = by_user.setdefault(r.entry.user_id, Standing(r.entry.user_id))
            s.positions[cid] = r.position
            s.points += points_for(r.position, rules.points_scale)
    ordered = _sort_and_rank(list(by_user.values()), depth)
    return _assign_prizes(ordered, rules.general_prizes)


def _assign_prizes(ordered: list[Standing], prizes: tuple[int, ...]) -> list[Standing]:
    """Premio per posizione. Se piu' persone sono a pari merito su posizioni che pagano, il regolamento non dice come dividere:
    non si decide in automatico (prize=None, prize_pending=True)."""
    for s in ordered:
        group = [x for x in ordered if x.rank == s.rank]
        covered = range(s.rank, s.rank + len(group))
        pays = any(p <= len(prizes) for p in covered)
        if len(group) > 1 and pays:
            s.prize, s.prize_pending = None, True
        else:
            s.prize = prizes[s.rank - 1] if s.rank <= len(prizes) else 0
            s.prize_pending = False
    return ordered


# ---------------------------------------------------------------- classifica a squadre


def team_standings(rankings: dict[int, list[Ranked]], team_of: dict[int, str], rules: Rules) -> tuple[list[Standing], dict[int, str]]:
    """Ogni settimana porta punti solo il MIGLIORE di ogni squadra; le squadre si ordinano per la posizione del loro migliore
    e si applica la scala delle squadre. Restituisce anche, per ogni concorso, i partecipanti senza squadra (ignorati)."""
    by_team: dict[str, Standing] = {}
    depth = max([len(r) for r in rankings.values()] + [len(rules.team_scale)])
    for cid, ranking in rankings.items():
        seen: list[str] = []
        for r in ranking:                              # gia' in ordine di merito: il primo di ogni squadra e' il migliore
            team = team_of.get(r.entry.user_id)
            if team and team not in seen:
                seen.append(team)
        for pos, team in enumerate(seen, start=1):
            s = by_team.setdefault(team, Standing(team))
            s.positions[cid] = pos
            s.points += points_for(pos, rules.team_scale)
    ordered = _sort_and_rank(list(by_team.values()), depth)
    for s in ordered:
        s.prize, s.prize_pending = 0, False            # S13: nessun premio per la classifica a squadre
    return ordered, {}


def best_of_team(ranking: list[Ranked], team_of: dict[int, str]) -> dict[str, Ranked]:
    out: dict[str, Ranked] = {}
    for r in ranking:
        team = team_of.get(r.entry.user_id)
        if team and team not in out:
            out[team] = r
    return out


# ---------------------------------------------------------------------- premio fedeltà


@dataclass(frozen=True)
class Loyalty:
    user_id: int
    eligible: bool
    strikes: int
    missed: tuple[int, ...]
    reason: str           # '' se in regola; altrimenti perche' non e' (piu') idoneo


def loyalty_status(contest_ids: list[int], played: dict[int, set[int]], strikes: dict[int, int], participants: list[int],
                   max_strikes: int) -> list[Loyalty]:
    """Idoneo = una giocata valida in OGNI concorso svolto e meno di `max_strikes` strike.
    (L'assenza NON genera strike: toglie solo il premio fedeltà, come dice il regolamento.)"""
    out = []
    for uid in participants:
        missed = tuple(c for c in contest_ids if c not in played.get(uid, set()))
        n = strikes.get(uid, 0)
        if n >= max_strikes:
            reason = f"{n} strike (limite {max_strikes})"
        elif missed:
            reason = f"ha saltato {len(missed)} concors{'o' if len(missed) == 1 else 'i'}"
        else:
            reason = ""
        out.append(Loyalty(uid, not reason, n, missed, reason))
    return out


def with_rules(rules: Rules, **changes) -> Rules:
    return replace(rules, **changes)