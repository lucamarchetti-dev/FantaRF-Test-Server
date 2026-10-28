"""Motore di calcolo dei punteggi. Modulo puro: nessun web, nessun database, nessun float.

Cosa e' CONFERMATO (regolamento PDF e staff):
  - bonus/malus per ruolo con i pesi della tabella; voto 5,5 se il giocatore non gioca nessuna delle partite;
  - per una partita non giocata (SV) vale 5,5, anche quando le altre partite sono state giocate (confermato dallo staff).
Cosa e' un'EVIDENZA ricavata dall'Excel (foglio «Calcoli», verificata su 55 giocatori) ma NON confermata dallo staff:
  - voto di partita = voto base + bonus x peso - malus x peso;  voto finale = media delle partite;
  - punteggio formazione = somma dei sei voti;  nessun arrotondamento
    (con tre partite il voto e' una frazione esatta, es. 6,9666...: foglio «Calcoli a 3», concorsi 5 e 6).
L'aritmetica e' RAZIONALE ESATTA (Fraction): niente virgola mobile, e due punteggi uguali sono davvero uguali (spareggi).
Per questo ogni scelta non confermata e' un PARAMETRO (ScoringConfig / pesi per stagione), non una costante nascosta.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from fractions import Fraction

FORMULA_VERSION = "excel-evidence-v1"

# Limiti di buon senso per intercettare refusi di battitura (NON sono regole di gioco).
VOTE_MIN, VOTE_MAX = Decimal("0"), Decimal("10")
COUNT_MAX = 99
MAX_VOTE_DECIMALS = 3


class ScoringError(ValueError):
    """Dati incompleti o incoerenti: il motore non indovina mai."""


@dataclass(frozen=True)
class RoleWeights:
    bonus_label: str
    bonus_weight: Decimal
    malus_label: str
    malus_weight: Decimal


# Tabella del regolamento (S13). La mappatura P/D/T/C/W/A -> categoria e' l'evidenza Q2, da confermare.
DEFAULT_WEIGHTS: dict[str, RoleWeights] = {
    "P": RoleWeights("Tiro parato", Decimal("0.3"), "Gol subito", Decimal("0.5")),
    "D": RoleWeights("Palla recuperata", Decimal("0.25"), "Contrasto aereo perso", Decimal("0.25")),
    "T": RoleWeights("Dribbling", Decimal("0.5"), "Fallo commesso", Decimal("0.3")),
    "C": RoleWeights("Passaggio decisivo", Decimal("0.5"), "Passaggio sbagliato", Decimal("0.1")),
    "W": RoleWeights("Assist", Decimal("1"), "Fuorigioco", Decimal("0.4")),
    "A": RoleWeights("Gol", Decimal("0.6"), "Tiro fuori dallo specchio", Decimal("0.2")),
}
CATEGORY_LABELS = {"P": "Portieri", "D": "Difensori", "T": "Esterni bassi", "C": "Centrocampisti",
                   "W": "Esterni alti", "A": "Attaccanti"}


@dataclass(frozen=True)
class ScoringConfig:
    matches: int = 2
    not_played_vote: Decimal = Decimal("5.5")        # PDF: nessuna delle partite giocata
    missing_match_vote: Decimal = Decimal("5.5")     # voto SV: predefinito 5,5 (confermato dallo staff), modificabile per stagione
    decimals: int | None = None                       # None = nessun arrotondamento (evidenza Excel, Q4); n = arrotonda a n decimali


@dataclass(frozen=True)
class MatchStat:
    match_no: int
    played: bool
    base_vote: Decimal | None = None
    bonus: int = 0
    malus: int = 0


@dataclass
class MatchBreakdown:
    match_no: int
    played: bool
    base_vote: Fraction
    bonus: int
    malus: int
    bonus_points: Fraction
    malus_points: Fraction
    match_vote: Fraction


@dataclass
class PlayerVote:
    vote: Fraction
    matches: list[MatchBreakdown] = field(default_factory=list)
    played_none: bool = False
    raw: Fraction | None = None


# ---------------------------------------------------------------- utilita'


def canon(d: Decimal) -> str:
    """Testo canonico, senza notazione scientifica e senza zeri finali: 6.50 -> '6.5', 10 -> '10'."""
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def to_decimal(text) -> Decimal:
    try:
        d = Decimal(str(text).strip().replace(",", "."))
    except (InvalidOperation, ValueError) as exc:
        raise ScoringError(f"«{text}» non è un numero valido.") from exc
    if not d.is_finite():
        raise ScoringError(f"«{text}» non è un numero valido.")
    return d


def round_half_up(f: Fraction, decimals: int) -> Fraction:
    """Arrotonda un razionale a `decimals` cifre, la meta' verso l'alto (lontano da zero)."""
    scale = 10 ** decimals
    scaled = f * scale
    n = math.floor(abs(scaled) + Fraction(1, 2))
    return Fraction(n if scaled >= 0 else -n, scale)


def quantize(f, decimals: int | None) -> Fraction:
    """None = voto esatto, nessun arrotondamento."""
    f = Fraction(f)
    return f if decimals is None else round_half_up(f, decimals)


def fstr(f) -> str:
    """Testo canonico di un razionale per il database: decimale esatto se termina ('7.1'), altrimenti 'n/d' ('20/3')."""
    f = Fraction(f)
    if f.denominator == 1:
        return str(f.numerator)
    d = f.denominator
    for p in (2, 5):
        while d % p == 0:
            d //= p
    if d != 1:
        return f"{f.numerator}/{f.denominator}"
    digits = 0
    while (f * 10 ** digits).denominator != 1:
        digits += 1
    return canon(Decimal(f.numerator * 10 ** digits // f.denominator).scaleb(-digits))


def fmt(value, places: int = 3) -> str:
    """Formato italiano per la VISUALIZZAZIONE: virgola, al piu' `places` decimali, niente zeri inutili.
    Il valore esatto non viene toccato: arrotonda solo cio' che si mostra."""
    if value is None or value == "":
        return "—"
    f = Fraction(value if not isinstance(value, Decimal) else str(value)) if not isinstance(value, Fraction) else value
    shown = round_half_up(f, places)
    return fstr(shown).replace(".", ",")


