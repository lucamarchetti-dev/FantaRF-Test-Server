from __future__ import annotations

import json

from ..timeutil import utcnow_iso


def log(db, user_id, action: str, entity: str | None = None, entity_id=None, details: dict | None = None) -> None:
    """Registra un'operazione nel registro di audit. Va chiamata dentro la stessa
    transazione dell'operazione che descrive, così i due fatti restano coerenti."""
    db.execute(
        "INSERT INTO audit_log (user_id, action, entity, entity_id, details_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            user_id, action, entity,
            None if entity_id is None else str(entity_id),
            json.dumps(details, ensure_ascii=False) if details else None,
            utcnow_iso(),
        ),
    )
