"""Test di fumo: l'app parte, il bootstrap prepara il DB e le pagine principali rispondono.

Non sostituisce i test del regolamento: verifica solo che ciò che andrà online si avvii davvero.
Si lancia dalla cartella che CONTIENE il pacchetto:  python -m FantaRF.deploy.smoke_test
"""
from __future__ import annotations

import os
import re
import sys
import tempfile

ADMIN, PWD = "smoke-admin", "Smoke-Test-12345"
failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("  ok   " if cond else "  FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="fantarf-smoke-")
    os.environ.update({
        "FANTAPG_INSTANCE_PATH": tmp,
        "FANTAPG_DATABASE": os.path.join(tmp, "smoke.sqlite3"),
        "FANTAPG_UPLOAD_DIR": os.path.join(tmp, "uploads"),
        "FANTAPG_SECRET_KEY": "smoke-test-key",
        "FANTAPG_BOOTSTRAP_ADMIN_USER": ADMIN,
        "FANTAPG_BOOTSTRAP_ADMIN_PASSWORD": PWD,
    })
    from .. import create_app
    from . import bootstrap

    print("Bootstrap")
    check("bootstrap termina senza errori", bootstrap.main() == 0)
    check("bootstrap ripetuto (idempotente)", bootstrap.main() == 0)

    app = create_app({"TESTING": True})
    client = app.test_client()

    print("Pagine pubbliche")
    r = client.get("/healthz")
    check("/healthz → 200 'ok'", r.status_code == 200 and r.data.strip() == b"ok")
    check("/ → 200", client.get("/").status_code == 200)
    check("/login → 200", client.get("/login").status_code == 200)
    check("/giocatori → 200", client.get("/giocatori").status_code == 200)
    check("/classifica/ → 200", client.get("/classifica/").status_code == 200)
    from .. import db as dbm
    from ..services import players as players_svc
    from ..services import seasons as seasons_svc
    conn = dbm.connect(app.config["DATABASE"])
    try:
        season = seasons_svc.active_season(conn)
        stats = players_svc.season_stats(conn, season["id"]) if season else {"selectable": 0}
    finally:
        conn.close()
    check("stagione attiva con giocatori selezionabili nel listone", stats["selectable"] > 0,
          f"selezionabili={stats['selectable']}")

    print("Protezione dello staff")
    r = client.get("/staff/", follow_redirects=False)
    check("/staff/ senza login non è accessibile", r.status_code in (301, 302, 303, 401, 403))

    print("Login")
    page = client.get("/login").get_data(as_text=True)
    token = re.search(r'name="_csrf" value="([^"]+)"', page)
    check("il modulo di login contiene il token CSRF", token is not None)
    if token:
        bad = client.post("/login", data={"username": ADMIN, "password": "sbagliata-sbagliata", "_csrf": token.group(1)})
        check("password errata → rifiutata", bad.status_code == 401)
        ok = client.post("/login", data={"username": ADMIN, "password": PWD, "_csrf": token.group(1)})
        check("password giusta → accettata", ok.status_code == 302)
        r = client.get("/staff/", follow_redirects=False)
        check("primo accesso → cambio password obbligatorio", r.status_code == 302 and "/password" in r.headers.get("Location", ""))

    print()
    if failures:
        print(f"{len(failures)} controlli falliti: " + "; ".join(failures))
        return 1
    print("Tutti i controlli superati.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
