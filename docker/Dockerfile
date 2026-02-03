# syntax=docker/dockerfile:1.7

# ────────────────────────────────────────────────
# deps stage – OS libs, Python deps, Playwright
# ────────────────────────────────────────────────
FROM python:3.13-slim-bookworm AS deps

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PLAYWRIGHT_BROWSERS_PATH=/usr/local/share/pw-browsers

WORKDIR /app

RUN --mount=type=cache,target=/var/cache/apt \
    apt-get update && \
    apt-get install --no-install-recommends -y \
        git \
        procps \
        xvfb x11-utils xauth libx11-6 libxext6 libxrender1 \
        libxtst6 libxi6 libxrandr2 libxfont2 curl \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 \
        gnupg ca-certificates && \
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    mkdir -p /etc/apt/keyrings && \
    curl -fsSL https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /etc/apt/keyrings/google-linux.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/google-linux.gpg] http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list && \
    apt-get update && \
    apt-get install --no-install-recommends -y \
        google-chrome-stable \
        fonts-liberation fonts-noto fonts-unifont \
        libasound2 libatk-bridge2.0-0 libatk1.0-0 libcups2 libdbus-1-3 \
        libdrm2 libgbm1 libglib2.0-0 libnspr4 libnss3 libu2f-udev ffmpeg \
        libxcomposite1 libxdamage1 libxfixes3 libxkbcommon0 xdg-utils nodejs && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --upgrade pip

# Optional model prefetch for LLM-Lingua
ARG PREFETCH_LLM_LINGUA=0
ARG LLMLINGUA_MODEL_NAME="microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank"
ENV HF_HOME=/app/.cache/huggingface

# copy only manifests → keep cache when only code changes
COPY pyproject.toml poetry.lock* ./

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install .

RUN if [ "$PREFETCH_LLM_LINGUA" = "1" ]; then \
  python -c "import os; from transformers import AutoModel, AutoTokenizer; model = os.environ.get('LLMLINGUA_MODEL_NAME', 'microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank'); AutoTokenizer.from_pretrained(model); AutoModel.from_pretrained(model); print('Prefetched:', model)"; \
  fi

# Preinstall Bright Data MCP globally so containers do not fetch it on-the-fly.
# Local dev (outside Docker) still uses npx to install on demand.
RUN npm install -g @brightdata/mcp@2.5.0 && \
    npm install -g exa-mcp-server && \
    npm cache clean --force

# ────────────────────────────────────────────────
# builder stage – copy code, install editable, test
# ────────────────────────────────────────────────
FROM deps AS builder

# Ensure the build runs in a non-local, in-container context so Django does not
# flip to DEBUG mode via the convenience defaults in settings.py. When DEBUG is
# True, staticfiles uses CompressedStaticFilesStorage (no manifest), which causes
# runtime 500s with Manifest lookups. Setting these envs guarantees
# CompressedManifestStaticFilesStorage during collectstatic.
ENV IN_DOCKER=1 \
    GOBII_RELEASE_ENV=build \
    DEBUG=0

# Prime frontend dependencies before copying the rest of the repository so we
# can leverage cached layers when only application code changes.
COPY frontend/package.json frontend/package-lock.json ./frontend/
RUN --mount=type=cache,target=/root/.npm \
    npm ci --prefix frontend

COPY frontend ./frontend
RUN --mount=type=cache,target=/root/.npm \
    npm run build --prefix frontend

COPY . .

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --editable .

ENV USE_EPHEMERAL_XVFB=true

RUN DJANGO_SECRET_KEY=dummy-build-key \
    DEBUG=0 \
    GOBII_RELEASE_ENV=build \
    POSTGRES_DB=dummy-db \
    POSTGRES_USER=dummy-user \
    POSTGRES_PASSWORD=dummy-pw \
    POSTGRES_HOST=dummy-host \
    POSTGRES_PORT=5432 \
    AWS_ACCESS_KEY_ID=dummy-aws-key \
    AWS_SECRET_ACCESS_KEY=dummy-aws-secret \
    AWS_STORAGE_BUCKET_NAME=dummy-bucket \
    AWS_S3_REGION_NAME=dummy-region \
    AWS_S3_ENDPOINT_URL=http://dummy-endpoint:9000 \
    REDIS_URL=redis://dummy-redis:6379/0 \
    GS_BUCKET_NAME=dummy-gcs-bucket \
    SEGMENT_WRITE_KEY=dummy-segment-key \
    GRAFANA_API_KEY=dummy-grafana-key \
    POSTMARK_SERVER_TOKEN=dummy-postmark-token \
    POSTMARK_INCOMING_WEBHOOK_TOKEN=dummy-postmark-incoming-token \
    MAILGUN_INCOMING_WEBHOOK_TOKEN=dummy-mailgun-incoming-token \
    GOOGLE_API_KEY=dummy-google-key \
    EXA_SEARCH_API_KEY=dummy-exa-key \
    python manage.py collectstatic --noinput --clear

# ────────────────────────────────────────────────
# runtime stage – lean image
# ────────────────────────────────────────────────
FROM deps AS runtime

RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser
WORKDIR /app

ENV STATIC_ROOT=/app/staticfiles \
    USE_EPHEMERAL_XVFB=true \
    NPM_CONFIG_CACHE=/tmp/.npm

COPY --from=builder /app /app

# Write git commit SHA for runtime traceability (passed from CI)
ARG GIT_COMMIT=unknown
RUN echo "${GIT_COMMIT}" > /app/.git-commit

# Create npm cache directory with proper permissions
RUN mkdir -p /tmp/.npm && chown -R appuser:appgroup /tmp/.npm

USER appuser

EXPOSE 8000
CMD ["gunicorn", "config.asgi:application", "-k", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:8000", "--graceful-timeout", "30", "--timeout", "0"]
