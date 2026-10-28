"""Accesso al database SQLite: connessione, transazioni, migrazioni versionate."""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from flask import current_app, g

from .timeutil import utcnow_iso

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def connect(path: str) -> sqlite3.Connection:
    # isolation_level=None: autocommit, le transazioni sono esplicite (vedi transaction()).
    conn = sqlite3.connect(path, isolation_level=None, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    # WAL (predefinito) per il server. La demo nel browser (WebAssembly) imposta FANTAPG_JOURNAL_MODE=DELETE.
    journal = os.environ.get("FANTAPG_JOURNAL_MODE", "WAL").upper()
    if journal not in ("WAL", "DELETE", "TRUNCATE", "MEMORY"):
        journal = "WAL"
    conn.execute(f"PRAGMA journal_mode = {journal}")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_exc=None) -> None:
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection):
    """Transazione con lock di scrittura immediato: serializza le operazioni concorrenti."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Applica le migrazioni non ancora eseguite. Ogni migrazione è atomica."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    done = {r["version"] for r in conn.execute("SELECT version FROM schema_migrations")}
    applied = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        version = path.stem
        if version in done:
            continue
        script = (
            "BEGIN IMMEDIATE;\n"
            + path.read_text(encoding="utf-8")
            + f"\nINSERT INTO schema_migrations (version, applied_at) "
            f"VALUES ('{version}', '{utcnow_iso()}');\nCOMMIT;"
        )
        try:
            conn.executescript(script)
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        applied.append(version)
    return applied
