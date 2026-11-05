-- Squadre dei partecipanti: storicizzazione per concorso.
-- user_teams (migrazione 004) resta l'associazione CORRENTE partecipante -> squadra, per stagione.
-- contest_teams conserva la squadra che un partecipante aveva in un concorso GIA' PUBBLICATO quando la sua
-- associazione viene cambiata o tolta: cosi' la classifica a squadre dei concorsi passati non si riscrive.
-- Nessun dato esistente viene toccato: senza righe qui vale l'associazione corrente.
CREATE TABLE contest_teams (
    contest_id INTEGER NOT NULL REFERENCES contests(id),
    user_id    INTEGER NOT NULL REFERENCES users(id),
    team_name  TEXT NOT NULL,
    frozen_at  TEXT NOT NULL,
    PRIMARY KEY (contest_id, user_id)
);
