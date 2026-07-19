# Dependabot tracks the human-readable tag while the digest pins the exact
# multi-architecture base index used by linux/amd64 and linux/arm64 builds.
FROM python:3.11-slim@sha256:db3ff2e1800a8581e2c48a27c3995339d47bdf046da21c7627accd3d51053a93

ARG APP_VERSION=2.0.1
ARG IMAGE_TAG=2.0.1
ARG BUILD_COMMIT=
ARG BUILD_DATE=
ARG APP_UID=568
ARG APP_GID=568

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_NAME=DitakNet \
    APP_ENV=production \
    APP_HOST=0.0.0.0 \
    APP_PORT=5833 \
    APP_VERSION=${APP_VERSION} \
    IMAGE_TAG=${IMAGE_TAG} \
    BUILD_COMMIT=${BUILD_COMMIT} \
    BUILD_DATE=${BUILD_DATE} \
    HOME=/tmp \
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
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid "${APP_GID}" apps \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" \
        --home-dir /app --no-create-home --shell /usr/sbin/nologin apps

COPY requirements.txt /app/requirements.txt
# Build/install tooling is not required by the running service. Removing it
# reduces image size and eliminates its vendored runtime attack surface.
RUN pip install --no-cache-dir --require-hashes -r /app/requirements.txt \
    && python -m pip uninstall --yes setuptools wheel \
    && python -m pip uninstall --yes pip

COPY app /app/app
COPY ditaknet /app/ditaknet
COPY --chown=${APP_UID}:${APP_GID} plugins /app/plugins
COPY docs /app/docs
COPY README.md /app/README.md

RUN mkdir -p /app/data /app/logs /app/backups /app/plugins \
    && chown -R "${APP_UID}:${APP_GID}" \
        /app/data /app/logs /app/backups /app/plugins

EXPOSE 5833

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import json,urllib.request; data=json.load(urllib.request.urlopen('http://127.0.0.1:5833/health',timeout=3)); assert data.get('status') == 'healthy', data"

# Match the TrueNAS SCALE apps identity. Only the four mounted runtime paths
# need write access; application code remains root-owned and read-only.
USER ${APP_UID}:${APP_GID}

CMD ["sh", "-c", "exec uvicorn ditaknet.main:app --host ${APP_HOST:-0.0.0.0} --port ${APP_PORT:-5833}"]
