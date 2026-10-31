"""Consultazione e manutenzione del listone (dati anagrafici per stagione)."""
from __future__ import annotations

from dataclasses import dataclass

from ..db import transaction
from ..domain.listone import ParsedRow, normalize_search
from ..timeutil import utcnow_iso
from . import audit


@dataclass
class Diff:
    new: list[ParsedRow]
    changed: list[dict]   # {row, old: {...}, fields: [nomi campi cambiati]}
    unchanged: int
    absent: list[dict]    # giocatori correnti della stagione non presenti nel file


def current_rows(db, season_id: int) -> dict[int, dict]:
    rows = db.execute(
        """SELECT p.external_id, psd.id AS psd_id, psd.player_id, psd.full_name, psd.serie, psd.ruolo,
                  psd.selectable, t.name AS team
             FROM player_season_data psd
             JOIN players p ON p.id = psd.player_id
             JOIN teams t ON t.id = psd.team_id
            WHERE psd.season_id = ? AND psd.valid_to IS NULL""",
        (season_id,),
    ).fetchall()
    return {r["external_id"]: dict(r) for r in rows}


def compute_diff(db, season_id: int, valid_rows: list[ParsedRow]) -> Diff:
    current = current_rows(db, season_id)
    new, changed, unchanged = [], [], 0
    seen = set()
    for row in valid_rows:
        seen.add(row.external_id)
        old = current.get(row.external_id)
        if old is None:
            new.append(row)
            continue
        fields = []
        if old["full_name"] != row.name:
            fields.append("name")
        if old["serie"] != row.serie:
            fields.append("serie")
        if old["ruolo"] != row.ruolo:
            fields.append("ruolo")
        if old["team"].casefold() != row.squadra.casefold():
            fields.append("squadra")
        if fields:
            changed.append({"row": row, "old": old, "fields": fields})
        else:
            unchanged += 1
    absent = [
        {"external_id": ext, "name": r["full_name"], "serie": r["serie"], "ruolo": r["ruolo"], "team": r["team"]}
        for ext, r in current.items() if ext not in seen
    ]
    absent.sort(key=lambda a: a["name"].casefold())
    return Diff(new, changed, unchanged, absent)


def _team_id(db, name: str) -> int:
    db.execute("INSERT OR IGNORE INTO teams (name) VALUES (?)", (name,))
    return db.execute("SELECT id FROM teams WHERE name = ?", (name,)).fetchone()["id"]


