"""Flusso di importazione del listone: caricamento -> anteprima -> conferma -> applicazione."""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from ..db import transaction
from ..domain.excel_reader import ImportFileError, TableRead, list_sheets, read_table
from ..domain.listone import Report, validate_rows
from ..timeutil import utcnow_iso
from . import audit
from .players import Diff, apply_rows, compute_diff

XLSX_MAGIC = b"PK\x03\x04"
MAX_STORED_ISSUES = 1000


class ImportStateError(Exception):
    """L'importazione non è più nello stato che consente l'operazione richiesta."""


@dataclass
class Preview:
    table: TableRead
    report: Report | None
    diff: Diff | None

    @property
    def ready(self) -> bool:
        return self.report is not None and len(self.report.valid) > 0


def store_upload(upload_dir: str, data: bytes) -> tuple[str, str]:
    if not data.startswith(XLSX_MAGIC):
        raise ImportFileError("Il file non è un Excel .xlsx valido.")
    Path(upload_dir).mkdir(parents=True, exist_ok=True)
    path = os.path.join(upload_dir, f"{uuid.uuid4().hex}.xlsx")
    with open(path, "wb") as fh:
        fh.write(data)
    return path, hashlib.sha256(data).hexdigest()


def create_batch(db, *, season_id: int, user_id, filename: str, sheet: str, path: str, sha256: str) -> int:
    # Controllo anticipato: foglio esistente e file leggibile (solleva ImportFileError).
    sheets = list_sheets(path)
    if sheet not in sheets:
        raise ImportFileError(f"Il foglio «{sheet}» non esiste. Fogli disponibili: {', '.join(sheets)}.")
    with transaction(db):
        cur = db.execute(
            "INSERT INTO import_batches (season_id, user_id, filename, sheet, file_path, file_sha256, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'preview', ?)",
            (season_id, user_id, filename[:200], sheet, path, sha256, utcnow_iso()),
        )
        batch_id = cur.lastrowid
        audit.log(db, user_id, "import.upload", "import_batch", batch_id,
                  {"filename": filename[:200], "sheet": sheet, "season_id": season_id})
    return batch_id


def get_batch(db, batch_id: int):
    return db.execute(
        "SELECT b.*, s.name AS season_name, u.display_name AS user_name "
        "FROM import_batches b JOIN seasons s ON s.id = b.season_id "
        "LEFT JOIN users u ON u.id = b.user_id WHERE b.id = ?",
        (batch_id,),
    ).fetchone()


def _mapping(batch) -> dict | None:
    return json.loads(batch["mapping_json"]) if batch["mapping_json"] else None


def build_preview(db, batch, max_rows: int = 20000) -> Preview:
    table = read_table(batch["file_path"], batch["sheet"], _mapping(batch), max_rows)
    if table.header_row is None or table.missing_fields:
        return Preview(table, None, None)
    report = validate_rows(table.raw_rows)
    diff = compute_diff(db, batch["season_id"], report.valid)
    return Preview(table, report, diff)


def set_mapping(db, batch_id: int, user_id, header_row: int, columns: dict[str, int]) -> None:
    with transaction(db):
        batch = get_batch(db, batch_id)
        if batch is None or batch["status"] != "preview":
            raise ImportStateError("L'importazione non è più modificabile.")
        db.execute(
            "UPDATE import_batches SET mapping_json = ? WHERE id = ?",
            (json.dumps({"header_row": header_row, "columns": columns}), batch_id),
        )
        audit.log(db, user_id, "import.mapping", "import_batch", batch_id,
                  {"header_row": header_row, "columns": columns})


