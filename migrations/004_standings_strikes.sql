-- Fase 4: regole stagione, squadre GDR, strike e accrediti premi.
CREATE TABLE season_rules (
    season_id INTEGER PRIMARY KEY REFERENCES seasons(id),
    rules_json TEXT NOT NULL,
    updated_by INTEGER REFERENCES users(id),
    updated_at TEXT NOT NULL
);

CREATE TABLE user_teams (
    season_id INTEGER NOT NULL REFERENCES seasons(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    team_name TEXT NOT NULL,
    updated_by INTEGER REFERENCES users(id),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (season_id, user_id)
);
CREATE INDEX idx_user_teams_name ON user_teams(season_id, team_name COLLATE NOCASE);

CREATE TABLE strikes (
    id INTEGER PRIMARY KEY,
    season_id INTEGER NOT NULL REFERENCES seasons(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    contest_id INTEGER REFERENCES contests(id),
    reason TEXT NOT NULL,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL,
    voided_at TEXT,
    voided_by INTEGER REFERENCES users(id),
    void_reason TEXT
);
CREATE INDEX idx_strikes_season_user ON strikes(season_id, user_id, voided_at);

CREATE TABLE credited_awards (
    id INTEGER PRIMARY KEY,
    season_id INTEGER NOT NULL REFERENCES seasons(id),
    contest_id INTEGER NOT NULL DEFAULT 0,
    user_id INTEGER NOT NULL REFERENCES users(id),
    kind TEXT NOT NULL CHECK (kind IN ('weekly', 'general', 'loyalty')),
    position INTEGER,
    amount INTEGER NOT NULL,
    credited_by INTEGER REFERENCES users(id),
    credited_at TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    UNIQUE (season_id, contest_id, user_id, kind)
);
