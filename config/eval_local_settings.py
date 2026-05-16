"""
Local settings for canonical eval runs.

This module keeps evals on the normal run_evals path while avoiding a local
Postgres/Redis dependency. It is intended for one-off local and CI-like eval
debugging, not production traffic.
"""
import os

os.environ.setdefault("DJANGO_SECRET_KEY", "eval-local-secret-key")
os.environ.setdefault("GOBII_ENCRYPTION_KEY", "eval-local-encryption-key")
os.environ.setdefault("POSTGRES_DB", "eval_local")
os.environ.setdefault("POSTGRES_USER", "eval_local")
os.environ.setdefault("POSTGRES_PASSWORD", "eval_local")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("POSTGRES_PORT", "5432")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("SEGMENT_WRITE_KEY", "")
os.environ.setdefault("GOBII_ENABLE_COMMUNITY_UNLIMITED", "0")
os.environ.setdefault("GOBII_ENABLE_TRACING", "0")
os.environ.setdefault("SANDBOX_COMPUTE_ENABLED", "0")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
os.environ.setdefault("LLM_BOOTSTRAP_OPTIONAL", "1")
os.environ.setdefault("BROWSER_USE_TASK_EXECUTION_DISABLED", "1")
os.environ.setdefault("EVAL_LOCAL_SETUP_ENABLED", "1")
os.environ.setdefault("EVAL_LOCAL_AUTO_MIGRATE", "1")

from .settings import *  # noqa: F403
from .settings import BASE_DIR, STORAGES

_eval_local_dir = BASE_DIR / ".local"
_eval_local_dir.mkdir(parents=True, exist_ok=True)

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": str(_eval_local_dir / "eval-local.sqlite3"),
        "OPTIONS": {"timeout": 30},
    }
}


class DisableMigrations(dict):
    def __contains__(self, item):
        return True

    def __getitem__(self, item):
        return None


MIGRATION_MODULES = DisableMigrations()

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
VITE_USE_DEV_SERVER = False
STORAGES["staticfiles"]["BACKEND"] = "django.contrib.staticfiles.storage.StaticFilesStorage"
