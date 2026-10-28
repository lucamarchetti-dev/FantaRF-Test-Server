-- Voto assegnato a una partita in cui il giocatore è «SV» (senza voto), modificabile da Staff → Punteggi.
-- Testo canonico (mai virgola mobile). NULL = nessuna scelta dello staff: vale il valore predefinito dell'app (5,5).
ALTER TABLE seasons ADD COLUMN sv_vote TEXT;
