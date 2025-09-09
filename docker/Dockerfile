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
        procps \
        xvfb x11-utils xauth libx11-6 libxext6 libxrender1 \
        libxtst6 libxi6 libxrandr2 libxfont2 curl && \
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install --no-install-recommends -y \
    nodejs && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --upgrade pip

# copy only manifests → keep cache when only code changes
COPY pyproject.toml poetry.lock* ./

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir . && \
    patchright install chrome --with-deps

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

COPY . .

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir --editable .

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

# Create npm cache directory with proper permissions
RUN mkdir -p /tmp/.npm && chown -R appuser:appgroup /tmp/.npm

USER appuser

EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000"]
