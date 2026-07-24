# syntax=docker/dockerfile:1.7

FROM python:3.13-slim-bookworm AS sqlite-tools

ARG SQLITE_VERSION_NUMBER=3530300
ARG SQLITE_SOURCE_SHA256=bb80bf8a3bffc19241ce8aba5a4bc74e9c3980013cb0b5f0f0976a99516942af

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean && \
    apt-get update && \
    apt-get install --no-install-recommends -y build-essential curl tcl unzip ca-certificates && \
    curl -fsSL "https://sqlite.org/2026/sqlite-src-${SQLITE_VERSION_NUMBER}.zip" -o /tmp/sqlite.zip && \
    echo "${SQLITE_SOURCE_SHA256}  /tmp/sqlite.zip" | sha256sum -c - && \
    unzip -q /tmp/sqlite.zip -d /tmp && \
    cd "/tmp/sqlite-src-${SQLITE_VERSION_NUMBER}" && \
    ./configure --disable-shared && \
    make -j"$(nproc)" sqlite3 sqlite3_rsync && \
    install -m 0755 sqlite3 sqlite3_rsync /usr/local/bin/

FROM python:3.13-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY --from=sqlite-tools /usr/local/bin/sqlite3 /usr/local/bin/sqlite3
COPY --from=sqlite-tools /usr/local/bin/sqlite3_rsync /usr/local/bin/sqlite3_rsync

# Keep downloaded debs in the BuildKit cache mount across rebuilds.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean && \
    apt-get update && \
    apt-get install --no-install-recommends -y \
        git \
        procps \
        curl \
        ripgrep \
        jq \
        less \
        unzip \
        zip \
        file \
        fd-find \
        tree \
        ca-certificates \
        gnupg \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libharfbuzz-subset0 \
        libgdk-pixbuf2.0-0 \
        libcairo2 && \
    install -d -m 0755 /etc/apt/keyrings && \
    curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg && \
    echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" > /etc/apt/sources.list.d/nodesource.list && \
    apt-get update && \
    apt-get install --no-install-recommends -y nodejs && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --upgrade pip uv

COPY pyproject.toml README.md ./
COPY __init__.py ./__init__.py
COPY asgi.py ./asgi.py
COPY server ./server
COPY sandbox_utils.py logging_config.py ./

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install .

COPY . ./

ARG GIT_COMMIT=unknown
RUN echo "${GIT_COMMIT}" > /app/.git-commit

RUN addgroup --system appgroup && \
    adduser --system --ingroup appgroup appuser && \
    mkdir -p /tmp/.npm /tmp/.cache /runtime-cache /workspace && \
    chown -R appuser:appgroup /tmp/.npm /tmp/.cache /runtime-cache /workspace
ENV HOME=/tmp \
    NPM_CONFIG_CACHE=/tmp/.npm \
    npm_config_cache=/tmp/.npm \
    XDG_CACHE_HOME=/tmp/.cache \
    SANDBOX_RUNTIME_CACHE_ROOT=/runtime-cache
USER appuser

EXPOSE 8080
CMD ["gunicorn", "sandbox_server.asgi:application", "-k", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:8080", "--workers", "1", "--timeout", "0"]
