# FantaRF

Webapp per il fantacalcio del GDR **RoleFootball / Progetto Football Manager**: i partecipanti compongono la formazione
settimanale, lo staff inserisce i voti, l'app calcola i punteggi e produce classifiche e strike.

**Stato: Fase 4 in corso** — fondamenta, utenti, stagioni, listone, concorsi e formazioni, voti e punteggi,
classifica generale e strike sono funzionanti. Alcune parti della Fase 4 (premi, regole di stagione, squadre del GDR)
hanno la logica pronta ma **non ancora una pagina nello staff**: vedi [Cosa manca](#cosa-manca).

## Cosa funziona oggi

**Accesso e ruoli**
- Account creati dallo staff, password con hash, cambio password obbligatorio al primo accesso.
- Ruoli *partecipante* e *staff*; tutta l'area `/staff` è protetta lato server.
- Protezione CSRF, header di sicurezza (CSP, `X-Frame-Options`…), blocco temporaneo dopo tentativi di login falliti.

**Stagioni e listone**
- Stagioni (una sola attiva, garantito dal database).
- Importazione del listone da Excel: riconoscimento colonne (o associazione manuale), anteprima, report degli scarti
  (`#N/A`, `#REF!`, Serie C, ID duplicati…), differenze rispetto al listone attuale, conferma atomica.
- Storico a versioni dei dati dei giocatori: un trasferimento non riscrive il passato.
- Listone consultabile con ricerca (senza accenti), filtri per serie/ruolo/squadra, paginazione, uso da smartphone.
- **Stemmi delle squadre**: basta mettere un file in `static/crests/` col nome della squadra (`hellas-verona.png`);
  niente codice, niente riavvio. Formati: `.svg`, `.png`, `.webp`.

**Concorsi e formazioni**
- Concorsi (staff): bozza → apertura → chiusura manuale, scadenza configurabile per concorso (proposta: lunedì 20:30),
  informazioni e partite della settimana, riapertura eccezionale con motivo obbligatorio e traccia.
  Alla scadenza il concorso si chiude da solo (nessun processo in background: l'orario si confronta a ogni richiesta).
- Formazione (partecipanti): selettore guidato con ricerca e filtri, regole in tempo reale (3 di Serie A, 3 di Serie B,
  squadre diverse), scelte non valide bloccate *con il motivo scritto*, nome del FantaTeam facoltativo.
- Validazione sempre ripetuta dal server nella stessa transazione che scrive: nessun invio oltre la scadenza.
- Ogni invio valido crea una versione immutabile (lo impedisce anche il database); conta l'ultima.
- Staff: tutte le formazioni, chi non ha ancora inviato, storico delle versioni, esclusione/riammissione di un utente dalla stagione.

**Voti e punteggi** (staff)
- Compilazione dei voti da parte di **due persone in modo indipendente**: se coincidono il dato si conferma da solo,
  se differiscono si risolve il conflitto con un motivo.
- Calcolo con valori esatti (frazioni, niente virgola mobile) e spiegazione riga per riga; voto inserito a mano dichiarato come tale.
- Pubblicazione; **correzioni dopo la pubblicazione** con anteprima degli effetti prima di applicarle.
- Pesi di bonus/malus per stagione e **voto SV** (partita senza voto, standard 5,5) modificabili da **Staff → Punteggi**; il voto SV vale anche per chi non gioca nessuna partita.
- Il partecipante, a concorso pubblicato, vede il proprio punteggio e come si è calcolato ogni voto.

**Classifiche** (`/classifica`, pubbliche)
- Classifica settimanale con premi (20.000 / 10.000 / 5.000) e spareggio configurabile (ora d'invio dell'ultima versione o del primo invio).
- Classifica generale a punti (25-20-16-13-10-8-6-4-2-1) con *countback* stile Formula 1; chi salta un concorso prende 0 punti per quella settimana.
- Classifica a squadre del GDR: ogni settimana porta punti solo il miglior partecipante di ogni squadra.
- Premio fedeltà (50.000): idoneo chi ha una giocata valida in **ogni** concorso svolto e meno di 3 strike.
- Consultabile per stagione (`?stagione=`). Con pari merito su posizioni che pagano, il premio **non** viene assegnato in automatico: resta «da decidere» allo staff.

**Strike**
- Lo staff li assegna con motivo obbligatorio (`/staff/strike`); non si cancellano mai: uno strike sbagliato si **annulla** e resta nello storico.
- Il partecipante vede i propri strike in `/strike`. L'assenza da un concorso non genera strike (toglie solo il premio fedeltà).

**Amministrazione**
- Registro di tutte le operazioni amministrative (`/staff/registro`).
- Backup del database da terminale, controllo di salute `/healthz`.

## Cosa manca

La logica c'è in `services/`, ma non è ancora collegata a una pagina dello staff:

- **Regole di stagione** (scale di punti, premi, soglia strike, spareggio): oggi valgono i default del regolamento S13; `services/rules.py` sa già salvarle.
- **Squadre del GDR dei partecipanti** (`services/clubs.py`): la classifica a squadre compare solo se la scala è confermata, e per ora non c'è modo di assegnare le squadre dall'interfaccia.
- **Accredito dei premi** (`credit_award` / `uncredit_award` in `services/standings.py`).

Parametri che il regolamento non definisce (spareggio, scala a squadre, pari merito nella generale)
sono tenuti come **parametri configurabili**, non come costanti nascoste.

## Avvio in locale

Serve Python 3.10 o superiore. Il repository **è** il pacchetto Python, quindi la cartella deve chiamarsi `FantaRF`
(è il nome che Flask usa per gli import).

```bash
git clone https://github.com/lucamarchetti-dev/FantaRF.git
cd FantaRF
python3 -m venv .venv               # Windows: python -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\activate
pip install -r requirements.txt

flask --app . create-user --username admin --display-name "Il tuo nome" --role staff
flask --app . create-season S13 --activate
flask --app . run                   # http://127.0.0.1:5000
```

Poi accedi, apri **Staff → Importa il listone** e carica `Elenco_Giocatori_Fantacalcio.xlsx` (foglio *Consultazione*).
Le migrazioni del database (`migrations/*.sql`) si applicano da sole all'avvio; `flask --app . init-db` le esegue a mano.

### Comandi da terminale

| Comando | Cosa fa |
|---|---|
| `create-user` | Crea un account (`--role staff` o `user`). |
| `create-season NOME [--activate]` | Crea una stagione. |
| `import-listone FILE --season S13 [--apply]` | Importa il listone; senza `--apply` mostra solo l'anteprima. |
| `crests-list [--solo-mancanti]` | Elenca le squadre della stagione attiva e lo stemma che ognuna si aspetta. |
| `backup-db DESTINAZIONE` | Copia coerente del database, anche con l'app in uso. |
| `init-db` | Applica le migrazioni. |

Tutti si lanciano come `flask --app . <comando>`.

## Primo concorso

1. **Staff → Stagioni**: crea e attiva la stagione; **Importa il listone**.
2. **Staff → Utenti**: crea gli account dei partecipanti (password provvisoria mostrata una sola volta).
3. **Staff → Concorsi → Nuovo concorso**: numero, scadenza (già proposta), informazioni. Nasce in **bozza**, invisibile ai partecipanti.
4. **Apri il concorso**: i partecipanti lo vedono in home e compongono la formazione.
5. Alla scadenza si chiude da solo. «Chiudi ora» e «Riapri» (eccezionale, con motivo) sono nella pagina del concorso.

Il vincolo «un giocatore per ruolo» è una casella per concorso, **spenta di default** finché lo staff non conferma che è obbligatorio.
Il selettore delle formazioni richiede JavaScript.

## Risultati di un concorso

1. A concorso chiuso: **Staff → Concorsi → il concorso → Risultati → Avvia i risultati**.
2. **Compila i voti**: ogni persona dello staff compila *il proprio* foglio (voto base, bonus, malus, oppure «SV» = non ha giocato) senza vedere gli altri.
3. **Verifica e conflitti**: dove due compilazioni coincidono il dato è confermato; dove differiscono si sceglie la giusta o si inseriscono i valori del referto, con un motivo.
4. **Calcola i punteggi**, controlla, poi **Pubblica**. Prima della pubblicazione i partecipanti non vedono nulla.
5. Errore scoperto dopo? **Dati e correzioni**: correggi con un motivo, guarda l'anteprima prima/dopo e conferma il ricalcolo.
   Fino ad allora i partecipanti vedono i punteggi già pubblicati.

## Configurazione (variabili d'ambiente)

| Variabile | Default | Significato |
|---|---|---|
| `FANTAPG_SECRET_KEY` | generata in `instance/secret_key` | Chiave per firmare le sessioni. **In produzione impostala tu.** |
| `FANTAPG_DATABASE` | `instance/fantapg.sqlite3` | Percorso del database. |
| `FANTAPG_UPLOAD_DIR` | `instance/uploads` | File Excel caricati. |
| `FANTAPG_INSTANCE_PATH` | `./instance` | Cartella dei dati locali. |
| `FANTAPG_SECURE_COOKIES` | `0` | `1` se il sito è in HTTPS (cookie `Secure` + HSTS). |
| `FANTAPG_TRUST_PROXY` | `0` | `1` dietro a un reverse proxy fidato (usa `X-Forwarded-*`). |
| `FANTAPG_MAX_UPLOAD_MB` | `8` | Dimensione massima dei file caricati. |
| `FANTAPG_MAX_IMPORT_ROWS` | `20000` | Righe massime importabili da un Excel. |
| `FANTAPG_MIN_PASSWORD_LENGTH` | `10` | Lunghezza minima delle password. |
| `FANTAPG_LOGIN_MAX_FAILURES` / `FANTAPG_LOGIN_WINDOW_MINUTES` | `5` / `15` | Blocco temporaneo dopo tentativi falliti. |
| `FANTAPG_TIMEZONE` | `Europe/Rome` | Fuso orario di visualizzazione e inserimento delle scadenze. |
| `FANTAPG_DEADLINE_WEEKDAY` / `FANTAPG_DEADLINE_TIME` | `0` / `20:30` | Scadenza *proposta* per i nuovi concorsi (0 = lunedì). Modificabile per concorso. |
| `FANTAPG_DEFAULT_ONE_PER_ROLE` | `0` | Valore iniziale di «un giocatore per ruolo» nei nuovi concorsi. |
| `FANTAPG_NOT_PLAYED_VOTE` | `5.5` | Non più usata dall'app web: il voto di chi non gioca nessuna partita segue il voto SV della stagione (Staff → Punteggi). |
| `FANTAPG_MISSING_MATCH_VOTE` | `5.5` | Voto SV **predefinito**, valido finché lo staff non ne sceglie un altro per la stagione da Staff → Punteggi. |
| `FANTAPG_SCORE_DECIMALS` | vuoto | Vuoto = valori esatti. Un numero arrotonda il voto a quei decimali. |
| `FANTAPG_SCORE_DISPLAY_DECIMALS` | `3` | Decimali mostrati a video (non cambia i valori salvati). |
| `FANTAPG_ALLOW_SINGLE_ENTRY` | `1` | `0` per esigere sempre due compilazioni indipendenti. |

Nessuna credenziale è nel codice. `instance/` (database, chiave, upload) è esclusa da git.

## Backup e ripristino

```bash
flask --app . backup-db backup-AAAA-MM-GG.sqlite3
```

Ripristino: ferma l'app e sostituisci il file indicato da `FANTAPG_DATABASE` con la copia.
Conserva anche `instance/uploads/` se vuoi poter riaprire i report delle importazioni.

## Messa online

Il server di sviluppo di Flask **non** va usato in produzione. Servi l'app con un server WSGI
(es. `gunicorn 'FantaRF:create_app()'`, oppure `waitress` su Windows) dietro HTTPS, con `FANTAPG_SECRET_KEY`,
`FANTAPG_SECURE_COOKIES=1` e, se c'è un proxy, `FANTAPG_TRUST_PROXY=1`.
Un solo processo di scrittura alla volta è sufficiente per questa utenza (SQLite).

## Struttura

```
FantaRF/
  domain/       regole pure, senza web né database: listone, formazione, stato dei concorsi,
                motore di calcolo esatto (scoring), classifiche e premi (ranking), lettura Excel
  services/     logica applicativa e accesso ai dati: utenti, stagioni, importazione, giocatori, concorsi,
                formazioni, risultati, pesi, ammissione, regole, squadre, classifiche, strike, audit
  views/        rotte web: main, auth, players, contests, standings, strikes, staff*, staff_contests,
                staff_results, staff_strikes
  migrations/   schema SQL versionato (001 iniziale · 002 concorsi · 003 risultati · 004 classifiche e strike · 005 voto SV)
  templates/    pagine Jinja
  static/       CSS, JavaScript, stemmi delle squadre (crests/)
  crests.py     catalogo degli stemmi
  config.py     configurazione da variabili d'ambiente
```

## Tecnologie

Python · Flask 3 · SQLite · openpyxl (lettura Excel) · Jinja2, CSS e JavaScript senza framework.