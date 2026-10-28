"""Regole pure sui concorsi: stato effettivo, scadenza predefinita, testo del tempo residuo."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

STATUSES = ("draft", "open", "closed", "calculating", "published")
STATUS_LABELS = {
    "draft": "bozza", "scheduled": "apre a breve", "open": "aperto", "closed": "chiuso",
    "calculating": "in calcolo", "published": "pubblicato",
}


def effective_status(status: str, opens_at: datetime | None, closes_at: datetime, now: datetime) -> str:
    """Stato reale in questo istante. La chiusura alla scadenza NON richiede un processo in
    background: basta confrontare l'orario, quindi non puo' "dimenticarsi" di scattare.

    L'invio e' ammesso fino all'istante di scadenza compreso (now <= closes_at).
    """
    if status != "open":
        return status
    if opens_at is not None and now < opens_at:
        return "scheduled"
    if now > closes_at:
        return "closed"
    return "open"


def default_deadline(now: datetime, weekday: int, hour: int, minute: int, tz_name: str) -> datetime:
    """Prossima occorrenza (strettamente futura) di <giorno> alle HH:MM nel fuso indicato, in UTC.
    weekday: 0 = lunedi'."""
    tz = ZoneInfo(tz_name)
    local_now = now.astimezone(tz)
    days_ahead = (weekday - local_now.weekday()) % 7
    candidate = (local_now + timedelta(days=days_ahead)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= local_now:
        candidate += timedelta(days=7)
    # ricalcola l'offset del giorno di arrivo (ora legale) mantenendo l'ora locale richiesta
    candidate = datetime(candidate.year, candidate.month, candidate.day, hour, minute, tzinfo=tz)
    return candidate.astimezone(timezone.utc)


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def time_left_text(delta: timedelta) -> str:
    seconds = int(delta.total_seconds())
    if seconds <= 0:
        return "scaduto"
    if seconds < 60:
        return "meno di un minuto"
    minutes = seconds // 60
    if minutes < 60:
        return _plural(minutes, "minuto", "minuti")
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        text = _plural(hours, "ora", "ore")
        return text + (f" e {_plural(minutes, 'minuto', 'minuti')}" if minutes else "")
    days, hours = divmod(hours, 24)
    text = _plural(days, "giorno", "giorni")
    return text + (f" e {_plural(hours, 'ora', 'ore')}" if hours else "")
