# Messa online per il test degli admin

## Due modi per metterlo online

| | **Demo su GitHub Pages** | **Server con database condiviso** (Render + Litestream) |
|---|---|---|
| Serve un server | No | Sì |
| Costo | Gratuito | Gratuito con limiti (vedi sotto) |
| Dati | **Ogni browser ha la propria copia**, privata | Un solo database, uguale per tutti gli admin |
| Adatto a | Far vedere e provare l'app, senza account né installazioni | Test condiviso: concorsi e voti inseriti da uno sono visti dagli altri |

Se gli admin devono lavorare **sugli stessi dati**, serve il server (sezione «Server» più sotto). Se bastano
una dimostrazione e una prova delle funzioni, la demo su Pages è la via più semplice.

## Demo su GitHub Pages

GitHub Pages pubblica solo file statici e non può eseguire Python: pubblicando il repository direttamente si vede
soltanto il README. La demo quindi porta **l'intera app Flask dentro il browser**: Python compilato in WebAssembly
([Pyodide](https://pyodide.org)), un *service worker* che intercetta i clic e i moduli, e il database SQLite
salvato nel browser (IndexedDB), già caricato con la stagione S13, il listone e un concorso aperto.

```
 browser ─► pagina guscio ─► iframe /app/ ─► service worker ─► worker Python (Flask + SQLite) ─► risposta HTML
                                                              └► IndexedDB (database e sessione)
```

### Attivazione (una tantum)

1. Carica sul repository i file di questo pacchetto (cartella `pages/`, `.github/workflows/pages.yml`, ecc.) e fai push su `main`.
2. Su GitHub: **Settings → Pages → Build and deployment → Source: «GitHub Actions»**.
   (Con «Deploy from a branch» si vede solo il README: è ciò che hai visto.)
3. Apri la scheda **Actions**: il workflow «Demo su GitHub Pages» costruisce e pubblica il sito. L'indirizzo compare nel
   riepilogo del job `Pubblica` (di solito `https://<utente>.github.io/<repository>/`).

### Come si usa

- Il banner in alto mostra **Accesso demo** con le credenziali: staff `admin` / `DemoAdmin-2026`,
  partecipante `tester` / `DemoTester-2026`. Ci sono anche il listone di esempio da scaricare e **Azzera dati**.
- La prima apertura scarica circa 15 MB (Python e librerie) e impiega qualche secondo; le successive sono molto più rapide.
- Dati e sessione restano nel browser anche dopo la chiusura. Usa **una sola scheda** alla volta.

### Limiti da conoscere

- **Dati non condivisi.** Un concorso creato da un admin non è visibile agli altri: ognuno ha la propria copia. Serve per provare
  le funzioni, non per lavorare in squadra.
- **Pagina pubblica.** Il sito di Pages è pubblico anche se il repository è privato (sui piani a pagamento). Contiene il listone
  e le credenziali demo, che valgono solo nel browser di chi le usa. **Non metterci dati reali.**
- **Sicurezza ridotta di proposito.** Nella demo le password usano pbkdf2 con poche iterazioni e la chiave di sessione è fissa:
  va bene perché i dati non lasciano il browser, non va bene per un server vero.
- **Browser.** Servono un browser recente e HTTPS (Pages lo è). Nella navigazione privata i dati non si salvano.
- **Python dalla rete.** Il motore Pyodide si scarica dal CDN `cdn.jsdelivr.net` (versione fissata 0.26.4). Senza rete la prima volta non parte.
- **Prove.** `python -m FantaRF.pages.test_bridge` (veloce, nessun browser, gira anche nella CI) e
  `python -m FantaRF.pages.test_browser` (usa Chromium via Playwright, a mano). Nessuna delle due avvia davvero Pyodide.

## Server con database condiviso (Render + Litestream)

Per far lavorare più admin sugli stessi dati: il codice resta su GitHub, GitHub Actions lo collauda e un hosting che esegue
Python lo pubblica.

```
 push su main ──► GitHub Actions (ci.yml) ──► verde? ──► Render ricostruisce l'immagine e pubblica
                  · test di fumo                          │
                  · build Docker + avvio                  ▼
                                               container: Litestream + gunicorn + Flask
                                                          │  replica continua del DB
                                                          ▼
                                               bucket S3-compatibile (Backblaze B2 / Cloudflare R2)
```

**Come si mantiene il database.** Il piano gratuito di Render ha un disco *effimero*: SQLite lì sparirebbe a ogni
riavvio o risveglio. Per questo il container usa [Litestream](https://litestream.io): a ogni avvio ripristina il
database dal bucket, mentre l'app gira replica ogni modifica. Listone, utenti, concorsi, formazioni e voti
inseriti dagli admin sopravvivono a riavvii, nuovi deploy e "sonni" del server.

## Cosa c'è di nuovo nel repository

| File | A cosa serve |
|---|---|
| `.github/workflows/ci.yml` | Test di fumo + build e avvio dell'immagine Docker, a ogni push e pull request. |
| `.github/workflows/keepalive.yml` | Opzionale: tiene sveglio il sito (inattivo finché non imposti `APP_URL`). |
| `Dockerfile`, `.dockerignore`, `.gitattributes` | Immagine con Python, gunicorn e Litestream (versione fissata 0.3.13). |
| `render.yaml` | Descrizione del servizio per Render (Blueprint). |
| `deploy/entrypoint.sh` | All'avvio: ripristina il DB, prepara i dati iniziali, serve l'app con replica attiva. |
| `deploy/bootstrap.py` | Crea (solo se mancano) admin, stagione attiva e listone iniziale. Non tocca dati esistenti. |
| `deploy/litestream.yml`, `deploy/wsgi.py`, `deploy/requirements-deploy.txt`, `deploy/smoke_test.py` | Supporto. |
| `pages/`, `.github/workflows/pages.yml` | Demo nel browser per GitHub Pages (vedi sopra). |
| `seed/Elenco_Giocatori_Fantacalcio.xlsx` | Listone di partenza, importato al primo avvio (748 righe valide, 178 scartate come da regole dell'import). |

Il codice dell'app (`services/`, `views/`, `domain/`…) **non è stato modificato**.

## Preparazione una tantum (circa 20 minuti)

### 1. Storage per il database (Backblaze B2 o Cloudflare R2)

Entrambi hanno 10 GB gratuiti e API compatibile S3; il database pesa pochi MB. Alla registrazione potrebbe esserti
chiesta una carta di credito (le fonti che ho trovato sono discordi): leggi cosa chiede prima di confermare. I nomi dei menu possono cambiare.

**Backblaze B2**
1. Crea un account e un bucket **privato** (es. `fantarf-test-db`).
2. *Application Keys* → crea una chiave **limitata a quel bucket**, con lettura e scrittura. Copia `keyID` e `applicationKey`
   (quest'ultima si vede una sola volta).
3. Sulla pagina del bucket copia l'*endpoint S3* (es. `https://s3.us-west-004.backblazeb2.com`); la **regione** è la parte
   centrale (`us-west-004`).

**Cloudflare R2**: endpoint `https://<ID-account>.r2.cloudflarestorage.com`, regione `auto`, chiave API con permessi sul bucket.

### 2. Carica il repository su GitHub

Il repository **è** il pacchetto Python, quindi il suo nome deve essere `FantaRF`. Copia i file di questo
pacchetto nella radice del repo (le cartelle `.github/`, `deploy/`, `seed/` e i file `Dockerfile`, `render.yaml`…) e fai push su `main`.

> **Meglio un repository privato.** `seed/` contiene il listone dei giocatori del GDR. Render può leggere repository privati.

Dopo il push, controlla in **Actions** che `CI` diventi verde.

### 3. Crea il servizio su Render

1. Account su [render.com](https://render.com), poi **New → Blueprint** e scegli il repository.
2. Render legge `render.yaml` e ti chiede i valori segreti:

| Variabile | Valore |
|---|---|
| `FANTAPG_BOOTSTRAP_ADMIN_USER` | nome utente del primo admin (es. `admin`) |
| `FANTAPG_BOOTSTRAP_ADMIN_PASSWORD` | password provvisoria, **almeno 10 caratteri** |
| `LITESTREAM_BUCKET` | nome del bucket |
| `LITESTREAM_ENDPOINT` | endpoint S3 completo, con `https://` |
| `LITESTREAM_REGION` | regione (`us-west-004`, `auto`…) |
| `LITESTREAM_ACCESS_KEY_ID` / `LITESTREAM_SECRET_ACCESS_KEY` | chiavi del punto 1 |

3. Al primo avvio (1-3 minuti) nel log vedi: `listone importato…`, poi `avvio con replica continua attiva`.

### 4. Primo accesso

Apri l'indirizzo `https://fantarf-test.onrender.com` (o quello assegnato), accedi con l'admin del punto 3:
l'app ti chiede subito di cambiare la password. Da **Staff → Utenti** crei gli account degli altri admin e dei tester.
La password provvisoria resta nelle variabili di Render ma **non funziona più** dopo il cambio.

## Aggiornare il sito

Fai push su `main`. Se la CI è verde Render pubblica la nuova versione; se è rossa resta online quella precedente.
Il database non viene toccato dai deploy: le migrazioni (`migrations/*.sql`) si applicano da sole all'avvio.

## Verifica che la persistenza funzioni (consigliato, 5 minuti)

Prima di far partire i tester: crea un concorso di prova, poi su Render **Manual Deploy → Clear build cache & deploy**
(oppure *Restart*). Quando il sito torna su, il concorso deve esserci ancora. Se sparisce, controlla nel log la riga
`ripristino del database dalla replica` e le chiavi del bucket.

## Copia o ripristino manuale del database

Con [Litestream 0.3.13](https://github.com/benbjohnson/litestream/releases/tag/v0.3.13) installato sul tuo computer e le stesse
variabili `LITESTREAM_*` impostate:

```bash
export FANTAPG_DATABASE=/tmp/fantapg.sqlite3
litestream restore -config deploy/litestream.yml -o copia-del-db.sqlite3 "$FANTAPG_DATABASE"
```

Ottieni un normale file SQLite apribile in locale (`flask --app . run` con `FANTAPG_DATABASE` puntato a quel file).

## Limiti noti (da conoscere prima di far testare)

- **Risveglio lento.** Dopo 15 minuti senza visite il piano gratuito di Render addormenta il sito; la prima richiesta
  impiega circa 1 minuto. Nessun dato va perso. `keepalive.yml` può tenerlo sveglio, **ma in un repository privato non
  usarlo**: GitHub Free include 2.000 minuti di Actions al mese e un ping ogni 10 minuti ne consumerebbe circa 4.300,
  bloccando la CI e quindi i deploy. Con un repository pubblico le Actions sono gratuite (ma `seed/` sarebbe visibile).
  Alternative: accettare il minuto di attesa, oppure un servizio esterno di monitoraggio che chiami `/healthz`.
  Il piano gratuito di Render ha anche 750 ore al mese: un solo servizio sempre acceso le consuma quasi tutte.
- **File Excel caricati.** I `.xlsx` caricati dall'interfaccia stanno nel disco effimero e si perdono al riavvio. I dati
  importati restano (sono nel database), ma non si possono più riaprire i report di importazioni vecchie.
- **Arresto brusco.** Con un riavvio normale Litestream sincronizza prima di uscire. Se il server viene ucciso di colpo si possono
  perdere le ultime modifiche (al massimo circa 10 secondi, vedi `sync-interval` in `deploy/litestream.yml`).
- **Un solo processo.** gunicorn gira con 1 worker e 4 thread: sufficiente per un gruppo di admin, voluto per SQLite.
- **Condizioni dei servizi.** Limiti e prezzi di Render, Backblaze e Cloudflare cambiano: verificali al momento di iscriverti.
- **Sicurezza.** Il sito è raggiungibile da chiunque abbia l'indirizzo. Restano attivi login con blocco dopo tentativi falliti,
  CSRF e cookie `Secure`. Non pubblicare l'indirizzo e non riusare per il test password che usi altrove.

## Se in futuro serve di più

Lo stesso `Dockerfile` gira ovunque ci sia Docker (un piano Render a pagamento, un VPS, Fly.io…). Con un disco persistente
si può omettere `LITESTREAM_BUCKET`: l'app salva già in `/data` e l'entrypoint avvisa che non c'è replica.
Questa variante non è stata configurata né provata.
