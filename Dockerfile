FROM python:3.12-slim

# Litestream: replica SQLite su storage S3-compatibile (versione fissata).
ARG LITESTREAM_VERSION=0.3.13
ADD https://github.com/benbjohnson/litestream/releases/download/v${LITESTREAM_VERSION}/litestream-v${LITESTREAM_VERSION}-linux-amd64.tar.gz /tmp/litestream.tar.gz
RUN tar -C /usr/local/bin -xzf /tmp/litestream.tar.gz && rm /tmp/litestream.tar.gz && litestream version

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app
WORKDIR /app

# Il repository È il pacchetto Python: dentro il container la cartella deve chiamarsi FantaRF.
COPY requirements.txt deploy/requirements-deploy.txt /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements.txt -r /tmp/requirements-deploy.txt

RUN useradd --create-home --uid 10001 app && mkdir -p /data && chown app:app /data
COPY --chown=app:app . /app/FantaRF
USER app

ENV FANTAPG_DATABASE=/data/fantapg.sqlite3 \
    FANTAPG_UPLOAD_DIR=/data/uploads \
    FANTAPG_INSTANCE_PATH=/data/instance

EXPOSE 10000
CMD ["sh", "/app/FantaRF/deploy/entrypoint.sh"]
