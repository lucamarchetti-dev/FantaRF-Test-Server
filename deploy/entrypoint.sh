#!/bin/sh
# Avvio del server di test: ripristina il DB dalla replica, prepara i dati iniziali, poi serve l'app
# mentre Litestream replica ogni modifica.
set -eu

export FANTAPG_DATABASE="${FANTAPG_DATABASE:-/data/fantapg.sqlite3}"
export FANTAPG_UPLOAD_DIR="${FANTAPG_UPLOAD_DIR:-/data/uploads}"
export FANTAPG_INSTANCE_PATH="${FANTAPG_INSTANCE_PATH:-/data/instance}"
mkdir -p "$(dirname "$FANTAPG_DATABASE")" "$FANTAPG_UPLOAD_DIR" "$FANTAPG_INSTANCE_PATH"

PORT="${PORT:-10000}"
SERVE="gunicorn --bind 0.0.0.0:${PORT} --workers 1 --threads 4 --timeout 60 --access-logfile - FantaRF.deploy.wsgi:app"
LS_CONFIG="/app/FantaRF/deploy/litestream.yml"

if [ -n "${LITESTREAM_BUCKET:-}" ]; then
  : "${LITESTREAM_ENDPOINT:?LITESTREAM_ENDPOINT mancante}"
  : "${LITESTREAM_ACCESS_KEY_ID:?LITESTREAM_ACCESS_KEY_ID mancante}"
  : "${LITESTREAM_SECRET_ACCESS_KEY:?LITESTREAM_SECRET_ACCESS_KEY mancante}"
  export LITESTREAM_PATH="${LITESTREAM_PATH:-fantarf}"
  export LITESTREAM_REGION="${LITESTREAM_REGION:-auto}"

  echo "[entrypoint] ripristino del database dalla replica (se esiste)..."
  # Se la replica esiste ma non è raggiungibile, il comando FALLISCE e il container si ferma (set -e):
  # meglio non partire che partire con un database vuoto e poi sovrascrivere la replica buona.
  litestream restore -config "$LS_CONFIG" -if-db-not-exists -if-replica-exists "$FANTAPG_DATABASE"

  echo "[entrypoint] preparazione dati iniziali..."
  python -m FantaRF.deploy.bootstrap

  echo "[entrypoint] avvio con replica continua attiva."
  exec litestream replicate -config "$LS_CONFIG" -exec "$SERVE"
else
  echo "[entrypoint] ATTENZIONE: LITESTREAM_BUCKET non impostato -> il database NON viene replicato." >&2
  echo "[entrypoint] Su un disco effimero (es. piano gratuito) i dati si perdono a ogni riavvio." >&2
  python -m FantaRF.deploy.bootstrap
  exec $SERVE
fi