def apply_rows(db, season_id: int, batch_id: int, valid_rows: list[ParsedRow]) -> dict:
    """Scrive le righe valide. DEVE essere chiamata dentro una transazione.

    - nuovo giocatore -> nuova riga di stagione;
    - dati cambiati -> la riga corrente viene chiusa (valid_to) e ne viene aperta una nuova:
      lo storico resta intatto; lo stato 'selectable' deciso dallo staff viene mantenuto;
    - invariato -> nessuna scrittura.
    I giocatori assenti dal file NON vengono disattivati automaticamente.
    """
    now = utcnow_iso()
    current = current_rows(db, season_id)
    counts = {"new": 0, "changed": 0, "unchanged": 0}
    for row in valid_rows:
        team_id = _team_id(db, row.squadra)
        search = normalize_search(row.name)
        old = current.get(row.external_id)
        if old is None:
            player = db.execute("SELECT id FROM players WHERE external_id = ?", (row.external_id,)).fetchone()
            if player is None:
                cur = db.execute(
                    "INSERT INTO players (external_id, full_name, search_name, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (row.external_id, row.name, search, now, now),
                )
                player_id = cur.lastrowid
            else:
                player_id = player["id"]
                db.execute(
                    "UPDATE players SET full_name = ?, search_name = ?, updated_at = ? WHERE id = ?",
                    (row.name, search, now, player_id),
                )
            selectable = 1
            counts["new"] += 1
        else:
            same = (
                old["full_name"] == row.name and old["serie"] == row.serie and old["ruolo"] == row.ruolo
                and old["team"].casefold() == row.squadra.casefold()
            )
            if same:
                counts["unchanged"] += 1
                continue
            player_id = old["player_id"]
            db.execute("UPDATE player_season_data SET valid_to = ? WHERE id = ?", (now, old["psd_id"]))
            db.execute(
                "UPDATE players SET full_name = ?, search_name = ?, updated_at = ? WHERE id = ?",
                (row.name, search, now, player_id),
            )
            selectable = old["selectable"]
            counts["changed"] += 1
        db.execute(
            """INSERT INTO player_season_data
                   (season_id, player_id, team_id, serie, ruolo, full_name, selectable, valid_from, batch_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (season_id, player_id, team_id, row.serie, row.ruolo, row.name, selectable, now, batch_id),
        )
    return counts


# ------------------------------------------------------------- consultazione


def _like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search_players(db, season_id: int, *, q: str = "", serie: str = "", ruolo: str = "",
                   team: str = "", state: str = "active", page: int = 1, per_page: int = 40) -> dict:
    """state: 'active' (solo selezionabili), 'inactive', 'all'."""
    where = ["psd.season_id = ?", "psd.valid_to IS NULL"]
    params: list = [season_id]
    if q:
        where.append("p.search_name LIKE ? ESCAPE '\\'")
        params.append(f"%{_like_escape(normalize_search(q))}%")
    if serie:
        where.append("psd.serie = ?")
        params.append(serie)
    if ruolo:
        where.append("psd.ruolo = ?")
        params.append(ruolo)
    if team:
        where.append("t.name = ?")
        params.append(team)
    if state == "active":
        where.append("psd.selectable = 1")
    elif state == "inactive":
        where.append("psd.selectable = 0")
    clause = " AND ".join(where)
    base = (
        "FROM player_season_data psd JOIN players p ON p.id = psd.player_id "
        "JOIN teams t ON t.id = psd.team_id WHERE " + clause
    )
    total = db.execute("SELECT COUNT(*) AS n " + base, params).fetchone()["n"]
    pages = max(1, -(-total // per_page))
    page = min(max(1, page), pages)
    rows = db.execute(
        "SELECT psd.id AS psd_id, p.id AS player_id, p.external_id, psd.full_name, psd.serie, psd.ruolo, psd.selectable, "
        "t.name AS team " + base + " ORDER BY p.search_name, p.external_id LIMIT ? OFFSET ?",
        params + [per_page, (page - 1) * per_page],
    ).fetchall()
    return {"rows": rows, "total": total, "page": page, "pages": pages, "per_page": per_page}


def get_player(db, season_id: int, player_id: int) -> dict | None:
    """Dati correnti di un giocatore nel listone di una stagione (None se non c'e')."""
    row = db.execute(
        """SELECT p.id AS player_id, p.external_id, psd.full_name, psd.serie, psd.ruolo, psd.selectable, t.name AS team
             FROM player_season_data psd
             JOIN players p ON p.id = psd.player_id
             JOIN teams t ON t.id = psd.team_id
            WHERE psd.season_id = ? AND psd.player_id = ? AND psd.valid_to IS NULL""",
        (season_id, player_id),
    ).fetchone()
    return dict(row) if row else None


def teams_in_season(db, season_id: int) -> list[dict]:
    return [dict(r) for r in db.execute(
        """SELECT DISTINCT t.name, psd.serie
             FROM player_season_data psd JOIN teams t ON t.id = psd.team_id
            WHERE psd.season_id = ? AND psd.valid_to IS NULL
            ORDER BY psd.serie, t.name""",
        (season_id,),
    )]


def season_stats(db, season_id: int) -> dict:
    row = db.execute(
        """SELECT COUNT(*) AS total,
                  SUM(selectable = 1) AS selectable,
                  SUM(serie = 'A' AND selectable = 1) AS serie_a,
                  SUM(serie = 'B' AND selectable = 1) AS serie_b,
                  COUNT(DISTINCT team_id) AS teams
             FROM player_season_data WHERE season_id = ? AND valid_to IS NULL""",
        (season_id,),
    ).fetchone()
    return {k: (row[k] or 0) for k in row.keys()}


def set_selectable(db, psd_id: int, selectable: bool, user_id: int) -> dict | None:
    with transaction(db):
        row = db.execute(
            "SELECT psd.id, psd.full_name, psd.season_id, psd.selectable FROM player_season_data psd "
            "WHERE psd.id = ? AND psd.valid_to IS NULL",
            (psd_id,),
        ).fetchone()
        if row is None:
            return None
        db.execute("UPDATE player_season_data SET selectable = ? WHERE id = ?", (1 if selectable else 0, psd_id))
        audit.log(db, user_id, "player.selectable", "player_season_data", psd_id,
                  {"name": row["full_name"], "from": row["selectable"], "to": 1 if selectable else 0})
        return dict(row)


def all_selectable(db, season_id: int) -> list[dict]:
    """Listone completo dei giocatori selezionabili, in forma compatta per il selettore della formazione."""
    rows = db.execute(
        """SELECT p.id, psd.full_name AS n, psd.serie AS s, psd.ruolo AS r, t.name AS t
             FROM player_season_data psd
             JOIN players p ON p.id = psd.player_id
             JOIN teams t ON t.id = psd.team_id
            WHERE psd.season_id = ? AND psd.valid_to IS NULL AND psd.selectable = 1
            ORDER BY p.search_name, p.id""", (season_id,)).fetchall()
    return [dict(r) for r in rows]
