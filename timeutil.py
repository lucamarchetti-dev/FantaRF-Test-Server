from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utcnow_iso() -> str:
    return iso(utcnow())


def minutes_ago_iso(minutes: int) -> str:
    return iso(utcnow() - timedelta(minutes=minutes))


def to_local(value: str | None, tz_name: str = "Europe/Rome") -> str:
    """Converte un orario ISO UTC in 'gg/mm/aaaa HH:MM' nel fuso configurato."""
    if not value:
        return "—"
    try:
        dt = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return value
    return dt.astimezone(ZoneInfo(tz_name)).strftime("%d/%m/%Y %H:%M")


# ---------------------------------------------------------------- Fase 2

WEEKDAYS_IT = ("lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica")
MONTHS_IT = ("gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto",
             "settembre", "ottobre", "novembre", "dicembre")


def parse_iso(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def local_to_utc(text: str, tz_name: str = "Europe/Rome") -> datetime:
    """'2026-10-12T20:30' (ora locale) -> datetime UTC. ValueError se il formato o l'orario non sono validi."""
    text = (text or "").strip().replace(" ", "T")
    try:
        naive = datetime.strptime(text, "%Y-%m-%dT%H:%M")
    except ValueError as exc:
        raise ValueError("Data e ora non valide (formato atteso: AAAA-MM-GGTHH:MM).") from exc
    tz = ZoneInfo(tz_name)
    aware = naive.replace(tzinfo=tz)
    utc = aware.astimezone(timezone.utc)
    # Orari inesistenti (salto dell'ora legale) non fanno il giro completo: si rifiutano.
    if utc.astimezone(tz).replace(tzinfo=None) != naive:
        raise ValueError("Quell'orario non esiste per il cambio dell'ora legale: scegline un altro.")
    return utc


def utc_to_local_input(value: str | None, tz_name: str = "Europe/Rome") -> str:
    if not value:
        return ""
    return parse_iso(value).astimezone(ZoneInfo(tz_name)).strftime("%Y-%m-%dT%H:%M")


def to_local_long(value: str | None, tz_name: str = "Europe/Rome") -> str:
    """'lunedì 12 ottobre 2026, ore 20:30'"""
    if not value:
        return "—"
    dt = parse_iso(value).astimezone(ZoneInfo(tz_name))
    return f"{WEEKDAYS_IT[dt.weekday()]} {dt.day} {MONTHS_IT[dt.month - 1]} {dt.year}, ore {dt:%H:%M}"
