"""Regole di validazione del listone giocatori. Modulo puro: niente web, niente database.

Il listone ufficiale è quello dell'organizzazione (file Excel). Qui si decide solo se
una riga è utilizzabile; non si deduce mai nulla (es. il ruolo) da altre fonti.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

EXCEL_ERRORS = frozenset(
    {"#N/A", "#REF!", "#VALUE!", "#NAME?", "#DIV/0!", "#NULL!", "#NUM!", "#GETTING_DATA", "#SPILL!", "#CALC!"}
)
SERIES = ("A", "B")
ROLES = ("P", "D", "T", "C", "W", "A")
ROLE_LABELS = {
    "P": "Portiere",
    "D": "Difensore centrale, mediano",
    "T": "Terzino, terzino fluidificante",
    "C": "Centrocampista, trequartista centrale",
    "W": "Ala di centrocampo, ala offensiva",
    "A": "Attaccante",
}
FIELDS = ("id", "name", "serie", "ruolo", "squadra")
FIELD_LABELS = {"id": "ID", "name": "Giocatore", "serie": "Serie", "ruolo": "Ruolo", "squadra": "Squadra"}
FIELD_ALIASES = {
    "id": {"id", "id giocatore", "codice", "codice giocatore"},
    "name": {"giocatore", "nome", "nome completo", "nome_completo", "calciatore"},
    "serie": {"serie", "lega"},
    "ruolo": {"ruolo"},
    "squadra": {"squadra", "team", "club"},
}
MAX_NAME_LENGTH = 120

# Ordine di priorita' per scegliere il motivo "principale" di scarto di una riga.
CODE_PRIORITY = (
    "placeholder", "excel_error", "missing", "bad_id", "bad_name",
    "serie_non_ammessa", "bad_serie", "bad_role", "dup_id",
)
CODE_LABELS = {
    "placeholder": "Righe segnaposto o con errore Excel (ID e nome non disponibili)",
    "excel_error": "Valori di errore Excel (#N/A, #REF!, ...) nei campi obbligatori",
    "missing": "Campi obbligatori mancanti",
    "bad_id": "ID non valido (deve essere un numero intero positivo)",
    "bad_name": "Nome non valido",
    "serie_non_ammessa": "Serie non prevista dal regolamento (ammesse solo A e B)",
    "bad_serie": "Serie non riconosciuta",
    "bad_role": "Ruolo non riconosciuto (ammessi P, D, T, C, W, A)",
    "dup_id": "ID duplicato nel file",
}
WARNING_LABELS = {
    "homonym": "Giocatori omonimi (ID diversi, stesso nome)",
    "team_in_both_series": "Squadra presente sia in Serie A sia in Serie B",
}


@dataclass(frozen=True)
class ParsedRow:
    row_number: int
    external_id: int
    name: str
    serie: str
    ruolo: str
    squadra: str


@dataclass
class RowIssue:
    row_number: int
    codes: list[str]
    messages: list[str]
    raw: dict

    @property
    def primary_code(self) -> str:
        for code in CODE_PRIORITY:
            if code in self.codes:
                return code
        return self.codes[0]


@dataclass
class Report:
    valid: list[ParsedRow] = field(default_factory=list)
    issues: list[RowIssue] = field(default_factory=list)
    warnings: list[RowIssue] = field(default_factory=list)
    total_rows: int = 0  # righe non vuote lette

    def issues_by_code(self) -> list[dict]:
        groups: dict[str, list[RowIssue]] = {}
        for issue in self.issues:
            groups.setdefault(issue.primary_code, []).append(issue)
        return [
            {"code": c, "label": CODE_LABELS[c], "count": len(groups[c]), "rows": groups[c]}
            for c in CODE_PRIORITY
            if c in groups
        ]

    def warnings_by_code(self) -> list[dict]:
        groups: dict[str, list[RowIssue]] = {}
        for w in self.warnings:
            groups.setdefault(w.codes[0], []).append(w)
        return [
            {"code": c, "label": WARNING_LABELS[c], "count": len(rows), "rows": rows}
            for c, rows in groups.items()
        ]


# ---------------------------------------------------------------- utilita'


def normalize_header(value) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).casefold()


def detect_columns(cells) -> dict[str, int]:
    """Riconosce le colonne note in una riga di intestazione (indici a base 0)."""
    found: dict[str, int] = {}
    for idx, cell in enumerate(cells):
        h = normalize_header(cell)
        if not h:
            continue
        for fld, aliases in FIELD_ALIASES.items():
            if fld not in found and h in aliases:
                found[fld] = idx
    return found


def find_header(rows, max_scan: int = 30):
    """rows: lista di (numero_riga, celle). Restituisce (numero_riga, mapping) o (None, {})."""
    best_row, best_map = None, {}
    for row_number, cells in rows[:max_scan]:
        mapping = detect_columns(cells)
        if len(mapping) > len(best_map):
            best_row, best_map = row_number, mapping
    if len(best_map) >= 3:
        return best_row, best_map
    return None, {}


def normalize_search(text: str) -> str:
    """Testo per la ricerca: senza accenti, minuscolo, spazi normalizzati."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(stripped.casefold().split())


def _clean(value):
    if value is None:
        return None
    if isinstance(value, str):
        s = " ".join(value.split())
        return s or None
    return value


def _is_excel_error(value) -> bool:
    return isinstance(value, str) and value.strip().upper() in EXCEL_ERRORS


def _show(value) -> str:
    return "" if value is None else str(value)


# ----------------------------------------------------------- validazione riga