# -------------------------------------------------------------- immissione


def parse_cell(sv: bool, vote: str, bonus: str, malus: str) -> MatchStat | None:
    """Interpreta una cella del foglio di immissione. None = cella vuota (non compilata).
    Solleva ScoringError con un messaggio comprensibile se i dati sono incoerenti."""
    vote, bonus, malus = (vote or "").strip(), (bonus or "").strip(), (malus or "").strip()
    if not sv and not vote and not bonus and not malus:
        return None

    def count(text: str, what: str) -> int:
        if text == "":
            return 0
        if not text.isdigit() or int(text) > COUNT_MAX:
            raise ScoringError(f"{what}: serve un numero intero tra 0 e {COUNT_MAX}.")
        return int(text)

    b, m = count(bonus, "Bonus"), count(malus, "Malus")
    if sv:
        if vote:
            raise ScoringError("Hai indicato «non ha giocato» e anche un voto: scegli una cosa sola.")
        if b or m:
            raise ScoringError("Un giocatore che non ha giocato non può avere bonus o malus.")
        return MatchStat(0, False, None, 0, 0)
    if not vote:
        raise ScoringError("Indica il voto, oppure spunta «non ha giocato».")
    v = to_decimal(vote)
    if v.as_tuple().exponent < -MAX_VOTE_DECIMALS:
        raise ScoringError(f"Il voto può avere al massimo {MAX_VOTE_DECIMALS} decimali.")
    if not (VOTE_MIN <= v <= VOTE_MAX):
        raise ScoringError(f"Il voto deve essere tra {fmt(VOTE_MIN)} e {fmt(VOTE_MAX)} (controlla di non aver sbagliato a digitare).")
    return MatchStat(0, True, v, b, m)


# ------------------------------------------------------------------ calcolo


def compute_player_vote(ruolo: str, stats: list[MatchStat], weights: dict[str, RoleWeights],
                        cfg: ScoringConfig) -> PlayerVote:
    """Voto finale di un giocatore. Servono i dati di TUTTE le partite del concorso: se mancano, errore."""
    if ruolo not in weights:
        raise ScoringError(f"Ruolo sconosciuto: {ruolo}")
    by_no = {s.match_no: s for s in stats}
    expected = list(range(1, cfg.matches + 1))
    if sorted(by_no) != expected or len(stats) != len(by_no):
        raise ScoringError(f"Servono i dati delle partite {expected}: ricevute {sorted(by_no)}.")
    w = weights[ruolo]
    wb, wm = Fraction(w.bonus_weight), Fraction(w.malus_weight)
    matches: list[MatchBreakdown] = []
    for no in expected:
        s = by_no[no]
        if s.played:
            if s.base_vote is None:
                raise ScoringError(f"Partita {no}: manca il voto base.")
            base = Fraction(s.base_vote)
        else:
            if s.bonus or s.malus or s.base_vote is not None:
                raise ScoringError(f"Partita {no}: un giocatore che non ha giocato non può avere voto, bonus o malus.")
            base = Fraction(cfg.missing_match_vote)
        bonus_points, malus_points = s.bonus * wb, s.malus * wm
        matches.append(MatchBreakdown(no, s.played, base, s.bonus, s.malus, bonus_points, malus_points,
                                      base + bonus_points - malus_points))
    if not any(m.played for m in matches):
        return PlayerVote(quantize(Fraction(cfg.not_played_vote), cfg.decimals), matches, played_none=True)
    raw = sum((m.match_vote for m in matches), Fraction(0)) / cfg.matches
    return PlayerVote(quantize(raw, cfg.decimals), matches, played_none=False, raw=raw)


def lineup_total(votes: list, size: int = 6) -> Fraction:
    if len(votes) != size:
        raise ScoringError(f"Servono {size} voti, ne ho {len(votes)}.")
    return sum((Fraction(v) for v in votes), Fraction(0))


def explain(ruolo: str, vote: PlayerVote, weights: dict[str, RoleWeights], cfg: ScoringConfig) -> list[str]:
    """Spiegazione riga per riga, leggibile da chi non conosce la formula."""
    w = weights[ruolo]
    lines = []
    for m in vote.matches:
        if not m.played:
            lines.append(f"Partita {m.match_no}: non ha giocato, vale {fmt(m.match_vote)}")
            continue
        parts = [fmt(m.base_vote)]
        if m.bonus:
            parts.append(f"+ {m.bonus} × {fmt(w.bonus_weight)} ({w.bonus_label.lower()})")
        if m.malus:
            parts.append(f"− {m.malus} × {fmt(w.malus_weight)} ({w.malus_label.lower()})")
        lines.append(f"Partita {m.match_no}: {' '.join(parts)} = {fmt(m.match_vote)}")
    if vote.played_none:
        lines.append(f"Non ha giocato nessuna partita: voto {fmt(vote.vote)}")
    else:
        lines.append(f"Media delle {len(vote.matches)} partite = {fmt(vote.vote)}")
    return lines
