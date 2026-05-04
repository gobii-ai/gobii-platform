"""
Simple test settings that use SQLite instead of PostgreSQL.
"""
import ipaddress
import os
import socket
from types import SimpleNamespace

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
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
os.environ["STRIPE_ENABLED"] = "1"
os.environ["STRIPE_TEST_SECRET_KEY"] = os.environ.get("STRIPE_TEST_SECRET_KEY") or "sk_test_dummy"

from .settings import *

# -----------------------------------------------------------------------------
#  Network and LLM isolation
# -----------------------------------------------------------------------------

_ORIGINAL_SOCKET_CONNECT = socket.socket.connect
_ORIGINAL_SOCKET_CONNECT_EX = socket.socket.connect_ex


def _test_socket_host_is_local(host):
    if host in ("", None):
        return True
    if isinstance(host, bytes):
        try:
            host = host.decode("ascii")
        except UnicodeDecodeError:
            return False
    host = str(host).strip().lower().rstrip(".")
    if host in {"localhost", "0.0.0.0"} or host.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_unspecified


def _assert_test_socket_address_allowed(address):
    if not isinstance(address, tuple) or not address:
        return
    host = address[0]
    if _test_socket_host_is_local(host):
        return
    raise RuntimeError(
        "Live network access is disabled in tests: attempted connection to "
        f"{host!r}. Mock the provider/client, or set "
        "GOBII_ALLOW_LIVE_TEST_NETWORK=1 for intentional integration tests."
    )


def _guarded_test_socket_connect(self, address):
    _assert_test_socket_address_allowed(address)
    return _ORIGINAL_SOCKET_CONNECT(self, address)


def _guarded_test_socket_connect_ex(self, address):
    _assert_test_socket_address_allowed(address)
    return _ORIGINAL_SOCKET_CONNECT_EX(self, address)


if os.environ.get("GOBII_ALLOW_LIVE_TEST_NETWORK") != "1":
    socket.socket.connect = _guarded_test_socket_connect
    socket.socket.connect_ex = _guarded_test_socket_connect_ex


class _TestLiteLLMResponse(SimpleNamespace):
    def get(self, key, default=None):
        return getattr(self, key, default)

    def __getitem__(self, key):
        return getattr(self, key)


def _test_litellm_content(messages):
    combined = "\n".join(
        str(message.get("content", ""))
        for message in messages or []
        if isinstance(message, dict)
    ).lower()
    if "json array containing exactly three short tags" in combined:
        return '["Operations", "Research", "Support"]'
    if "plain-language sentence under 160 characters" in combined:
        return "Test agent summary."
    if "physical identity" in combined or "visual identities" in combined:
        return "A friendly professional with an approachable expression."
    return "Test completion."


def _test_litellm_usage():
    details = SimpleNamespace(cached_tokens=0)
    return SimpleNamespace(
        prompt_tokens=1,
        completion_tokens=1,
        total_tokens=2,
        prompt_tokens_details=details,
    )


def _test_litellm_response(**kwargs):
    usage = _test_litellm_usage()
    content = _test_litellm_content(kwargs.get("messages"))
    message = SimpleNamespace(
        role="assistant",
        content=content,
        tool_calls=[],
        reasoning_content=None,
    )
    choice = SimpleNamespace(message=message, finish_reason="stop", index=0)
    return _TestLiteLLMResponse(
        id="test-litellm-completion",
        response_id="test-litellm-completion",
        choices=[choice],
        usage=usage,
        model=kwargs.get("model"),
        provider=kwargs.get("custom_llm_provider") or kwargs.get("provider"),
        model_extra={"usage": usage},
    )


def _test_litellm_stream(**kwargs):
    usage = _test_litellm_usage()
    content = _test_litellm_content(kwargs.get("messages"))
    return iter(
        [
            SimpleNamespace(
                id="test-litellm-completion",
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(content=content, reasoning_content=None, tool_calls=None),
                        finish_reason=None,
                    )
                ],
                usage=None,
            ),
            SimpleNamespace(
                id="test-litellm-completion",
                choices=[SimpleNamespace(delta=SimpleNamespace(content=None), finish_reason="stop")],
                usage=usage,
            ),
        ]
    )


def _test_litellm_completion(**kwargs):
    if kwargs.get("stream"):
        return _test_litellm_stream(**kwargs)
    return _test_litellm_response(**kwargs)


if os.environ.get("GOBII_ALLOW_LIVE_TEST_LLM") != "1":
    import litellm as _litellm

    _litellm.completion = _test_litellm_completion

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

# Tests can toggle GOBII_PROPRIETARY_MODE with override_settings after Django
# has already loaded template app directories. Keep proprietary templates in
# test-only DIRS so proprietary views can still render in those cases.
_proprietary_template_dir = BASE_DIR / "proprietary" / "templates"
if _proprietary_template_dir.exists():
    TEMPLATES[0]["DIRS"].append(_proprietary_template_dir)

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

# API tests should verify task creation/enqueueing without launching browser-use
# and its provider clients in Celery eager mode.
BROWSER_USE_TASK_EXECUTION_DISABLED = True

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
