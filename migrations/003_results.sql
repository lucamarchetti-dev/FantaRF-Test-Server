-- Fase 3: voti, statistiche, punteggi. I decimali sono salvati come TESTO canonico (mai in virgola mobile).

ALTER TABLE contests ADD COLUMN matches_count INTEGER NOT NULL DEFAULT 2 CHECK (matches_count BETWEEN 1 AND 4);
-- Aumenta a ogni modifica di dati che incidono sui punteggi; calculated_version dice a quale versione si riferiscono
-- i punteggi salvati: se differiscono, i punteggi sono "vecchi" e vanno ricalcolati.
ALTER TABLE contests ADD COLUMN data_version INTEGER NOT NULL DEFAULT 0;
ALTER TABLE contests ADD COLUMN calculated_version INTEGER;
ALTER TABLE contests ADD COLUMN published_at TEXT;

-- Pesi bonus/malus per stagione (default: tabella del regolamento, nel codice di dominio).
CREATE TABLE scoring_weights (
    season_id    INTEGER NOT NULL REFERENCES seasons (id),
    ruolo        TEXT NOT NULL CHECK (ruolo IN ('P', 'D', 'T', 'C', 'W', 'A')),
    bonus_label  TEXT NOT NULL,
    bonus_weight TEXT NOT NULL,
    malus_label  TEXT NOT NULL,
    malus_weight TEXT NOT NULL,
    PRIMARY KEY (season_id, ruolo)
);

-- Compilazioni INDEPENDENTI dei membri dello staff (doppia immissione): una per persona e cella.
CREATE TABLE vote_entries (
    id          INTEGER PRIMARY KEY,
    contest_id  INTEGER NOT NULL REFERENCES contests (id),
    player_id   INTEGER NOT NULL REFERENCES players (id),
    match_no    INTEGER NOT NULL CHECK (match_no BETWEEN 1 AND 4),
    entered_by  INTEGER NOT NULL REFERENCES users (id),
    played      INTEGER NOT NULL CHECK (played IN (0, 1)),
    base_vote   TEXT,
    bonus_count INTEGER NOT NULL DEFAULT 0 CHECK (bonus_count >= 0),
    malus_count INTEGER NOT NULL DEFAULT 0 CHECK (malus_count >= 0),
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    UNIQUE (contest_id, player_id, match_no, entered_by),
    CHECK ((played = 1 AND base_vote IS NOT NULL) OR (played = 0 AND base_vote IS NULL AND bonus_count = 0 AND malus_count = 0))
);

-- Dato VERIFICATO per giocatore/partita: quello che usa il calcolo.
--   confirmed = due compilazioni indipendenti concordano; single = convalidata una sola compilazione;
--   resolved = deciso dallo staff dopo un conflitto; corrected = modificato dopo la pubblicazione (con motivo).
CREATE TABLE match_stats (
    id          INTEGER PRIMARY KEY,
    contest_id  INTEGER NOT NULL REFERENCES contests (id),
    player_id   INTEGER NOT NULL REFERENCES players (id),
    match_no    INTEGER NOT NULL CHECK (match_no BETWEEN 1 AND 4),
    played      INTEGER NOT NULL CHECK (played IN (0, 1)),
    base_vote   TEXT,
    bonus_count INTEGER NOT NULL DEFAULT 0 CHECK (bonus_count >= 0),
    malus_count INTEGER NOT NULL DEFAULT 0 CHECK (malus_count >= 0),
    status      TEXT NOT NULL CHECK (status IN ('confirmed', 'single', 'resolved', 'corrected')),
    note        TEXT,
    decided_by  INTEGER REFERENCES users (id),
    decided_at  TEXT NOT NULL,
    UNIQUE (contest_id, player_id, match_no),
    CHECK ((played = 1 AND base_vote IS NOT NULL) OR (played = 0 AND base_vote IS NULL AND bonus_count = 0 AND malus_count = 0))
);

-- Ogni decisione manuale sui dati verificati lascia una traccia con il valore prima e dopo.
CREATE TABLE stat_changes (
    id         INTEGER PRIMARY KEY,
    contest_id INTEGER NOT NULL REFERENCES contests (id),
    player_id  INTEGER NOT NULL REFERENCES players (id),
    match_no   INTEGER NOT NULL,
    user_id    INTEGER REFERENCES users (id),
    action     TEXT NOT NULL,
    reason     TEXT,
    old_json   TEXT,
    new_json   TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Voto finale inserito A MANO (es. partita rinviata, caso non previsto): la provenienza resta dichiarata.
CREATE TABLE player_overrides (
    contest_id INTEGER NOT NULL REFERENCES contests (id),
    player_id  INTEGER NOT NULL REFERENCES players (id),
    vote       TEXT NOT NULL,
    reason     TEXT NOT NULL,
    user_id    INTEGER REFERENCES users (id),
    created_at TEXT NOT NULL,
    PRIMARY KEY (contest_id, player_id)
);

CREATE TABLE player_scores (
    contest_id     INTEGER NOT NULL REFERENCES contests (id),
    player_id      INTEGER NOT NULL REFERENCES players (id),
    ruolo          TEXT NOT NULL,
    vote           TEXT NOT NULL,
    source         TEXT NOT NULL CHECK (source IN ('computed', 'manual')),
    breakdown_json TEXT NOT NULL,
    PRIMARY KEY (contest_id, player_id)
);

CREATE TABLE lineup_scores (
    contest_id INTEGER NOT NULL REFERENCES contests (id),
    lineup_id  INTEGER NOT NULL REFERENCES lineups (id),
    version_id INTEGER NOT NULL REFERENCES lineup_versions (id),
    total      TEXT,                 -- NULL = formazione incompleta (mancano voti)
    PRIMARY KEY (contest_id, lineup_id)
);

CREATE TABLE score_runs (
    id           INTEGER PRIMARY KEY,
    contest_id   INTEGER NOT NULL REFERENCES contests (id),
    kind         TEXT NOT NULL CHECK (kind IN ('start', 'calculate', 'publish', 'recalculate')),
    user_id      INTEGER REFERENCES users (id),
    data_version INTEGER,
    summary_json TEXT,
    created_at   TEXT NOT NULL
);
