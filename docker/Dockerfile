# syntax=docker/dockerfile:1.7

# ────────────────────────────────────────────────
# deps stage – OS libs, Python deps, Playwright
# ────────────────────────────────────────────────
FROM python:3.13-slim-bookworm AS deps

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
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

# copy only manifests → keep cache when only code changes
COPY pyproject.toml poetry.lock* ./

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install .

# Preinstall Bright Data MCP globally so containers do not fetch it on-the-fly.
# Local dev (outside Docker) still uses npx to install on demand.
RUN npm install -g @brightdata/mcp@2.9.3 && \
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
ARG VITE_BUILD_BASE_URL
ARG SKIP_FRONTEND_BUILD=false
ENV IN_DOCKER=1 \
    GOBII_RELEASE_ENV=build \
    DEBUG=0 \
    VITE_BUILD_BASE_URL=${VITE_BUILD_BASE_URL}

# Prime frontend dependencies before copying the rest of the repository so we
# can leverage cached layers when only application code changes.
COPY frontend/package.json frontend/package-lock.json ./frontend/
RUN --mount=type=cache,target=/root/.npm \
    if [ "$SKIP_FRONTEND_BUILD" != "true" ]; then npm ci --prefix frontend; fi

COPY frontend ./frontend
RUN --mount=type=cache,target=/root/.npm \
    if [ "$SKIP_FRONTEND_BUILD" != "true" ]; then npm run build --prefix frontend; fi

COPY . .

# When frontend build is skipped, download the manifest from CDN so Django
# can resolve entry points to hashed filenames in CDN mode.
RUN if [ "$SKIP_FRONTEND_BUILD" = "true" ] && [ -n "$VITE_BUILD_BASE_URL" ]; then \
      mkdir -p static/frontend && \
      curl -fsSL "${VITE_BUILD_BASE_URL}manifest.json" -o static/frontend/manifest.json; \
    fi

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

# ── Granular COPY from builder ──────────────────
# Copy only what's needed at runtime. Excludes: frontend/, node_modules/,
# .npm, tests/, __pycache__/, gobii_platform.egg-info/,
# docs/, scripts/, reports/, assets/, mediafiles/, .github/, .venv/,
# prompts/, sandbox_server/ (separate monorepo subproject), src/ (empty / not tracked in git)

# Python application directories
COPY --from=builder /app/api ./api
COPY --from=builder /app/agents ./agents
COPY --from=builder /app/billing ./billing
COPY --from=builder /app/config ./config
COPY --from=builder /app/console ./console
COPY --from=builder /app/constants ./constants
COPY --from=builder /app/marketing_events ./marketing_events
COPY --from=builder /app/middleware ./middleware
COPY --from=builder /app/misc ./misc
COPY --from=builder /app/pages ./pages
COPY --from=builder /app/proprietary ./proprietary
COPY --from=builder /app/setup ./setup
COPY --from=builder /app/tasks ./tasks
COPY --from=builder /app/templates ./templates
COPY --from=builder /app/templatetags ./templatetags
COPY --from=builder /app/util ./util
COPY --from=builder /app/vendor ./vendor

# tests/ is required because it's listed in INSTALLED_APPS (Django app)
COPY --from=builder /app/tests ./tests

# Compose runs collectstatic from the runtime image into a fresh named volume,
# so the project-level static source tree must exist here as an input.
COPY --from=builder /app/static ./static

# Root-level Python files
COPY --from=builder /app/manage.py ./manage.py
COPY --from=builder /app/observability.py ./observability.py
COPY --from=builder /app/turnstile_signup.py ./turnstile_signup.py
COPY --from=builder /app/agent_namer.py ./agent_namer.py
COPY --from=builder /app/celery_task_counter.py ./celery_task_counter.py

# Package metadata
COPY --from=builder /app/pyproject.toml ./pyproject.toml

# Collected static files (output of collectstatic)
COPY --from=builder /app/staticfiles ./staticfiles

# Vite manifest needed for Django template tag to resolve frontend assets
COPY --from=builder /app/static/frontend/manifest.json ./static/frontend/manifest.json

# Write git commit SHA for runtime traceability (passed from CI)
ARG GIT_COMMIT=unknown
RUN echo "${GIT_COMMIT}" > /app/.git-commit

# Create npm cache directory with proper permissions
RUN mkdir -p /tmp/.npm && chown -R appuser:appgroup /tmp/.npm

USER appuser

EXPOSE 8000
CMD ["gunicorn", "config.asgi:application", "-k", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:8000", "--graceful-timeout", "30", "--timeout", "0"]
