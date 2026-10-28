"""Configurazione. Nessun segreto nel codice: tutto arriva da variabili d'ambiente."""
from __future__ import annotations

import os
import secrets
from pathlib import Path


def _flag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _load_or_create_secret(instance_path: Path) -> str:
    """SECRET_KEY da ambiente; in sviluppo ne genera una persistente in instance/."""
    from_env = os.environ.get("FANTAPG_SECRET_KEY")
    if from_env:
        return from_env
    instance_path.mkdir(parents=True, exist_ok=True)
    key_file = instance_path / "secret_key"
    if key_file.exists():
        return key_file.read_text().strip()
    key = secrets.token_urlsafe(48)
    key_file.write_text(key)
    try:
        key_file.chmod(0o600)
    except OSError:  # es. filesystem Windows
        pass
    return key


def load_config(instance_path: str | os.PathLike, overrides: dict | None = None) -> dict:
    instance = Path(instance_path)
    cfg = {
        "DATABASE": os.environ.get("FANTAPG_DATABASE", str(instance / "fantapg.sqlite3")),
        "UPLOAD_DIR": os.environ.get("FANTAPG_UPLOAD_DIR", str(instance / "uploads")),
        "SECURE_COOKIES": _flag("FANTAPG_SECURE_COOKIES"),
        "TRUST_PROXY": _flag("FANTAPG_TRUST_PROXY"),
        "MAX_UPLOAD_MB": int(os.environ.get("FANTAPG_MAX_UPLOAD_MB", "8")),
        "MAX_IMPORT_ROWS": int(os.environ.get("FANTAPG_MAX_IMPORT_ROWS", "20000")),
        "TIMEZONE": os.environ.get("FANTAPG_TIMEZONE", "Europe/Rome"),
        "LOGIN_MAX_FAILURES": int(os.environ.get("FANTAPG_LOGIN_MAX_FAILURES", "5")),
        "LOGIN_WINDOW_MINUTES": int(os.environ.get("FANTAPG_LOGIN_WINDOW_MINUTES", "15")),
        "MIN_PASSWORD_LENGTH": int(os.environ.get("FANTAPG_MIN_PASSWORD_LENGTH", "10")),
        # Scadenza proposta quando si crea un concorso (regolamento: lunedì ore 20:30). Resta modificabile per concorso.
        "DEFAULT_DEADLINE_WEEKDAY": int(os.environ.get("FANTAPG_DEADLINE_WEEKDAY", "0")),   # 0 = lunedì
        "DEFAULT_DEADLINE_TIME": os.environ.get("FANTAPG_DEADLINE_TIME", "20:30"),
        # "Un giocatore per ruolo": NON ancora confermato dallo staff -> spento di default.
        "DEFAULT_ONE_PER_ROLE": _flag("FANTAPG_DEFAULT_ONE_PER_ROLE", "0"),
        # Parametri del calcolo. Il voto SV (5,5) è confermato; l'arrotondamento resta un valore ricavato dall'Excel.
        "SCORE_NOT_PLAYED_VOTE": os.environ.get("FANTAPG_NOT_PLAYED_VOTE", "5.5"),        # PDF: nessuna partita giocata (l'app web usa il voto SV della stagione)
        "SCORE_MISSING_MATCH_VOTE": os.environ.get("FANTAPG_MISSING_MATCH_VOTE", "5.5"),  # voto SV predefinito (5,5, confermato dallo staff); lo staff può cambiarlo per stagione da Staff → Punteggi
        "SCORE_DECIMALS": os.environ.get("FANTAPG_SCORE_DECIMALS", ""),                  # vuoto = nessun arrotondamento (Q4)
        "SCORE_DISPLAY_DECIMALS": int(os.environ.get("FANTAPG_SCORE_DISPLAY_DECIMALS", "3")),  # solo per mostrare i numeri
        "ALLOW_SINGLE_ENTRY": _flag("FANTAPG_ALLOW_SINGLE_ENTRY", "1"),                   # convalida di una sola compilazione
        "PLAYERS_PER_PAGE": 40,
        "AUDIT_PER_PAGE": 50,
    }
    if overrides:
        cfg.update(overrides)
    if not cfg.get("SECRET_KEY"):
        cfg["SECRET_KEY"] = _load_or_create_secret(instance)
    return cfg
