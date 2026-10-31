"""Media e andamento storico dei voti finali dei giocatori.

Quale voto si media
-------------------
Il voto finale UFFICIALE di un giocatore in un concorso e' quello che il calcolo salva in ``player_scores.vote``
(uno solo per concorso e giocatore: la chiave primaria e' (contest_id, player_id)), lo stesso valore che entra nel
punteggio delle formazioni. Qui NON si ricalcola nulla e non si usa il totale di una formazione: si legge soltanto
quel dato persistito, cosi' media e storico non possono divergere dal punteggio ufficiale.
Il voto puo' essere ``computed`` (calcolato dai dati verificati) o ``manual`` (inserito a mano dallo staff, con
motivo): entrambi sono voti ufficiali e la provenienza resta visibile nello storico.

Quando un concorso conta
------------------------
Solo se e' ``published`` E i punteggi salvati sono aggiornati (``calculated_version = data_version``: e' la stessa
condizione che ``results.is_stale`` usa per dire «da ricalcolare»). Restano quindi fuori: concorsi in compilazione,
con conflitti, non pubblicati e concorsi pubblicati con una correzione non ancora confermata dallo staff.
Appena la correzione viene applicata (ricalcolo confermato) il concorso rientra con i voti corretti.
Le compilazioni indipendenti (``vote_entries``) e i dati verificati (``match_stats``) non vengono mai letti.

Perimetro
---------
Media e storico sono calcolati per STAGIONE (i concorsi appartengono a una stagione e il listone e' per stagione):
la media mostrata e' sempre la media delle voci dello storico mostrato.
"""
from __future__ import annotations

import json
from fractions import Fraction

from ..domain.scoring import round_half_up

# Concorso i cui punteggi ufficiali sono definitivi (vedi docstring del modulo; stessa regola di results.is_stale).
_OFFICIAL = ("c.status = 'published' AND c.calculated_version IS NOT NULL "
             "AND c.calculated_version = c.data_version")

VOTE_DECIMALS = 2   # media e voti si MOSTRANO sempre con 2 decimali (6,55; 7,00); il valore esatto non viene toccato

_CHUNK = 500   # limite prudente di parametri per una singola query


