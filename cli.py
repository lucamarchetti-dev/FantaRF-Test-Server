"""Comandi da terminale: flask --app fantapg <comando>."""
from __future__ import annotations

import os
import shutil

import click
from flask import current_app

from . import db as dbm
from .domain.excel_reader import ImportFileError
from .services import imports as imports_svc
from .services import seasons as seasons_svc
from .services import users as users_svc


def register(app) -> None:
    @app.cli.command("init-db")
    def init_db():
        """Applica le migrazioni del database (idempotente)."""
        conn = dbm.connect(current_app.config["DATABASE"])
        try:
            applied = dbm.migrate(conn)
        finally:
            conn.close()
        click.echo("Migrazioni applicate: " + (", ".join(applied) if applied else "nessuna (già aggiornato)"))

    @app.cli.command("create-user")
    @click.option("--username", prompt=True)
    @click.option("--display-name", prompt="Nome visualizzato")
    @click.option("--role", type=click.Choice(["user", "staff"]), default="staff", show_default=True)
    @click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
    @click.option("--must-change/--no-must-change", default=False, show_default=True,
                  help="Richiede il cambio password al primo accesso.")
    def create_user(username, display_name, role, password, must_change):
        """Crea un account (utile per il primo account staff)."""
        from .security import password_problem
        problem = password_problem(password, current_app.config["MIN_PASSWORD_LENGTH"])
        if problem:
            raise click.ClickException(problem)
        conn = dbm.connect(current_app.config["DATABASE"])
        try:
            users_svc.create_user(conn, username=username, display_name=display_name, role=role,
                                  password=password, actor_id=None, must_change=must_change)
        except users_svc.UserError as exc:
            raise click.ClickException(str(exc))
        finally:
            conn.close()
        click.echo(f"Account «{username}» ({role}) creato.")

    @app.cli.command("create-season")
    @click.argument("name")
    @click.option("--activate", is_flag=True, help="Imposta subito la stagione come attiva.")
    def create_season(name, activate):
        """Crea una stagione (es. S13)."""
        conn = dbm.connect(current_app.config["DATABASE"])
        try:
            sid = seasons_svc.create_season(conn, name, None, None, None)
            if activate:
                seasons_svc.activate_season(conn, sid, None)
        except seasons_svc.SeasonError as exc:
            raise click.ClickException(str(exc))
        finally:
            conn.close()
        click.echo(f"Stagione «{name}» creata" + (" e attivata." if activate else "."))

    @app.cli.command("import-listone")
    @click.argument("file", type=click.Path(exists=True, dir_okay=False))
    @click.option("--season", "season_name", required=True, help="Nome della stagione di destinazione.")
    @click.option("--sheet", default="Consultazione", show_default=True)
    @click.option("--apply", "do_apply", is_flag=True, help="Applica davvero. Senza questo flag è solo un'anteprima.")
    def import_listone(file, season_name, sheet, do_apply):
        """Importa il listone da Excel (anteprima di default, --apply per confermare)."""
        conn = dbm.connect(current_app.config["DATABASE"])
        try:
            season = conn.execute("SELECT id FROM seasons WHERE name = ?", (season_name,)).fetchone()
            if season is None:
                raise click.ClickException(f"Stagione «{season_name}» inesistente: creala con create-season.")
            with open(file, "rb") as fh:
                path, sha = imports_svc.store_upload(current_app.config["UPLOAD_DIR"], fh.read())
            try:
                batch_id = imports_svc.create_batch(conn, season_id=season["id"], user_id=None,
                                                    filename=os.path.basename(file), sheet=sheet, path=path, sha256=sha)
                batch = imports_svc.get_batch(conn, batch_id)
                preview = imports_svc.build_preview(conn, batch, current_app.config["MAX_IMPORT_ROWS"])
            except ImportFileError as exc:
                if os.path.exists(path):
                    os.remove(path)
                raise click.ClickException(str(exc))
            if preview.report is None:
                raise click.ClickException("Colonne obbligatorie non riconosciute: usa l'interfaccia web per associarle.")
            r, d = preview.report, preview.diff
            click.echo(f"Righe lette: {r.total_rows} | valide: {len(r.valid)} | scartate: {len(r.issues)}")
            for g_ in r.issues_by_code():
                click.echo(f"  - {g_['count']:4d}  {g_['label']}")
            click.echo(f"Nuovi: {len(d.new)} | modificati: {len(d.changed)} | invariati: {d.unchanged} | "
                       f"assenti dal file: {len(d.absent)}")
            if not do_apply:
                imports_svc.cancel_batch(conn, batch_id, None)
                click.echo("Anteprima: nessuna modifica. Aggiungi --apply per importare.")
                return
            summary = imports_svc.apply_batch(conn, batch_id, None)
            click.echo(f"Importazione applicata (lotto #{batch_id}): {summary['new']} nuovi, "
                       f"{summary['changed']} aggiornati.")
        finally:
            conn.close()

    @app.cli.command("backup-db")
    @click.argument("destination", type=click.Path(dir_okay=False))
    def backup_db(destination):
        """Copia coerente del database (anche con l'app in esecuzione)."""
        import sqlite3
        src = dbm.connect(current_app.config["DATABASE"])
        try:
            dst = sqlite3.connect(destination)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        click.echo(f"Backup creato: {destination}")

    @app.cli.command("crests-list")
    @click.option("--solo-mancanti", is_flag=True, help="Mostra soltanto le squadre senza stemma.")
    def crests_list(solo_mancanti):
        """Elenca le squadre della stagione attiva e il file di stemma che ognuna si aspetta (static/crests/)."""
        from . import crests as crests_mod
        from .services import players as players_svc
        conn = dbm.connect(current_app.config["DATABASE"])
        try:
            season = seasons_svc.active_season(conn)
            if season is None:
                raise click.ClickException("Nessuna stagione attiva: crea e attiva una stagione, poi importa il listone.")
            teams = players_svc.teams_in_season(conn, season["id"])
        finally:
            conn.close()
        cat = crests_mod.catalog()
        have = cat.files()
        rows, expected = [], set()
        for t in teams:
            slug = crests_mod.slugify(t["name"])
            expected.add(slug)
            rows.append((t["serie"], t["name"], slug, have.get(slug)))
        missing = [r for r in rows if r[3] is None]
        click.echo(f"Cartella stemmi: {cat.folder}")
        click.echo(f"Stagione {season['name']}: {len(rows)} squadre, stemmi presenti {len(rows) - len(missing)}, mancanti {len(missing)}.")
        click.echo("Formati accettati: " + ", ".join(crests_mod.EXTENSIONS) + "\n")
        shown = missing if solo_mancanti else rows
        width = max([len(r[1]) for r in shown] + [7])
        for serie, name, slug, found in shown:
            click.echo(f"  {serie}  {name.ljust(width)}  {found if found else slug + '.svg (o .png / .webp)  <-- MANCA'}")
        stray = sorted(f for slug, f in have.items() if slug not in expected)
        if stray:
            click.echo("\nFile che non corrispondono a nessuna squadra (nome sbagliato?): " + ", ".join(stray))
