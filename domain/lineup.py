"""Validazione della formazione. Modulo puro: niente web, niente database.

Regole CONFERMATE dal regolamento: sei giocatori, tre di Serie A e tre di Serie B, tutti di
squadre diverse, tutti presenti nel listone ufficiale. Il vincolo "un giocatore per ruolo"
(esempio del regolamento) NON e' ancora confermato: e' un parametro (Rules.one_per_role).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .listone import ROLES

ROLE_ORDER = {r: i for i, r in enumerate(ROLES)}


@dataclass(frozen=True)
class Candidate:
    player_id: int
    psd_id: int
    name: str
    serie: str
    ruolo: str
    team: str
    selectable: bool = True


@dataclass(frozen=True)
class Rules:
    size: int = 6
    per_serie: int = 3
    distinct_teams: bool = True
    one_per_role: bool = False


@dataclass(frozen=True)
class Issue:
    code: str
    message: str
    player_ids: tuple[int, ...] = ()


@dataclass
class Validation:
    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def sort_key(c: Candidate):
    return (ROLE_ORDER.get(c.ruolo, 99), c.name.casefold(), c.player_id)


def _names(cands) -> str:
    return ", ".join(c.name for c in cands)


def validate(requested_ids: list[int], known: dict[int, Candidate], rules: Rules = Rules()) -> Validation:
    """requested_ids: gli ID scelti dall'utente (anche ripetuti/sconosciuti).
    known: i giocatori realmente presenti nel listone della stagione, per ID."""
    v = Validation()

    seen: set[int] = set()
    for pid in requested_ids:
        if pid in seen:
            name = known[pid].name if pid in known else "un giocatore"
            v.errors.append(Issue("duplicate", f"Hai scelto due volte {name}: ogni giocatore può comparire una sola volta.", (pid,)))
        seen.add(pid)
    unique = list(dict.fromkeys(requested_ids))

    missing = [pid for pid in unique if pid not in known]
    if missing:
        v.errors.append(Issue(
            "unknown_player",
            "Uno dei giocatori scelti non è presente nel listone di questa stagione. Ricarica la pagina e scegli di nuovo.",
            tuple(missing)))

    chosen = [known[pid] for pid in unique if pid in known]
    blocked = [c for c in chosen if not c.selectable]
    for c in blocked:
        v.errors.append(Issue("not_selectable", f"{c.name} non è al momento selezionabile: scegli un altro giocatore.", (c.player_id,)))

    n = len(unique)
    if n < rules.size:
        v.errors.append(Issue("count", f"Hai scelto {n} giocatori su {rules.size}: ne mancano {rules.size - n}."))
    elif n > rules.size:
        v.errors.append(Issue("count", f"Hai scelto {n} giocatori: ne servono esattamente {rules.size}. Togline {n - rules.size}."))

    by_serie = {s: [c for c in chosen if c.serie == s] for s in ("A", "B")}
    for s, group in by_serie.items():
        if len(group) > rules.per_serie:
            other = "B" if s == "A" else "A"
            v.errors.append(Issue(
                "series_split",
                f"Hai scelto {len(group)} giocatori di Serie {s}: ne servono {rules.per_serie} di Serie A e "
                f"{rules.per_serie} di Serie B. Sostituisci un giocatore di Serie {s} con uno di Serie {other}.",
                tuple(c.player_id for c in group)))

    if rules.distinct_teams:
        by_team: dict[str, list[Candidate]] = {}
        for c in chosen:
            by_team.setdefault(c.team.casefold(), []).append(c)
        for group in by_team.values():
            if len(group) > 1:
                v.errors.append(Issue(
                    "same_team",
                    f"{_names(group)} giocano nella stessa squadra ({group[0].team}): le squadre devono essere tutte diverse.",
                    tuple(c.player_id for c in group)))

    by_role: dict[str, list[Candidate]] = {}
    for c in chosen:
        by_role.setdefault(c.ruolo, []).append(c)
    repeated = {r: g for r, g in by_role.items() if len(g) > 1}
    for r, group in repeated.items():
        ids = tuple(c.player_id for c in group)
        if rules.one_per_role:
            v.errors.append(Issue(
                "role_duplicate",
                f"In questo concorso serve un giocatore per ruolo: hai scelto più giocatori con ruolo {r} ({_names(group)}).",
                ids))
        else:
            v.warnings.append(Issue(
                "roles_repeated",
                f"Attenzione: hai più giocatori con ruolo {r} ({_names(group)}). Nell'esempio del regolamento ogni ruolo "
                "compare una sola volta: chiedi allo staff se per questo concorso è richiesto.",
                ids))
    return v
