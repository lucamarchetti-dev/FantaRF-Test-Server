-- Squadra che un partecipante aveva in un concorso GIÀ PUBBLICATO, fissata nel momento in cui l'associazione
-- (user_teams) cambia o viene tolta: così un cambio di squadra non riscrive la classifica a squadre dei concorsi passati.
-- Se per (concorso, utente) non c'è una riga, vale la squadra corrente di user_teams. Vedi services/clubs.py.
CREATE TABLE contest_teams (
    contest_id INTEGER NOT NULL REFERENCES contests(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    team_name TEXT NOT NULL,
    frozen_at TEXT NOT NULL,
    PRIMARY KEY (contest_id, user_id)
);
