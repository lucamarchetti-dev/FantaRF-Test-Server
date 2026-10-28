"""Lettura del file Excel del listone: individua intestazione e colonne, estrae righe grezze."""
from __future__ import annotations

import zipfile
from dataclasses import dataclass, field

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from .listone import FIELDS, find_header, normalize_header


class ImportFileError(Exception):
    """File non leggibile o foglio inesistente (messaggio mostrabile all'utente)."""


@dataclass
class TableRead:
    sheet: str
    sheets: list[str]
    header_row: int | None
    mapping: dict[str, int]
    headers: list[dict] = field(default_factory=list)  # {index, letter, text}
    raw_rows: list[dict] = field(default_factory=list)

    @property
    def missing_fields(self) -> list[str]:
        return [f for f in FIELDS if f not in self.mapping]


def list_sheets(path: str) -> list[str]:
    wb = _open(path)
    try:
        return list(wb.sheetnames)
    finally:
        wb.close()


def _open(path: str):
    try:
        # data_only: valori calcolati salvati nel file; read_only: nessuna esecuzione di formule.
        return load_workbook(path, read_only=True, data_only=True)
    except (zipfile.BadZipFile, KeyError, OSError, ValueError) as exc:
        raise ImportFileError("Il file non è un Excel .xlsx valido o è danneggiato.") from exc
    except Exception as exc:  # openpyxl solleva eccezioni diverse per file malformati
        raise ImportFileError("Il file non è un Excel .xlsx valido o è danneggiato.") from exc


def read_table(path: str, sheet: str, mapping_override: dict | None = None, max_rows: int = 20000) -> TableRead:
    """mapping_override: {"header_row": int, "columns": {campo: indice_colonna_base_0}}"""
    wb = _open(path)
    try:
        if sheet not in wb.sheetnames:
            raise ImportFileError(
                f"Il foglio «{sheet}» non esiste. Fogli disponibili: {', '.join(wb.sheetnames)}."
            )
        ws = wb[sheet]
        rows: list[tuple[int, tuple]] = []
        for number, cells in enumerate(ws.iter_rows(values_only=True), start=1):
            if number > max_rows:
                raise ImportFileError(f"Il foglio supera il limite di {max_rows} righe.")
            rows.append((number, cells))
        sheets = list(wb.sheetnames)
    finally:
        wb.close()

    if mapping_override and mapping_override.get("columns"):
        header_row = int(mapping_override.get("header_row") or 1)
        mapping = {f: int(i) for f, i in mapping_override["columns"].items() if f in FIELDS and i is not None}
    else:
        header_row, mapping = find_header(rows)

    headers: list[dict] = []
    header_cells: tuple = ()
    if header_row is not None:
        header_cells = next((c for n, c in rows if n == header_row), ())
        for idx, cell in enumerate(header_cells):
            text = " ".join(str(cell).split()) if cell is not None else ""
            if text:
                headers.append({"index": idx, "letter": get_column_letter(idx + 1), "text": text})

    table = TableRead(sheet=sheet, sheets=sheets, header_row=header_row, mapping=mapping, headers=headers)
    if header_row is None or table.missing_fields:
        return table

    for number, cells in rows:
        if number <= header_row:
            continue
        raw = {"row_number": number}
        for fld in FIELDS:
            idx = mapping[fld]
            raw[fld] = cells[idx] if idx < len(cells) else None
        table.raw_rows.append(raw)
    return table


def header_label(text) -> str:
    return normalize_header(text)