def _parse_id(value):
    if value is None:
        return None, "missing", "ID mancante"
    if _is_excel_error(value):
        return None, "excel_error", f"ID con errore Excel ({value})"
    if isinstance(value, bool):
        return None, "bad_id", f"ID non valido ({value})"
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, float) and value.is_integer():
        parsed = int(value)
    elif isinstance(value, str) and re.fullmatch(r"\d+", value.strip()):
        parsed = int(value.strip())
    else:
        return None, "bad_id", f"ID non valido ({value})"
    if parsed <= 0:
        return None, "bad_id", f"ID non valido ({value})"
    return parsed, None, None


def _parse_name(value):
    if value is None:
        return None, "missing", "Nome mancante"
    if _is_excel_error(value):
        return None, "excel_error", f"Nome con errore Excel ({value})"
    if not isinstance(value, str):
        return None, "bad_name", f"Nome non valido ({value})"
    if len(value) > MAX_NAME_LENGTH:
        return None, "bad_name", f"Nome troppo lungo (oltre {MAX_NAME_LENGTH} caratteri)"
    return value, None, None


def _parse_serie(value):
    if value is None:
        return None, "missing", "Serie mancante"
    if _is_excel_error(value):
        return None, "excel_error", f"Serie con errore Excel ({value})"
    s = str(value).strip().upper()
    if s in SERIES:
        return s, None, None
    if s == "C":
        return None, "serie_non_ammessa", "Serie «C» non prevista dal regolamento (ammesse A e B)"
    return None, "bad_serie", f"Serie non riconosciuta ({value})"


def _parse_role(value):
    if value is None:
        return None, "missing", "Ruolo mancante"
    if _is_excel_error(value):
        return None, "excel_error", f"Ruolo con errore Excel ({value})"
    s = str(value).strip().upper()
    if s in ROLES:
        return s, None, None
    return None, "bad_role", f"Ruolo non riconosciuto ({value})"


def _parse_team(value):
    if value is None:
        return None, "missing", "Squadra mancante"
    if _is_excel_error(value):
        return None, "excel_error", f"Squadra con errore Excel ({value})"
    return str(value), None, None


def _check_row(raw: dict):
    """Restituisce None (riga vuota), ParsedRow oppure RowIssue."""
    row_number = raw["row_number"]
    cleaned = {f: _clean(raw.get(f)) for f in FIELDS}
    if all(v is None for v in cleaned.values()):
        return None
    shown = {f: _show(raw.get(f)) for f in FIELDS}

    id_unusable = cleaned["id"] is None or _is_excel_error(cleaned["id"])
    name_unusable = cleaned["name"] is None or _is_excel_error(cleaned["name"])
    if id_unusable and name_unusable:
        return RowIssue(
            row_number, ["placeholder"],
            ["Riga segnaposto o con errore Excel: ID e nome non disponibili"], shown,
        )

    parsers = {
        "id": _parse_id, "name": _parse_name, "serie": _parse_serie,
        "ruolo": _parse_role, "squadra": _parse_team,
    }
    values, codes, messages = {}, [], []
    for fld, parser in parsers.items():
        value, code, message = parser(cleaned[fld])
        if code:
            codes.append(code)
            messages.append(message)
        else:
            values[fld] = value
    if codes:
        return RowIssue(row_number, codes, messages, shown)
    return ParsedRow(row_number, values["id"], values["name"], values["serie"], values["ruolo"], values["squadra"])


def validate_rows(raw_rows) -> Report:
    """Valida le righe grezze (dict con row_number + campi) e restituisce un Report."""
    report = Report()
    parsed: list[ParsedRow] = []
    issues: list[RowIssue] = []
    raw_by_row: dict[int, dict] = {}

    for raw in raw_rows:
        result = _check_row(raw)
        if result is None:
            continue
        report.total_rows += 1
        raw_by_row[raw["row_number"]] = {f: _show(raw.get(f)) for f in FIELDS}
        if isinstance(result, ParsedRow):
            parsed.append(result)
        else:
            issues.append(result)

    # ID duplicati: si scartano tutte le righe coinvolte (non si sceglie "quella giusta").
    by_id: dict[int, list[int]] = {}
    for row in parsed:
        by_id.setdefault(row.external_id, []).append(row.row_number)
    duplicated = {i: rows for i, rows in by_id.items() if len(rows) > 1}
    valid: list[ParsedRow] = []
    for row in parsed:
        if row.external_id in duplicated:
            others = ", ".join(str(r) for r in duplicated[row.external_id] if r != row.row_number)
            issues.append(
                RowIssue(row.row_number, ["dup_id"],
                         [f"ID {row.external_id} duplicato (anche alle righe {others})"],
                         raw_by_row[row.row_number])
            )
        else:
            valid.append(row)

    report.valid = sorted(valid, key=lambda r: r.row_number)
    report.issues = sorted(issues, key=lambda i: i.row_number)

    # Avvisi non bloccanti.
    names: dict[str, list[ParsedRow]] = {}
    for row in report.valid:
        names.setdefault(normalize_search(row.name), []).append(row)
    for rows in names.values():
        if len(rows) > 1:
            for row in rows:
                report.warnings.append(
                    RowIssue(row.row_number, ["homonym"],
                             [f"Omonimo: «{row.name}» compare con ID {', '.join(str(r.external_id) for r in rows)}"],
                             raw_by_row[row.row_number])
                )
    series_by_team: dict[str, set[str]] = {}
    for row in report.valid:
        series_by_team.setdefault(row.squadra.casefold(), set()).add(row.serie)
    for row in report.valid:
        if len(series_by_team[row.squadra.casefold()]) > 1:
            report.warnings.append(
                RowIssue(row.row_number, ["team_in_both_series"],
                         [f"La squadra «{row.squadra}» compare sia in Serie A sia in Serie B"],
                         raw_by_row[row.row_number])
            )
    report.warnings.sort(key=lambda w: w.row_number)
    return report
