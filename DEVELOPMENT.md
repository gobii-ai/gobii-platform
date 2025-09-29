# DEVELOPMENT

A single-service monorepo. Docker Compose spins up Postgres, Redis, and MinIO;  
Django and Celery run directly inside your `console/.venv` for fast reloads.

---

## 0. Prerequisites

- docker
- uv
- python
- nodejs 22.x (for the Vite dev server)

---

## 1. One-time setup

```bash
# clone repo, then:
cp infra/local/.env.example infra/local/.env

# spin up services
docker compose -f infra/local/docker-compose.yml up -d db, redis, minio

# create Python venv
cd console
uv venv .venv
source .venv/bin/activate

# install
uv pip install -e '.[dev]'

# install frontend deps
npm ci --prefix frontend

# bootstrap Django
python manage.py migrate
python manage.py createsuperuser   # email + pw
python manage.py setup_initial_site   # setup Google OAuth placeholder

# run Celery worker (new shell)
celery -A config worker -l info --pool solo

# optional: Celery beat for periodic jobs
# celery -A config beat -l info
```

---

## 2. Daily workflow

```bash
# 1) ensure services are up
docker compose -f infra/local/docker-compose.yml up -d

# 2) activate venv
source console/.venv/bin/activate

# 3) run the web server with hot reload
python manage.py runserver 0.0.0.0:8000

# 4) (new shell) run the Vite dev server for React pages
npm run dev --prefix frontend
```

Open:

* `http://localhost:8000/admin/` – Django admin  
* `http://localhost:8000/accounts/login/` – email / Google login  
* `http://localhost:8000/docs/` – Swagger UI (auto-generated OpenAPI)  

With both servers running, `/console/agents/<agent-id>/chat/` serves the React
shell and calls `/api/v1/ping/` via your Django session. When DEBUG is on
(default for local dev) the template loads modules from the Vite dev server
automatically; the legacy agent list at `/console/agents/` stays server-rendered.

---

## 3. Project folders

```
console/
├── apps/               # first-party Django apps (add them here)
├── config/             # settings, URLs, celery.py
├── manage.py
└── pyproject.toml
infra/
└── local/
    ├── docker-compose.yml
    └── .env            # never committed
```

---

## 4. Testing

```bash
pytest                                      # unit tests
pytest -q  --cov=apps --cov-report=html     # coverage
```

---
