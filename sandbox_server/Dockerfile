# syntax=docker/dockerfile:1.7

FROM python:3.13-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN --mount=type=cache,target=/var/cache/apt \
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
COPY server ./server
COPY sandbox_utils.py logging_config.py ./

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir .

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
CMD ["gunicorn", "sandbox_server:application", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "4", "--timeout", "0"]
