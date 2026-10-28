-- Fase 2: concorsi, formazioni inviate (a versioni), esclusioni dalla stagione.

CREATE TABLE contests (
    id           INTEGER PRIMARY KEY,
    season_id    INTEGER NOT NULL REFERENCES seasons (id),
    number       INTEGER NOT NULL CHECK (number > 0),
    name         TEXT NOT NULL,
    -- 'calculating' e 'published' sono riservati alla Fase 3; la chiusura per scadenza
    -- NON viene scritta qui: lo stato effettivo si calcola da status + closes_at.
    status       TEXT NOT NULL DEFAULT 'draft'
                 CHECK (status IN ('draft', 'open', 'closed', 'calculating', 'published')),
    opens_at     TEXT,
    closes_at    TEXT NOT NULL,
    info         TEXT NOT NULL DEFAULT '',
    -- Vincolo "un giocatore per ruolo": non ancora confermato dallo staff, quindi per concorso e spento di default.
    one_per_role INTEGER NOT NULL DEFAULT 0 CHECK (one_per_role IN (0, 1)),
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    UNIQUE (season_id, number)
);

-- Ogni riapertura eccezionale e' registrata con motivo e autore.
CREATE TABLE contest_reopenings (
    id               INTEGER PRIMARY KEY,
    contest_id       INTEGER NOT NULL REFERENCES contests (id),
    user_id          INTEGER REFERENCES users (id),
    old_closes_at    TEXT NOT NULL,
    new_closes_at    TEXT NOT NULL,
    reason           TEXT NOT NULL,
    created_at       TEXT NOT NULL
);

CREATE TABLE season_exclusions (
    season_id  INTEGER NOT NULL REFERENCES seasons (id),
    user_id    INTEGER NOT NULL REFERENCES users (id),
    reason     TEXT NOT NULL,
    created_by INTEGER REFERENCES users (id),
    created_at TEXT NOT NULL,
    PRIMARY KEY (season_id, user_id)
);

-- Una giocata per utente e concorso; le sue versioni sono immutabili.
CREATE TABLE lineups (
    id         INTEGER PRIMARY KEY,
    contest_id INTEGER NOT NULL REFERENCES contests (id),
    user_id    INTEGER NOT NULL REFERENCES users (id),
    created_at TEXT NOT NULL,
    UNIQUE (contest_id, user_id)
);

CREATE TABLE lineup_versions (
    id              INTEGER PRIMARY KEY,
    lineup_id       INTEGER NOT NULL REFERENCES lineups (id),
    version         INTEGER NOT NULL CHECK (version > 0),
    fanta_team_name TEXT,
    submitted_at    TEXT NOT NULL,
    UNIQUE (lineup_id, version)
);

-- Istantanea dei dati usati per la validazione: resta com'era anche se il listone cambia.
CREATE TABLE lineup_players (
    id        INTEGER PRIMARY KEY,
    version_id INTEGER NOT NULL REFERENCES lineup_versions (id),
    position  INTEGER NOT NULL CHECK (position BETWEEN 1 AND 6),
    psd_id    INTEGER NOT NULL REFERENCES player_season_data (id),
    player_id INTEGER NOT NULL REFERENCES players (id),
    full_name TEXT NOT NULL,
    serie     TEXT NOT NULL CHECK (serie IN ('A', 'B')),
    ruolo     TEXT NOT NULL CHECK (ruolo IN ('P', 'D', 'T', 'C', 'W', 'A')),
    team_name TEXT NOT NULL,
    UNIQUE (version_id, position),
    UNIQUE (version_id, player_id)
);
CREATE INDEX idx_lineup_players_player ON lineup_players (player_id);

-- Lo storico non si riscrive: nemmeno per errore o da codice futuro.
CREATE TRIGGER lineup_versions_no_update BEFORE UPDATE ON lineup_versions
BEGIN SELECT RAISE(ABORT, 'lineup_versions e'' immutabile'); END;
CREATE TRIGGER lineup_versions_no_delete BEFORE DELETE ON lineup_versions
BEGIN SELECT RAISE(ABORT, 'lineup_versions e'' immutabile'); END;
CREATE TRIGGER lineup_players_no_update BEFORE UPDATE ON lineup_players
BEGIN SELECT RAISE(ABORT, 'lineup_players e'' immutabile'); END;
CREATE TRIGGER lineup_players_no_delete BEFORE DELETE ON lineup_players
BEGIN SELECT RAISE(ABORT, 'lineup_players e'' immutabile'); END;