def apply_batch(db, batch_id: int, user_id, max_rows: int = 20000) -> dict:
    """Applica l'importazione in un'unica transazione: o tutto o niente.

    Il file viene riletto e rivalidato adesso (non ci si fida dell'anteprima mostrata prima)
    e se ne verifica l'impronta SHA-256. Una doppia conferma (doppio clic, due schede)
    non può applicare due volte: il lock di scrittura serializza e lo stato lo impedisce.
    """
    try:
        with transaction(db):
            batch = get_batch(db, batch_id)
            if batch is None:
                raise ImportStateError("Importazione inesistente.")
            if batch["status"] != "preview":
                raise ImportStateError("Questa importazione è già stata applicata o annullata.")
            path = batch["file_path"]
            if not path or not os.path.exists(path):
                raise ImportStateError("Il file caricato non è più disponibile: caricalo di nuovo.")
            with open(path, "rb") as fh:
                if hashlib.sha256(fh.read()).hexdigest() != batch["file_sha256"]:
                    raise ImportStateError("Il file caricato è stato alterato: caricalo di nuovo.")
            table = read_table(path, batch["sheet"], _mapping(batch), max_rows)
            if table.header_row is None or table.missing_fields:
                raise ImportStateError("Colonne obbligatorie non riconosciute: completa l'associazione.")
            report = validate_rows(table.raw_rows)
            if not report.valid:
                raise ImportStateError("Nessuna riga valida da importare.")
            diff = compute_diff(db, batch["season_id"], report.valid)
            counts = apply_rows(db, batch["season_id"], batch_id, report.valid)
            summary = {
                "rows_read": report.total_rows,
                "rows_valid": len(report.valid),
                "rows_rejected": len(report.issues),
                **counts,
                "absent_from_file": len(diff.absent),
                "issues_by_code": [{"code": g["code"], "label": g["label"], "count": g["count"]}
                                   for g in report.issues_by_code()],
                "issues": [
                    {"row": i.row_number, "reasons": i.messages, "raw": i.raw}
                    for i in report.issues[:MAX_STORED_ISSUES]
                ],
                "issues_truncated": len(report.issues) > MAX_STORED_ISSUES,
            }
            db.execute(
                "UPDATE import_batches SET status = 'applied', summary_json = ?, applied_at = ? WHERE id = ?",
                (json.dumps(summary, ensure_ascii=False), utcnow_iso(), batch_id),
            )
            audit.log(db, user_id, "import.apply", "import_batch", batch_id,
                      {k: summary[k] for k in ("rows_read", "rows_valid", "rows_rejected", "new", "changed",
                                               "unchanged", "absent_from_file")})
            return summary
    except ImportStateError:
        raise
    except Exception as exc:
        # La transazione è già stata annullata: nessun dato parziale nel listone.
        _record_failure(db, batch_id, user_id, exc)
        raise


def _record_failure(db, batch_id: int, user_id, exc: Exception) -> None:
    message = f"{type(exc).__name__}: {exc}"[:500]
    try:
        with transaction(db):
            db.execute(
                "UPDATE import_batches SET status = 'failed', error = ? WHERE id = ? AND status = 'preview'",
                (message, batch_id),
            )
            audit.log(db, user_id, "import.failed", "import_batch", batch_id, {"error": message})
    except Exception:  # il log dell'errore non deve nascondere l'errore originale
        pass


def cancel_batch(db, batch_id: int, user_id) -> None:
    with transaction(db):
        batch = get_batch(db, batch_id)
        if batch is None or batch["status"] != "preview":
            raise ImportStateError("L'importazione non è più annullabile.")
        db.execute("UPDATE import_batches SET status = 'cancelled' WHERE id = ?", (batch_id,))
        audit.log(db, user_id, "import.cancel", "import_batch", batch_id)
    if batch["file_path"] and os.path.exists(batch["file_path"]):
        os.remove(batch["file_path"])


def recent_batches(db, limit: int = 15):
    return db.execute(
        "SELECT b.id, b.filename, b.sheet, b.status, b.created_at, b.applied_at, b.summary_json, "
        "s.name AS season_name, u.display_name AS user_name "
        "FROM import_batches b JOIN seasons s ON s.id = b.season_id "
        "LEFT JOIN users u ON u.id = b.user_id ORDER BY b.id DESC LIMIT ?",
        (limit,),
    ).fetchall()
