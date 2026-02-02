"""
Simple test settings that use SQLite instead of PostgreSQL.
"""
import os

# Set environment variables before importing settings
os.environ.setdefault("DJANGO_SECRET_KEY", "test-secret-key")
os.environ.setdefault("GOBII_ENCRYPTION_KEY", "dummy-encryption-key-for-testing")
os.environ.setdefault("POSTGRES_DB", "test")
os.environ.setdefault("POSTGRES_USER", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("POSTGRES_PORT", "5432")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("SEGMENT_WRITE_KEY", "")
# Keep proprietary mode off by default in tests; specific tests can override
# Disable the community unlimited override so plan limits behave predictably in tests
os.environ.setdefault("GOBII_ENABLE_COMMUNITY_UNLIMITED", "0")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("GOBII_ENABLE_TRACING", "0")
os.environ.setdefault("LLMLINGUA_ENABLED", "0")
os.environ["STRIPE_ENABLED"] = "1"
os.environ["STRIPE_TEST_SECRET_KEY"] = os.environ.get("STRIPE_TEST_SECRET_KEY") or "sk_test_dummy"

from .settings import *

# Ensure Stripe integration appears enabled during tests when patched/mocked.
STRIPE_TEST_SECRET_KEY = os.environ.get("STRIPE_TEST_SECRET_KEY", "sk_test_dummy")
STRIPE_KEYS_PRESENT = True
STRIPE_ENABLED = True
STRIPE_DISABLED_REASON = ""

# Override database to use SQLite for testing
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        # Shared-cache memory DB so threaded tests use the same schema connection.
        "NAME": "file:memorydb_default?mode=memory&cache=shared",
        "OPTIONS": {"uri": True},
    }
}

# Disable all migrations to avoid PostgreSQL-specific SQL (e.g., CASCADE, EXTENSION) when running
# the suite in SQLite. Django will instead create the schema directly from models.

class DisableMigrations(dict):
    def __contains__(self, item):
        return True

    def __getitem__(self, item):
        return None

MIGRATION_MODULES = DisableMigrations()

# -----------------------------------------------------------------------------
#  Celery configuration – run tasks eagerly and keep everything in-process
# -----------------------------------------------------------------------------

# Execute Celery tasks locally, synchronously (no broker connection required)
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True  # Propagate exceptions to test runner

# Use in-memory transport / backend so Celery never attempts to connect to Redis
CELERY_BROKER_URL = ""
CELERY_RESULT_BACKEND = ""

# Channels: keep WebSocket tests in-process without Redis.
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }
}

# Ensure email delivery is simulated in tests (no network calls)
SIMULATE_EMAIL_DELIVERY = True

# Bypass the first-run setup wizard during tests to keep API responses predictable.
FIRST_RUN_SETUP_ENABLED = False

# Skip LLM bootstrap gating in tests; specific test cases can override as needed.
LLM_BOOTSTRAP_OPTIONAL = True

# -----------------------------------------------------------------------------
#  Silence Django's noisy "Adding permission ..." output at high verbosity
# -----------------------------------------------------------------------------

from django.contrib.auth import management as _auth_mgmt

# Django's create_permissions management routine prints one line per permission
# when verbosity >= 2 (see django/contrib/auth/management/__init__.py).  At the
# verbosity levels we use in CI (2/3) this floods the GitHub Actions log with
# hundreds of lines that add no diagnostic value.  Monkey-patch the helper so
# it always behaves as if verbosity == 1.

_orig_create_permissions = _auth_mgmt.create_permissions


def _quiet_create_permissions(app_config, verbosity, *args, **kwargs):  # type: ignore[override]
    return _orig_create_permissions(app_config, 0, *args, **kwargs)


_auth_mgmt.create_permissions = _quiet_create_permissions

# -----------------------------------------------------------------------------
#  Static files – avoid Manifest storage to prevent missing-hash errors in tests
# -----------------------------------------------------------------------------

STORAGES["staticfiles"]["BACKEND"] = "django.contrib.staticfiles.storage.StaticFilesStorage"

# Avoid relying on a running Vite dev server during test runs.
VITE_USE_DEV_SERVER = False
