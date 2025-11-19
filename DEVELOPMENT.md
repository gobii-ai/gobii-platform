# DEVELOPMENT

Run Django and Celery directly on your laptop (via `uv`) while Docker Compose provides Postgres, Redis, and optional extras. All defaults in `config/settings.py` are tuned so you do **not** need to export environment variables for local work; the Compose stack and Python processes share the same static credentials.

---

## Prerequisites

- Docker Desktop (or another Docker engine) with at least 4 GB RAM.
- `uv` ≥ 0.4.
- Python 3.12 (uv will manage the interpreter inside `.venv`).
- Node.js 22.x (ships with npm 10) for the Vite frontend.

---

## One-time setup

From the repository root:

```bash
# Create the reusable virtual environment.
uv venv .venv

# Install Python dependencies in editable mode (no activation needed).
uv run pip install -e .

# Frontend deps (install once; Vite reuses node_modules).
npm ci --prefix frontend

# Start backing services (Postgres, Redis, MinIO).
docker compose -f docker-compose.dev.yaml up

# Bootstrap the database.
uv run python manage.py migrate
uv run python manage.py createsuperuser
```

> Tip: `docker-compose.dev.yaml` binds services to `127.0.0.1` using the `postgres/postgres` defaults that `config/settings.py` applies when `GOBII_RELEASE_ENV=local` (the default outside containers).

---

## Daily workflow

1. **Ensure services are running**
   ```bash
   docker compose -f docker-compose.dev.yaml up
   ```
   (Optional) add `--profile containers` if you prefer to run Django from within Docker.

2. **Start the Django ASGI server with live reload**
   ```bash
   uv run uvicorn config.asgi:application --reload --host 0.0.0.0 --port 8000
   ```

3. **Start the Celery worker (new shell)**
   ```bash
   uv run celery -A config worker -l info --pool solo
   ```
   The `--pool solo` setting avoids multiprocessing issues on macOS/Apple Silicon while keeping autoreload-friendly behavior.

4. **Front-end hot reload (new shell)**
   ```bash
   npm run dev --prefix frontend
   ```

5. **Optional processes**
   - Celery beat: `uv run celery -A config beat --loglevel info --scheduler redbeat.RedBeatScheduler`
   - Object storage (MinIO UI) is available at http://localhost:9090 (`minioadmin`/`minioadmin` by default).
   - Prefer running Django/Celery inside containers? `docker compose -f docker-compose.dev.yaml --profile containers up web` (add `--profile worker` or `--profile beat` as needed) will reuse the same backing services.

Stop everything when finished with `Ctrl+C` in the Compose terminal (or run `docker compose -f docker-compose.dev.yaml down` in another shell if you prefer a clean exit).

---

## Testing

Follow the testing guidance in `README` or individual apps. When writing or running tests, prefer targeted modules first, then finish with the full suite:

```bash
# Example: run a focused test file
uv run python manage.py test path.to.app.tests.test_example --settings=config.test_settings

# (Use --parallel auto for larger suites when needed)
uv run python manage.py test --settings=config.test_settings --parallel auto
```

---

## Troubleshooting

- **Database migrations fail the first time** – ensure Postgres is up (`docker compose -f docker-compose.dev.yaml ps`) and rerun `uv run python manage.py migrate`.
- **Celery cannot connect to Redis** – verify the container is healthy and that the worker command is using the same shell with `.venv` activated (it will pick up `REDIS_URL=redis://localhost:6379/0` automatically).
- **Need a clean database** – run `docker compose -f docker-compose.dev.yaml down -v` to drop the local Postgres volume, then bring it back up and rerun migrations.

---

## Agent Evaluations

The platform includes an end-to-end evaluation system for verifying agent behavior, tool usage, and prompt effectiveness.

To run the standard evaluation suite against a temporary test agent:

```bash
uv run python manage.py run_evals
```

Options:
- `--scenario <slug>`: Run a specific scenario (e.g., `--scenario echo_response`).
- `--sync`: Run synchronously (eager mode) for debugging without a separate Celery worker.
- `--agent-id <uuid>`: Run against an existing Persistent Agent instead of creating a temporary one.