def fmt_vote(value) -> str:
    """Formato italiano per la visualizzazione: virgola e sempre 2 decimali (mezzo verso l'alto). Solo testo:
    il valore esatto (frazione) resta intatto."""
    f = round_half_up(Fraction(value), VOTE_DECIMALS)
    sign = "-" if f < 0 else ""
    cents = abs(f.numerator * 10 ** VOTE_DECIMALS // f.denominator)
    return f"{sign}{cents // 10 ** VOTE_DECIMALS},{cents % 10 ** VOTE_DECIMALS:02d}"


def _average(votes: list[Fraction]) -> Fraction:
    return sum(votes, Fraction(0)) / len(votes)


def averages(db, season_id: int, player_ids=None) -> dict[int, dict]:
    """Media dei voti finali ufficiali per giocatore: ``{player_id: {"avg": Fraction, "n": int}}``.

    Una sola query per tutti i giocatori richiesti (nessuna query per riga). I giocatori senza voti ufficiali
    NON compaiono nel risultato: chi li mostra deve scrivere «—», mai 0. ``player_ids=None`` = tutti i giocatori.
    """
    base = ("SELECT ps.player_id, ps.vote FROM player_scores ps JOIN contests c ON c.id = ps.contest_id "
            f"WHERE c.season_id = ? AND {_OFFICIAL}")
    if player_ids is None:
        rows = db.execute(base, (season_id,)).fetchall()
    else:
        ids = list(dict.fromkeys(int(i) for i in player_ids))
        rows = []
        for start in range(0, len(ids), _CHUNK):
            part = ids[start:start + _CHUNK]
            rows += db.execute(f"{base} AND ps.player_id IN ({','.join('?' * len(part))})",
                               [season_id, *part]).fetchall()
    votes: dict[int, list[Fraction]] = {}
    for r in rows:
        votes.setdefault(r["player_id"], []).append(Fraction(r["vote"]))
    return {pid: {"avg": _average(v), "n": len(v)} for pid, v in votes.items()}


def _flags(source: str, breakdown_json: str | None) -> dict:
    """Informazioni di provenienza del voto, per spiegarlo nello storico."""
    played_none = False
    if source == "computed" and breakdown_json:
        try:
            played_none = bool(json.loads(breakdown_json).get("played_none"))
        except (ValueError, AttributeError):
            played_none = False
    return {"manual": source == "manual", "played_none": played_none}


def history(db, season_id: int, player_id: int) -> dict:
    """Storico dei voti ufficiali di un giocatore, dal concorso piu' recente al meno recente (ordine dell'app:
    numero del concorso decrescente), con una sola voce per concorso, piu' media e numero di voti.

    ``{"entries": [{contest_id, number, name, closes_at, published_at, vote: Fraction, vote_text, manual, played_none}],
       "avg": Fraction | None, "avg_text": "6,55" | None, "n": int}``
    """
    rows = db.execute(
        "SELECT c.id AS contest_id, c.number, c.name, c.closes_at, c.published_at, "
        "       ps.vote, ps.source, ps.breakdown_json "
        "  FROM player_scores ps JOIN contests c ON c.id = ps.contest_id "
        f" WHERE c.season_id = ? AND ps.player_id = ? AND {_OFFICIAL} "
        " ORDER BY c.number DESC, c.id DESC",
        (season_id, player_id),
    ).fetchall()
    entries = []
    for r in rows:
        entries.append({
            "contest_id": r["contest_id"], "number": r["number"], "name": r["name"],
            "closes_at": r["closes_at"], "published_at": r["published_at"],
            "vote": Fraction(r["vote"]), "vote_text": fmt_vote(r["vote"]), **_flags(r["source"], r["breakdown_json"]),
        })
    avg = _average([e["vote"] for e in entries]) if entries else None
    return {"entries": entries, "avg": avg, "avg_text": fmt_vote(avg) if avg is not None else None, "n": len(entries)}


# ------------------------------------------------------------------ presentazione (solo numeri, niente HTML)


def display_averages(db, season_id: int, player_ids) -> dict[int, dict]:
    """Come ``averages`` ma con il testo gia' formattato all'italiana: ``{pid: {"text": "6,75", "n": 3}}``.
    Il valore esatto non viene toccato: si arrotonda solo cio' che si mostra."""
    return {pid: {"text": fmt_vote(a["avg"]), "n": a["n"]}
            for pid, a in averages(db, season_id, player_ids).items()}


CHART_W, CHART_H = 320, 150
_PAD_L, _PAD_R, _PAD_T, _PAD_B = 34, 12, 14, 26


def trend_chart(entries: list[dict], avg: Fraction | None) -> dict | None:
    """Geometria di un piccolo grafico a linea (SVG viewBox CHART_W x CHART_H) in ordine cronologico.

    Serve almeno un secondo punto perche' ci sia un andamento: con meno di 2 voti restituisce None.
    L'asse verticale parte dal voto minimo e arriva al massimo (con un po' di margine): e' un grafico di
    tendenza, i valori esatti sono nella tabella. Nessuna dipendenza esterna.
    """
    if len(entries) < 2:
        return None
    chrono = list(reversed(entries))            # dal piu' vecchio al piu' recente
    votes = [float(e["vote"]) for e in chrono]
    lo, hi = min(votes), max(votes)
    if hi - lo < 1e-9:
        lo, hi = lo - 1, hi + 1
    margin = (hi - lo) * 0.12
    lo, hi = lo - margin, hi + margin
    inner_w, inner_h = CHART_W - _PAD_L - _PAD_R, CHART_H - _PAD_T - _PAD_B

    def y_of(v: float) -> float:
        return _PAD_T + (hi - v) / (hi - lo) * inner_h

    n = len(chrono)
    points = []
    for i, (e, v) in enumerate(zip(chrono, votes)):
        x = _PAD_L + (inner_w * i / (n - 1))
        points.append({"x": round(x, 1), "y": round(y_of(v), 1), "number": e["number"],
                       "label": f"{e['name']}: {fmt_vote(e['vote'])}"})
    return {
        "w": CHART_W, "h": CHART_H, "points": points,
        "line": " ".join(f"{p['x']},{p['y']}" for p in points),
        "avg_y": round(y_of(float(avg)), 1) if avg is not None else None,
        "left": _PAD_L, "right": CHART_W - _PAD_R, "base": CHART_H - _PAD_B,
        "top_label": fmt_vote(max(e["vote"] for e in chrono)),
        "bottom_label": fmt_vote(min(e["vote"] for e in chrono)),
        "top_y": round(y_of(max(votes)), 1), "bottom_y": round(y_of(min(votes)), 1),
    }
