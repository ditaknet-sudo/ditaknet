FROM python:3.11-slim

ARG APP_VERSION=2.0.0
ARG IMAGE_TAG=2.0.0
ARG BUILD_COMMIT=
ARG BUILD_DATE=

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_NAME=DitakNet \
    APP_HOST=0.0.0.0 \
    APP_PORT=5833 \
    APP_VERSION=${APP_VERSION} \
    IMAGE_TAG=${IMAGE_TAG} \
    BUILD_COMMIT=${BUILD_COMMIT} \
    BUILD_DATE=${BUILD_DATE} \
    DATA_DIR=/app/data \
    LOG_DIR=/app/logs \
    BACKUP_DIR=/app/backups \
    PLUGIN_DIR=/app/plugins

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        iproute2 \
        iputils-ping \
        net-tools \
        procps \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app /app/app
COPY ditaknet /app/ditaknet
COPY plugins /app/plugins
COPY docs /app/docs
COPY README.md /app/README.md

RUN mkdir -p /app/data /app/logs /app/backups

EXPOSE 5833

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import json,urllib.request; urllib.request.urlopen('http://127.0.0.1:5833/health', timeout=3).read()"

CMD ["sh", "-c", "uvicorn ditaknet.main:app --host ${APP_HOST:-0.0.0.0} --port ${APP_PORT:-5833}"]
