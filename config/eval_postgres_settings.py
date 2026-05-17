"""
Local Postgres settings for full canonical eval runs.

Use this when SQLite eager execution is too serial for a live suite. It keeps
the normal run_evals path, seeds local routing profiles, and runs Celery tasks
eagerly through the command's bounded local thread pool.
"""
import os

os.environ.setdefault("DJANGO_SECRET_KEY", "eval-local-secret-key")
os.environ.setdefault("GOBII_ENCRYPTION_KEY", "eval-local-encryption-key")
os.environ["POSTGRES_DB"] = os.environ.get("EVAL_POSTGRES_DB", "eval_local")
os.environ["POSTGRES_USER"] = os.environ.get("EVAL_POSTGRES_USER", os.environ.get("USER", "postgres"))
os.environ["POSTGRES_PASSWORD"] = os.environ.get("EVAL_POSTGRES_PASSWORD", "")
os.environ["POSTGRES_HOST"] = os.environ.get("EVAL_POSTGRES_HOST", "127.0.0.1")
os.environ["POSTGRES_PORT"] = os.environ.get("EVAL_POSTGRES_PORT", "55432")
os.environ["REDIS_URL"] = os.environ.get("EVAL_REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("SEGMENT_WRITE_KEY", "")
os.environ.setdefault("GOBII_ENABLE_COMMUNITY_UNLIMITED", "0")
os.environ.setdefault("GOBII_ENABLE_TRACING", "0")
os.environ.setdefault("SANDBOX_COMPUTE_ENABLED", "0")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
os.environ.setdefault("LLM_BOOTSTRAP_OPTIONAL", "1")
os.environ.setdefault("BROWSER_USE_TASK_EXECUTION_DISABLED", "1")
os.environ.setdefault("EVAL_LOCAL_SETUP_ENABLED", "1")
os.environ.setdefault("EVAL_LOCAL_AUTO_MIGRATE", "1")
os.environ.setdefault("EVAL_BROWSER_TASK_SIMULATION_ENABLED", "1")

from .settings import *  # noqa: F403
from .settings import STORAGES

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("EVAL_POSTGRES_DB", "eval_local"),
        "USER": os.environ.get("EVAL_POSTGRES_USER", os.environ.get("USER", "postgres")),
        "PASSWORD": os.environ.get("EVAL_POSTGRES_PASSWORD", ""),
        "HOST": os.environ.get("EVAL_POSTGRES_HOST", "127.0.0.1"),
        "PORT": os.environ.get("EVAL_POSTGRES_PORT", "55432"),
        "CONN_MAX_AGE": 0,
        "DISABLE_SERVER_SIDE_CURSORS": True,
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {
            "sslmode": os.environ.get("EVAL_POSTGRES_SSLMODE", "disable"),
            "keepalives": 1,
            "keepalives_idle": 60,
            "keepalives_interval": 30,
            "keepalives_count": 5,
        },
    }
}

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_BROKER_URL = ""
CELERY_RESULT_BACKEND = ""

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }
}

SIMULATE_EMAIL_DELIVERY = True
FIRST_RUN_SETUP_ENABLED = False
LLM_BOOTSTRAP_OPTIONAL = True
BROWSER_USE_TASK_EXECUTION_DISABLED = True
EVAL_LOCAL_SETUP_ENABLED = True
EVAL_LOCAL_AUTO_MIGRATE = True
EVAL_BROWSER_TASK_SIMULATION_ENABLED = True
LITELLM_MAX_RETRIES = int(os.environ.get("EVAL_LITELLM_MAX_RETRIES", "4"))
LITELLM_RETRY_BACKOFF_SECONDS = float(os.environ.get("EVAL_LITELLM_RETRY_BACKOFF_SECONDS", "1.5"))
AGENT_EMPTY_LLM_RESPONSE_LOOP_RETRIES = int(os.environ.get("EVAL_AGENT_EMPTY_LLM_RESPONSE_LOOP_RETRIES", "2"))
VITE_USE_DEV_SERVER = False
STORAGES["staticfiles"]["BACKEND"] = "django.contrib.staticfiles.storage.StaticFilesStorage"
