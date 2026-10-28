-- Fanta PG - schema iniziale (Fase 1)
-- Tutti gli orari sono salvati in UTC, formato ISO 8601 (es. 2026-10-09T18:30:00Z).

CREATE TABLE users (
    id                   INTEGER PRIMARY KEY,
    username             TEXT NOT NULL COLLATE NOCASE UNIQUE,
    display_name         TEXT NOT NULL,
    password_hash        TEXT NOT NULL,
    role                 TEXT NOT NULL CHECK (role IN ('user', 'staff')),
    status               TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
    must_change_password INTEGER NOT NULL DEFAULT 0 CHECK (must_change_password IN (0, 1)),
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);

CREATE TABLE login_failures (
    id         INTEGER PRIMARY KEY,
    username   TEXT NOT NULL,
    ip         TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_login_failures_username ON login_failures (username COLLATE NOCASE, created_at);
CREATE INDEX idx_login_failures_ip ON login_failures (ip, created_at);

CREATE TABLE seasons (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL COLLATE NOCASE UNIQUE,
    status     TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'active', 'archived')),
    starts_on  TEXT,
    ends_on    TEXT,
    created_at TEXT NOT NULL
);
-- Al massimo una stagione attiva, garantito dal database.
CREATE UNIQUE INDEX idx_one_active_season ON seasons (status) WHERE status = 'active';

-- Squadre del GDR (Serie A / Serie B). Il nome e' l'identificativo naturale.
CREATE TABLE teams (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE
);

-- Anagrafica stabile del giocatore. external_id e' l'ID ufficiale del listone.
CREATE TABLE players (
    id          INTEGER PRIMARY KEY,
    external_id INTEGER NOT NULL UNIQUE,
    full_name   TEXT NOT NULL,
    search_name TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX idx_players_search ON players (search_name);

CREATE TABLE import_batches (
    id           INTEGER PRIMARY KEY,
    season_id    INTEGER NOT NULL REFERENCES seasons (id),
    user_id      INTEGER REFERENCES users (id),
    filename     TEXT NOT NULL,
    sheet        TEXT NOT NULL,
    mapping_json TEXT,
    file_path    TEXT,
    file_sha256  TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'preview'
                 CHECK (status IN ('preview', 'applied', 'cancelled', 'failed')),
    summary_json TEXT,
    error        TEXT,
    created_at   TEXT NOT NULL,
    applied_at   TEXT
);

-- Dati del giocatore validi per una stagione, con storico a versioni:
-- la riga corrente ha valid_to NULL; una modifica chiude la riga e ne apre una nuova.
-- Le formazioni delle fasi successive faranno riferimento a queste righe, cosi' un
-- trasferimento non altera i concorsi gia' chiusi.
CREATE TABLE player_season_data (
    id         INTEGER PRIMARY KEY,
    season_id  INTEGER NOT NULL REFERENCES seasons (id),
    player_id  INTEGER NOT NULL REFERENCES players (id),
    team_id    INTEGER NOT NULL REFERENCES teams (id),
    serie      TEXT NOT NULL CHECK (serie IN ('A', 'B')),
    ruolo      TEXT NOT NULL CHECK (ruolo IN ('P', 'D', 'T', 'C', 'W', 'A')),
    full_name  TEXT NOT NULL,
    selectable INTEGER NOT NULL DEFAULT 1 CHECK (selectable IN (0, 1)),
    valid_from TEXT NOT NULL,
    valid_to   TEXT,
    batch_id   INTEGER REFERENCES import_batches (id)
);
CREATE UNIQUE INDEX idx_psd_current ON player_season_data (season_id, player_id) WHERE valid_to IS NULL;
CREATE INDEX idx_psd_filters ON player_season_data (season_id, serie, ruolo, team_id) WHERE valid_to IS NULL;

CREATE TABLE audit_log (
    id           INTEGER PRIMARY KEY,
    user_id      INTEGER REFERENCES users (id),
    action       TEXT NOT NULL,
    entity       TEXT,
    entity_id    TEXT,
    details_json TEXT,
    created_at   TEXT NOT NULL
);
CREATE INDEX idx_audit_created ON audit_log (created_at DESC, id DESC);
