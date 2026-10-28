"""Stemmi delle squadre.

Ogni squadra può avere il suo stemma: basta mettere un file in FantaRF/static/crests/ con il nome della squadra
"semplificato" (minuscolo, senza accenti, spazi e simboli sostituiti da un trattino):

    "Hellas Verona"  ->  hellas-verona.svg   (oppure .png, .webp)

Se il file non c'è, le pagine mostrano al suo posto uno spazio vuoto tratteggiato. Aggiungere o togliere un file
NON richiede modifiche al codice né un riavvio: la cartella viene riletta quando cambia.
"""
from __future__ import annotations

import os
import re
import unicodedata

from flask import current_app, url_for

EXTENSIONS = (".svg", ".png", ".webp")      # in ordine di preferenza se ce n'è più di uno per la stessa squadra
FOLDER_NAME = "crests"


def slugify(name: str | None) -> str:
    """'Hellas Verona' -> 'hellas-verona'. Solo a-z, 0-9 e trattini: è anche ciò che rende sicuro il nome del file."""
    text = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


class CrestCatalog:
    """Elenco dei file presenti in static/crests/, riletto solo quando la cartella cambia."""

    def __init__(self, folder: str):
        self.folder = folder
        self._stamp = None
        self._files: dict[str, str] = {}          # slug (minuscolo) -> nome reale del file

    def files(self) -> dict[str, str]:
        try:
            stamp = os.stat(self.folder).st_mtime_ns
        except OSError:                           # cartella assente: nessuno stemma, nessun errore
            self._stamp, self._files = None, {}
            return self._files
        if stamp != self._stamp:
            found: dict[str, tuple[int, str]] = {}
            for entry in os.listdir(self.folder):
                stem, ext = os.path.splitext(entry)
                ext = ext.lower()
                if ext not in EXTENSIONS or not os.path.isfile(os.path.join(self.folder, entry)):
                    continue
                rank = EXTENSIONS.index(ext)
                key = stem.lower()
                if key not in found or rank < found[key][0]:
                    found[key] = (rank, entry)
            self._files = {k: v[1] for k, v in found.items()}
            self._stamp = stamp
        return self._files

    def filename_for(self, team_name: str | None) -> str | None:
        slug = slugify(team_name)
        return self.files().get(slug) if slug else None


def init_app(app) -> None:
    app.extensions["crests"] = CrestCatalog(os.path.join(app.static_folder or "", FOLDER_NAME))
    app.jinja_env.globals["crest_url"] = crest_url


def catalog() -> CrestCatalog:
    return current_app.extensions["crests"]


def crest_url(team_name: str | None) -> str:
    """Indirizzo dello stemma della squadra, oppure stringa vuota se il file non c'è ancora."""
    filename = catalog().filename_for(team_name)
    return url_for("static", filename=f"{FOLDER_NAME}/{filename}") if filename else ""
