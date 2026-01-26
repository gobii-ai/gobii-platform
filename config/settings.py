"""
Gobii settings – dev profile
"""

from pathlib import Path
from datetime import timedelta
import environ, os
from decimal import Decimal
from typing import Any
from urllib.parse import urlparse
from celery.schedules import crontab
from django.core.exceptions import ImproperlyConfigured

LOG_LEVEL = os.getenv("DJANGO_LOG_LEVEL", "INFO")

GA_MEASUREMENT_ID = os.getenv("GA_MEASUREMENT_ID", "")  # e.g. G-2PCKFMF85B

BASE_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = BASE_DIR.parent
env = environ.Env(
    DEBUG=(bool, False),
)
# loads .env file when running locally, checking possible locations in order of preference.
env_paths_to_check = [
    ROOT_DIR / "infra" / "platform" / "local" / ".env",
    BASE_DIR / ".env",
]

for env_path in env_paths_to_check:
    if env_path.exists():
        environ.Env.read_env(env_path)
        break

# Ensure local dev has a sensible default release environment identifier.
# setdefault means staging/prod/preview, which explicitly pass this variable
# will not be overridden.
os.environ.setdefault("GOBII_RELEASE_ENV", "local")

# Smart local defaults: make developer experience "just work" on laptops
# When not running inside Docker/Compose and release env is local, fill in
# sensible defaults for DB/Redis/Celery and dev keys. Compose and prod provide
# explicit values so these setdefault calls won't override them.
IN_DOCKER = os.path.exists("/.dockerenv") or env.bool("IN_DOCKER", default=False)
RELEASE_ENV = os.getenv("GOBII_RELEASE_ENV", "local")

if RELEASE_ENV == "local" and not IN_DOCKER:
    # Core toggles and keys (non-secret dev defaults)
    os.environ.setdefault("DEBUG", "1")
    os.environ.setdefault("DJANGO_SECRET_KEY", "dev-insecure")
    os.environ.setdefault("GOBII_ENCRYPTION_KEY", "dev-insecure")

    # Postgres (local compose defaults)
    os.environ.setdefault("POSTGRES_HOST", "localhost")
    os.environ.setdefault("POSTGRES_PORT", "5432")
    os.environ.setdefault("POSTGRES_DB", "gobii")
    os.environ.setdefault("POSTGRES_USER", "postgres")
    os.environ.setdefault("POSTGRES_PASSWORD", "postgres")

    # Redis + Celery
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
    os.environ.setdefault("CELERY_BROKER_URL", os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
    os.environ.setdefault("CELERY_RESULT_BACKEND", os.environ.get("REDIS_URL", "redis://localhost:6379/0"))

# Community vs Proprietary build toggle
# - Community Edition (default): minimal external deps, no Turnstile, no email verification
# - Proprietary/Prod: enable Turnstile, real email delivery, and email verification
#
# Licensing notice (important): Proprietary Mode is available only to customers
# who hold a current, valid proprietary software license from Gobii, Inc.
# Enabling or using GOBII_PROPRIETARY_MODE without such a license is not
# permitted and may violate Gobii, Inc.’s intellectual property rights and/or
# applicable license terms. By setting this flag you represent and warrant that
# you are authorized to do so under a written license agreement with Gobii, Inc.
GOBII_PROPRIETARY_MODE = env.bool("GOBII_PROPRIETARY_MODE", default=False)
# In Community Edition, we optionally override limits to be effectively unlimited
# for agents/tasks. Can be disabled (e.g., in tests) via env.
GOBII_ENABLE_COMMUNITY_UNLIMITED = env.bool("GOBII_ENABLE_COMMUNITY_UNLIMITED", default=True)
# Referral credits: if True, credits are granted after referred user completes first task.
# If False, credits are granted immediately at signup. Default True for fraud prevention.
REFERRAL_DEFERRED_GRANT = env.bool("REFERRAL_DEFERRED_GRANT", default=True)
# Master switch for deferred referral credit checks on task completion.
# Set to False to skip the check entirely (useful until referral system is fully implemented).
DEFERRED_REFERRAL_CREDITS_ENABLED = env.bool("DEFERRED_REFERRAL_CREDITS_ENABLED", default=False)
# Allow disabling the first-run setup redirect (e.g., in automated tests)
FIRST_RUN_SETUP_ENABLED = env.bool("FIRST_RUN_SETUP_ENABLED", default=True)
# Permit skipping LLM bootstrap enforcement (useful for non-interactive tests)
LLM_BOOTSTRAP_OPTIONAL = env.bool("LLM_BOOTSTRAP_OPTIONAL", default=False)
# Enable insights system (contextual info during agent processing)
INSIGHTS_ENABLED = env.bool("INSIGHTS_ENABLED", default=True)
# Maximum times we will allow the same provider/model to be preferred consecutively
MAX_PREFERRED_PROVIDER_STREAK = env.int("MAX_PREFERRED_PROVIDER_STREAK", default=3)
# Default timeout (seconds) for LiteLLM requests
LITELLM_TIMEOUT_SECONDS = env.int("LITELLM_TIMEOUT_SECONDS", default=300)
# Retry configuration for transient LiteLLM failures
LITELLM_MAX_RETRIES = env.int("LITELLM_MAX_RETRIES", default=2)
LITELLM_RETRY_BACKOFF_SECONDS = env.float("LITELLM_RETRY_BACKOFF_SECONDS", default=1.0)
# Proxy health check: deactivate after N consecutive failures
PROXY_CONSECUTIVE_FAILURE_THRESHOLD = env.int("PROXY_CONSECUTIVE_FAILURE_THRESHOLD", default=3)
# Decodo inventory alerting
DECODO_LOW_INVENTORY_THRESHOLD = env.int("DECODO_LOW_INVENTORY_THRESHOLD", default=30)
DECODO_LOW_INVENTORY_EMAIL = env(
    "DECODO_LOW_INVENTORY_EMAIL",
    default="support@gobii.ai",
)

try:
    from proprietary import defaults as _proprietary_defaults_module
except ImportError:  # Community builds may not package proprietary defaults
    _proprietary_defaults_module = None


_COMMUNITY_DEFAULTS = {
    "brand": {
        "PUBLIC_DISCORD_URL": "https://discord.gg/yyDB8GwxtE",
        "PUBLIC_X_URL": "https://x.com/gobii_ai",
        "PUBLIC_GITHUB_URL": "https://github.com/gobii-ai",
    }
}


def _proprietary_default(section: str, key: str, *, fallback: str = "") -> str:
    """Fetch a proprietary default without leaking values into community builds."""

    if not GOBII_PROPRIETARY_MODE or _proprietary_defaults_module is None:
        return fallback

    defaults_map = getattr(_proprietary_defaults_module, "DEFAULTS", {})
    section_defaults = defaults_map.get(section, {})
    return section_defaults.get(key, fallback)


def _community_default(section: str, key: str, *, fallback: str = "") -> str:
    """Provide OSS-friendly defaults for select public links."""

    if GOBII_PROPRIETARY_MODE:
        return fallback

    section_defaults = _COMMUNITY_DEFAULTS.get(section, {})
    return section_defaults.get(key, fallback)

# ────────── Core ──────────
DEBUG = env.bool("DEBUG", default=False)
SECRET_KEY = env("DJANGO_SECRET_KEY")
ALLOWED_HOSTS = ["*"]  # tighten in prod

# Default origins differ between OSS/community mode and proprietary deployments.
_COMMUNITY_DEFAULT_TRUSTED_ORIGINS = [
    "http://localhost",
    "http://127.0.0.1",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "https://localhost",
    "https://127.0.0.1",
]
_PROPRIETARY_DEFAULT_TRUSTED_ORIGINS = [
    "https://gobii.ai",
    "https://gobii.ai:443",
    "https://www.gobii.ai",
    "https://www.gobii.ai:443",
    "https://getgobii.com",
    "https://getgobii.com:443",
    "https://www.getgobii.com",
    "https://www.getgobii.com:443",
]
CSRF_TRUSTED_ORIGINS = env.list(
    "CSRF_TRUSTED_ORIGINS",
    default=_PROPRIETARY_DEFAULT_TRUSTED_ORIGINS
    if GOBII_PROPRIETARY_MODE
    else _COMMUNITY_DEFAULT_TRUSTED_ORIGINS,
)
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
X_FRAME_OPTIONS = env("X_FRAME_OPTIONS", default="SAMEORIGIN")
USE_X_FORWARDED_HOST = True
SITE_ID = 1

PUBLIC_BRAND_NAME = env(
    "PUBLIC_BRAND_NAME",
    default=_proprietary_default("brand", "PUBLIC_BRAND_NAME", fallback="Agent Platform"),
)
PUBLIC_SITE_URL = env(
    "PUBLIC_SITE_URL",
    default=_proprietary_default("brand", "PUBLIC_SITE_URL", fallback="http://localhost:8000"),
)


def _cookie_secure_default(site_url: str) -> bool:
    parsed = urlparse((site_url or "").strip())
    scheme = parsed.scheme.lower()
    if scheme == "https":
        return True
    if scheme == "http":
        return False
    if not scheme and parsed.netloc:
        return False
    return not DEBUG


_SECURE_COOKIE_DEFAULT = _cookie_secure_default(PUBLIC_SITE_URL)
SESSION_COOKIE_SECURE = env.bool("SESSION_COOKIE_SECURE", default=_SECURE_COOKIE_DEFAULT)
CSRF_COOKIE_SECURE = env.bool("CSRF_COOKIE_SECURE", default=_SECURE_COOKIE_DEFAULT)

SESSION_COOKIE_NAME = env.str("SESSION_COOKIE_NAME", default="sessionid")
CSRF_COOKIE_NAME = env.str("CSRF_COOKIE_NAME", default="csrftoken")

PUBLIC_CONTACT_EMAIL = env(
    "PUBLIC_CONTACT_EMAIL",
    default=_proprietary_default("brand", "PUBLIC_CONTACT_EMAIL"),
)
PUBLIC_SUPPORT_EMAIL = env(
    "PUBLIC_SUPPORT_EMAIL",
    default=_proprietary_default("brand", "PUBLIC_SUPPORT_EMAIL"),
)
PUBLIC_GITHUB_URL = env(
    "PUBLIC_GITHUB_URL",
    default=_proprietary_default(
        "brand",
        "PUBLIC_GITHUB_URL",
        fallback=_community_default("brand", "PUBLIC_GITHUB_URL"),
    ),
)
PUBLIC_DISCORD_URL = env(
    "PUBLIC_DISCORD_URL",
    default=_proprietary_default(
        "brand",
        "PUBLIC_DISCORD_URL",
        fallback=_community_default("brand", "PUBLIC_DISCORD_URL"),
    ),
)
PUBLIC_X_URL = env(
    "PUBLIC_X_URL",
    default=_proprietary_default(
        "brand",
        "PUBLIC_X_URL",
        fallback=_community_default("brand", "PUBLIC_X_URL"),
    ),
)

INSTALLED_APPS = [
    # Django
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "whitenoise.runserver_nostatic",  # Should be before staticfiles if DEBUG is True and runserver
    "daphne",
    "django.contrib.staticfiles",

    # 3rd-party
    "channels",
    "rest_framework",
    "drf_spectacular",
    "django.contrib.sites",
    # Cloudflare Turnstile (disabled by default in community edition; see TURNSTILE_ENABLED below)
    "djstripe",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.google",
    "storages",
    "django_htmx",
    "waffle",

    # first-party
    "setup",
    "pages",
    "console",
    "api",
    "tests",

    # (no need to list project root as app)
    "anymail",

    # Celery Beat now handled by RedBeat in Redis

    # sitemap support
    "django.contrib.sitemaps",

    "config.apps.TracingInitialization"
]

# Load proprietary overrides (templates, etc.) if enabled
if GOBII_PROPRIETARY_MODE:
    # Prepend so its templates override base/app templates cleanly
    INSTALLED_APPS = ["proprietary", *INSTALLED_APPS]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "middleware.app_shell.AppShellMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "setup.middleware.FirstRunSetupMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "waffle.middleware.WaffleMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
    "middleware.user_id_baggage.UserIdBaggageMiddleware",
    "middleware.fbp_middleware.FbpMiddleware",
    "middleware.utm_capture.UTMTrackingMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.static",
                "django.template.context_processors.i18n",
                "django.template.context_processors.debug",
                "config.context_processors.global_settings_context",
                "pages.context_processors.account_info",
                "pages.context_processors.environment_info",
                "pages.context_processors.show_signup_tracking",
                "pages.context_processors.analytics",
                "pages.context_processors.llm_bootstrap",
                "pages.context_processors.canonical_url",
            ],
            # Manually register project-local template tag libraries
            "libraries": {
                "form_extras": "templatetags.form_extras",
                "analytics_tags": "templatetags.analytics_tags",
                "social_extras": "templatetags.social_extras",
                "vite_tags": "templatetags.vite_tags",
            },
        },
    },
]


ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# ────────── Database ──────────
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB"),
        "USER": env("POSTGRES_USER"),
        "PASSWORD": env("POSTGRES_PASSWORD"),
        "HOST": env("POSTGRES_HOST"),
        "PORT": env("POSTGRES_PORT"),
        # Keep connections alive for a reasonable time; Celery tasks are long-lived
        # and may perform ORM work only at the end. This reduces reconnect churn while
        # still allowing the DB/infra to reap very old connections.
        "CONN_MAX_AGE": env.int("DJANGO_DB_CONN_MAX_AGE", default=0), # Close after each use - pooled at Neon, so
                                                                           # not the overhead you'd think
        # Server-side cursors do not play well with pooled connections; disable them
        # when pooling is active (default) unless explicitly overridden.
        "DISABLE_SERVER_SIDE_CURSORS": env.bool(
            "DJANGO_DB_DISABLE_SERVER_SIDE_CURSORS",
            default=True
        ),
        # Validate recycled connections automatically when re-used by Django
        # (Django will perform a cheap "SELECT 1" on reuse if needed).
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {
            "sslmode": env(
                "POSTGRES_SSLMODE", default=None
            ),  # e.g., 'require', 'verify-full'
            # Optional TCP keepalive tuning to survive NAT/LB idling during long tasks.
            # These can be overridden via environment if needed.
            # libpq expects integer values (0/1) for keepalive flags; avoid booleans which become 'True'/'False'
            "keepalives": env.int("PGTCP_KEEPALIVES", default=1),
            "keepalives_idle": env.int("PGTCP_KEEPALIVES_IDLE", default=60),
            "keepalives_interval": env.int("PGTCP_KEEPALIVES_INTERVAL", default=30),
            "keepalives_count": env.int("PGTCP_KEEPALIVES_COUNT", default=5),
        },
    }
}

# ────────── Static & media ──────────
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = env('STATIC_ROOT', default=BASE_DIR / 'staticfiles')


# ────────── Frontend (Vite) ──────────
VITE_DEV_SERVER_URL = env('VITE_DEV_SERVER_URL', default='http://127.0.0.1:5173')
VITE_USE_DEV_SERVER = env.bool('VITE_USE_DEV_SERVER', default=DEBUG)
VITE_ASSET_ENTRY = env('VITE_ASSET_ENTRY', default='src/main.tsx')
VITE_MANIFEST_PATH = Path(env('VITE_MANIFEST_PATH', default=str(BASE_DIR / 'static' / 'frontend' / 'manifest.json')))
MEDIA_URL = "/media/"
MEDIA_ROOT = env('MEDIA_ROOT', default=BASE_DIR / 'mediafiles')

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {"location": MEDIA_ROOT, "base_url": MEDIA_URL},
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

if DEBUG:
    STORAGES["staticfiles"][
        "BACKEND"
    ] = "whitenoise.storage.CompressedStaticFilesStorage"
else:
    STORAGES["staticfiles"][
        "BACKEND"
    ] = "whitenoise.storage.CompressedManifestStaticFilesStorage"

# Environment Variables for Cloud Storage
STORAGE_BACKEND_TYPE = env("STORAGE_BACKEND_TYPE", default="LOCAL")

# GCS Variables
GS_BUCKET_NAME = env("GS_BUCKET_NAME", default=None)
GS_PROJECT_ID = env("GS_PROJECT_ID", default=None)
GS_DEFAULT_ACL = env("GS_DEFAULT_ACL", default="projectPrivate")
GS_QUERYSTRING_AUTH = env.bool("GS_QUERYSTRING_AUTH", default=False)

# S3 Variables
AWS_ACCESS_KEY_ID = env("AWS_ACCESS_KEY_ID", default=None)
AWS_SECRET_ACCESS_KEY = env("AWS_SECRET_ACCESS_KEY", default=None)
AWS_STORAGE_BUCKET_NAME = env("AWS_STORAGE_BUCKET_NAME", default=None)
AWS_S3_REGION_NAME = env("AWS_S3_REGION_NAME", default=None)
AWS_S3_ENDPOINT_URL = env("AWS_S3_ENDPOINT_URL", default=None)
AWS_S3_OBJECT_PARAMETERS = {
    "CacheControl": env("AWS_S3_CACHE_CONTROL", default="max-age=86400")
}
AWS_DEFAULT_ACL = env("AWS_DEFAULT_ACL", default=None)
AWS_QUERYSTRING_AUTH = env.bool("AWS_QUERYSTRING_AUTH", default=False)
AWS_S3_ADDRESSING_STYLE = env(
    "AWS_S3_ADDRESSING_STYLE", default="auto"
)  # Recommended for MinIO path style

# --- Conditional Cloud Storage Overrides ---
if STORAGE_BACKEND_TYPE == "GCS":
    if not GS_BUCKET_NAME:
        raise ImproperlyConfigured("GS_BUCKET_NAME must be set when using GCS storage.")

    STORAGES["default"] = {
        "BACKEND": "storages.backends.gcloud.GoogleCloudStorage",
        "OPTIONS": {
            "bucket_name": GS_BUCKET_NAME,
            "project_id": GS_PROJECT_ID,
            "location": "media",
            "default_acl": GS_DEFAULT_ACL,
            "querystring_auth": GS_QUERYSTRING_AUTH,
        },
    }
    # Static files continue to be served by WhiteNoise as configured above

elif STORAGE_BACKEND_TYPE == "S3":
    if not AWS_STORAGE_BUCKET_NAME:
        raise ImproperlyConfigured(
            "AWS_STORAGE_BUCKET_NAME must be set when using S3 storage."
        )

    STORAGES["default"] = {
        "BACKEND": "storages.backends.s3boto3.S3Boto3Storage",
        "OPTIONS": {
            "access_key": AWS_ACCESS_KEY_ID,
            "secret_key": AWS_SECRET_ACCESS_KEY,
            "bucket_name": AWS_STORAGE_BUCKET_NAME,
            "region_name": AWS_S3_REGION_NAME,
            "endpoint_url": AWS_S3_ENDPOINT_URL,
            "object_parameters": AWS_S3_OBJECT_PARAMETERS,
            "default_acl": AWS_DEFAULT_ACL,
            "querystring_auth": AWS_QUERYSTRING_AUTH,
            "location": "media",
            "addressing_style": AWS_S3_ADDRESSING_STYLE,
        },
    }
    STORAGES["staticfiles"] = { # S3 overrides WhiteNoise for static files if S3 is chosen
            "BACKEND": "storages.backends.s3boto3.S3Boto3Storage",
            "OPTIONS": {
                "access_key": AWS_ACCESS_KEY_ID,
                "secret_key": AWS_SECRET_ACCESS_KEY,
                "bucket_name": AWS_STORAGE_BUCKET_NAME,
                "region_name": AWS_S3_REGION_NAME,
                "endpoint_url": AWS_S3_ENDPOINT_URL,
                "object_parameters": AWS_S3_OBJECT_PARAMETERS,
                "default_acl": "public-read",
                "querystring_auth": AWS_QUERYSTRING_AUTH,
                "location": "static",
                "addressing_style": AWS_S3_ADDRESSING_STYLE,
            },
        }


# ────────── Auth ──────────
AUTHENTICATION_BACKENDS = (
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
)
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]

# Domains declined during signup (lowercase, comma separated via env override)
SIGNUP_BLOCKED_EMAIL_DOMAINS = [
    domain.strip().lower()
    for domain in env(
        "SIGNUP_BLOCKED_EMAIL_DOMAINS",
        default="mailslurp.biz",
    ).split(",")
    if domain.strip()
]

# Mailgun credentials only exist in hosted/prod environments; local proprietary
# runs typically omit them. Use that to decide whether to enforce email
# verification, while still allowing an explicit override via ENV.
MAILGUN_API_KEY = env("MAILGUN_API_KEY", default="")
MAILGUN_HAS_API_KEY = bool(MAILGUN_API_KEY)
MAILGUN_ENABLED = env.bool(
    "MAILGUN_ENABLED",
    default=GOBII_PROPRIETARY_MODE and MAILGUN_HAS_API_KEY,
)

# Community Edition disables email verification by default to avoid external email providers
ACCOUNT_EMAIL_VERIFICATION = env(
    "ACCOUNT_EMAIL_VERIFICATION",
    default="optional" if GOBII_PROPRIETARY_MODE and MAILGUN_API_KEY else "none",
)
ACCOUNT_LOGOUT_ON_GET = True
ACCOUNT_ADAPTER = "config.account_adapter.GobiiAccountAdapter"
SOCIALACCOUNT_ADAPTER = "config.socialaccount_adapter.GobiiSocialAccountAdapter"

# TODO: Test the removal of this; got deprecation warning
#ACCOUNT_EMAIL_REQUIRED = True
ACCOUNT_UNIQUE_EMAIL  = True

ACCOUNT_CONFIRM_EMAIL_ON_GET = True  # auto-confirm as soon as user hits the link
ACCOUNT_DEFAULT_HTTP_PROTOCOL = env.str("ACCOUNT_DEFAULT_HTTP_PROTOCOL", "https")
ACCOUNT_EMAIL_CONFIRMATION_EXPIRE_DAYS = 10

SOCIALACCOUNT_PROVIDERS = {
    "google": {
        "AUTH_PARAMS": {
            "prompt": "select_account",
        },
    },
}


LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/"

# Integrate Cloudflare Turnstile with django-allauth ✨
TURNSTILE_ENABLED = env.bool("TURNSTILE_ENABLED", default=GOBII_PROPRIETARY_MODE)

# Conditionally enable Cloudflare Turnstile app and forms
if TURNSTILE_ENABLED:
    INSTALLED_APPS.append("turnstile")  # type: ignore[arg-type]
    ACCOUNT_FORMS = {
        "signup": "turnstile_signup.SignupFormWithTurnstile",
        "login": "turnstile_signup.LoginFormWithTurnstile",
    }

# Optional: allow using dummy keys in dev; override in env for prod
TURNSTILE_SITEKEY = env("TURNSTILE_SITEKEY", default="1x00000000000000000000AA")
# Cloudflare's published Turnstile test secret is longer than the sitekey; using
# the shorter value caused server-side verification to fail even for the dummy
# widget. Keep the documented secret as the default so local proprietary mode
# logins succeed without extra configuration.
TURNSTILE_SECRET = env(
    "TURNSTILE_SECRET", default="1x0000000000000000000000000000000AA"
)

# Cloudflare Turnstile widget defaults (light theme, normal size)
TURNSTILE_DEFAULT_CONFIG = {
    "theme": "light",
    "size": "normal",
}

# ────────── DRF / OpenAPI ──────────
REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "api.auth.APIKeyAuthentication",
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.TokenAuthentication",
    ],
    "EXCEPTION_HANDLER": "api.exceptions.json_exception_handler",
}
SPECTACULAR_SETTINGS = {
    "TITLE": "Gobii API",
    "VERSION": "0.1.0",
    "DESCRIPTION": "API for Gobii AI browser agents platform",
    "SCHEMA_PATH_PREFIX": r"/api/v[0-9]",
    "SCHEMA_PATH_PREFIX_TRIM": True,
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": False,  # Prevents nesting in inline serializers
    "COMPONENT_NO_READ_ONLY_REQUIRED": True,  # Prevents read-only fields from being marked as required
    "COMPONENT_SPLIT_PATCH": True,  # Creates separate components for PATCH endpoints
    "CAMELIZE_NAMES": True,  # Ensures consistent case in generated types
    "POSTPROCESSING_HOOKS": ["drf_spectacular.hooks.postprocess_schema_enums"],
    # Enum names are auto-detected
    # Override operationIds to use cleaner function names
    "OPERATION_ID_MAPPING": {
        "pattern": None  # Use just the operation name (get, list, etc.)
    },
    # Servers definition for default base URL in client
    "SERVERS": [{"url": "https://gobii.ai/api/v1", "description": "Production server"}],
    # Tags for API organization
    "TAGS": [
        {"name": "browser-use", "description": "Browser Use Agent operations and tasks"},
        {"name": "utils", "description": "Utility operations"}
    ]
}

# ────────── Redis ──────────
REDIS_URL = env("REDIS_URL")

# Channels uses Redis for cross-process messaging (WebSockets, background broadcasts).
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {"hosts": [REDIS_URL]},
    }
}

# ────────── Celery ──────────
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_TASK_TIME_LIMIT = 14400  # 4 hours for web task processing
CELERY_TASK_SOFT_TIME_LIMIT = 12600  # 3.5 hours soft limit
CELERY_BEAT_SCHEDULE = {
    # Daily task to grant monthly free credits to users. Subscription users are updated when stripe pushes to webhook
    "grant_monthly_free_credits": {
        "task": "api.tasks.grant_monthly_free_credits",
        "schedule": crontab(minute=5, hour=0),
    },
    # Hourly garbage collection of timed-out tasks
    "garbage-collect-timed-out-tasks": {
        "task": "api.tasks.maintenance_tasks.garbage_collect_timed_out_tasks",
        "schedule": crontab(minute=30),  # Run at 30 minutes past every hour
        "options": {
            "expires": 3600,  # Task expires after 1 hour to prevent queueing
            "routing_key": "celery.single_instance",  # Use single instance routing to prevent overlaps
        },
    },
    "prune-prompt-archives": {
        "task": "api.tasks.maintenance_tasks.prune_prompt_archives",
        "schedule": crontab(minute=15, hour=2),  # Nightly cleanup at 02:15 UTC
        "options": {
            "routing_key": "celery.single_instance",
        },
    },
    "sandbox-compute-idle-sweep": {
        "task": "api.tasks.sandbox_compute.sweep_idle_sessions",
        "schedule": timedelta(seconds=env.int("SANDBOX_COMPUTE_IDLE_SWEEP_INTERVAL_SECONDS", default=300)),
        "options": {
            "routing_key": "celery.single_instance",
        },
    },
}

# Conditionally enable Twilio sync task only when explicitly enabled
TWILIO_ENABLED = env.bool("TWILIO_ENABLED", default=False)
if TWILIO_ENABLED:
    CELERY_BEAT_SCHEDULE["twilio-sync-numbers"] = {
        "task": "api.tasks.sms_tasks.sync_twilio_numbers",
        "schedule": crontab(minute="*/60"),   # hourly
    }

# RedBeat scheduler configuration
CELERY_BEAT_SCHEDULER = "redbeat.RedBeatScheduler"
CELERY_TIMEZONE       = "UTC"
CELERY_ENABLE_UTC     = True

# ────────── Misc ──────────
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ────────── Soft Expiration Settings ──────────
# Number of days of inactivity before a free-plan agent is soft-expired
AGENT_SOFT_EXPIRATION_INACTIVITY_DAYS = env.int("AGENT_SOFT_EXPIRATION_INACTIVITY_DAYS", default=30)
# Hours of grace after a user downgrades to Free before expiration checks apply
AGENT_SOFT_EXPIRATION_DOWNGRADE_GRACE_HOURS = env.int("AGENT_SOFT_EXPIRATION_DOWNGRADE_GRACE_HOURS", default=48)
# Retention window for persisted prompt archives
PROMPT_ARCHIVE_RETENTION_DAYS = env.int("PROMPT_ARCHIVE_RETENTION_DAYS", default=14)

# ────────── Free Plan Cron Throttle Settings ──────────
# These settings control exponential backoff for cron-triggered scheduled runs on
# free-plan agents. The throttle starts at 2× the agent's configured schedule
# interval once the agent is older than START_AGE_DAYS, then doubles every
# STAGE_DAYS until capped at MAX_INTERVAL_DAYS.
AGENT_CRON_THROTTLE_START_AGE_DAYS = env.int("AGENT_CRON_THROTTLE_START_AGE_DAYS", default=16)
AGENT_CRON_THROTTLE_STAGE_DAYS = env.int("AGENT_CRON_THROTTLE_STAGE_DAYS", default=7)
AGENT_CRON_THROTTLE_MAX_INTERVAL_DAYS = env.int("AGENT_CRON_THROTTLE_MAX_INTERVAL_DAYS", default=30)
# Rate-limit the throttle notice so users do not get spammed when triggers are skipped.
AGENT_CRON_THROTTLE_NOTICE_TTL_DAYS = env.int("AGENT_CRON_THROTTLE_NOTICE_TTL_DAYS", default=7)

# ────────── Agent Event Processing Lock Settings ──────────
AGENT_EVENT_PROCESSING_LOCK_TIMEOUT_SECONDS = env.int(
    "AGENT_EVENT_PROCESSING_LOCK_TIMEOUT_SECONDS",
    default=900,
)
AGENT_EVENT_PROCESSING_LOCK_EXTEND_INTERVAL_SECONDS = env.int(
    "AGENT_EVENT_PROCESSING_LOCK_EXTEND_INTERVAL_SECONDS",
    default=max(30, AGENT_EVENT_PROCESSING_LOCK_TIMEOUT_SECONDS // 2),
)
AGENT_EVENT_PROCESSING_LOCK_ACQUIRE_TIMEOUT_SECONDS = env.float(
    "AGENT_EVENT_PROCESSING_LOCK_ACQUIRE_TIMEOUT_SECONDS",
    default=1.0,
)
AGENT_EVENT_PROCESSING_LOCK_MAX_EXTENSIONS = env.int(
    "AGENT_EVENT_PROCESSING_LOCK_MAX_EXTENSIONS",
    default=200,
)
AGENT_EVENT_PROCESSING_HEARTBEAT_TTL_SECONDS = env.int(
    "AGENT_EVENT_PROCESSING_HEARTBEAT_TTL_SECONDS",
    default=max(AGENT_EVENT_PROCESSING_LOCK_TIMEOUT_SECONDS, 600),
)
AGENT_EVENT_PROCESSING_MAX_RUNTIME_SECONDS = env.int(
    "AGENT_EVENT_PROCESSING_MAX_RUNTIME_SECONDS",
    default=1800,
)
AGENT_EVENT_PROCESSING_PENDING_SET_TTL_SECONDS = env.int(
    "AGENT_EVENT_PROCESSING_PENDING_SET_TTL_SECONDS",
    default=env.int(
        "AGENT_EVENT_PROCESSING_PENDING_TTL_SECONDS",
        default=max(AGENT_EVENT_PROCESSING_LOCK_TIMEOUT_SECONDS * 2, 600),
    ),
)
AGENT_EVENT_PROCESSING_PENDING_DRAIN_DELAY_SECONDS = env.int(
    "AGENT_EVENT_PROCESSING_PENDING_DRAIN_DELAY_SECONDS",
    default=env.int(
        "AGENT_EVENT_PROCESSING_PENDING_FALLBACK_DELAY_SECONDS",
        default=5,
    ),
)
AGENT_EVENT_PROCESSING_PENDING_DRAIN_LIMIT = env.int(
    "AGENT_EVENT_PROCESSING_PENDING_DRAIN_LIMIT",
    default=50,
)
AGENT_EVENT_PROCESSING_PENDING_DRAIN_SCHEDULE_TTL_SECONDS = env.int(
    "AGENT_EVENT_PROCESSING_PENDING_DRAIN_SCHEDULE_TTL_SECONDS",
    default=max(30, AGENT_EVENT_PROCESSING_PENDING_DRAIN_DELAY_SECONDS * 6),
)
AGENT_WEB_TASK_ACTIVE_MAX_AGE_SECONDS = env.int(
    "AGENT_WEB_TASK_ACTIVE_MAX_AGE_SECONDS",
    default=4 * 60 * 60,
)
# Max age for appending "X ago" suffixes to agent prompt message headers
AGENT_RECENT_MINUTES_SUFFIX_MAX_AGE_SECONDS = env.int(
    "AGENT_RECENT_MINUTES_SUFFIX_MAX_AGE_SECONDS",
    default=1800,
)

# Backwards-compatible aliases for legacy settings names.
AGENT_EVENT_PROCESSING_PENDING_TTL_SECONDS = AGENT_EVENT_PROCESSING_PENDING_SET_TTL_SECONDS
AGENT_EVENT_PROCESSING_PENDING_FALLBACK_DELAY_SECONDS = AGENT_EVENT_PROCESSING_PENDING_DRAIN_DELAY_SECONDS

# Feature flags (django-waffle)
# Default to explicit management in admin; core features are not gated anymore.
# You can still override with WAFFLE_FLAG_DEFAULT=1 in environments where you want missing flags active.
WAFFLE_FLAG_DEFAULT = env.bool("WAFFLE_FLAG_DEFAULT", default=False)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,

    # ---------------- Handlers ----------------
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
            "stream": "ext://sys.stdout",  # default is stderr; explicit is nice
        },
    },

    # --------------- Formatters ---------------
    "formatters": {
        "verbose": {
            "format": "{asctime} [{levelname}] {name}: {message}",
            "style": "{",
        },
    },

    # --------------- Root logger --------------
    "root": {
        "handlers": ["console"],
        "level": LOG_LEVEL,              # affects everything that propagates up
    },

    # --------------- Other loggers -----------
    "loggers": {
        # Core Django (requests, system checks, etc.)
        "django": {
            "handlers": ["console"],
            "level": LOG_LEVEL,
            "propagate": False,         # prevent double-logging
        },

        # Optional: dump SQL when you set DJANGO_SQL_DEBUG=1
        "django.db.backends": {
            "handlers": ["console"],
            "level": "DEBUG" if os.getenv("DJANGO_SQL_DEBUG") else "INFO",
            "propagate": False,
        },
    },
}


# ────────── Browser runtime ──────────
# Controls whether the Playwright/Chrome context runs headless.
# - Can be overridden with env var BROWSER_HEADLESS=true|false
# - Defaults to False. Headed is necessary in a production environment to reduce bot detection. In a dev environment,
#   headless is preferred to prevent a browser window from popping up periodically as the agent runs.
BROWSER_HEADLESS = env.bool("BROWSER_HEADLESS", default=False)
# Maximum steps a browser task is allowed to execute before forcing completion.
BROWSER_AGENT_MAX_STEPS = env.int("BROWSER_AGENT_MAX_STEPS", default=100)
# Maximum allowed concurrently active browser tasks per agent.
BROWSER_AGENT_MAX_ACTIVE_TASKS = env.int("BROWSER_AGENT_MAX_ACTIVE_TASKS", default=3)
# Maximum number of browser tasks an agent may spawn per calendar day.
BROWSER_AGENT_DAILY_MAX_TASKS = env.int("BROWSER_AGENT_DAILY_MAX_TASKS", default=60)
# Preferred vision detail level to request from browser_use when vision is enabled.
BROWSER_AGENT_VISION_DETAIL_LEVEL = env("BROWSER_AGENT_VISION_DETAIL_LEVEL", default="auto")

SOCIALACCOUNT_LOGIN_ON_GET = True

# Proprietary mode uses Mailgun in production, but devs often run locally without
# credentials. Fall back to the console backend when no API key is configured so
# login/signup flows do not hard-error while still exercising the email code.
EMAIL_BACKEND = (
    "anymail.backends.mailgun.EmailBackend"
    if MAILGUN_ENABLED
    else "django.core.mail.backends.console.EmailBackend"
)

MAILGUN_SENDER_DOMAIN = env(
    "MAILGUN_SENDER_DOMAIN",
    default=_proprietary_default("support", "MAILGUN_SENDER_DOMAIN"),
)

POSTMARK_SERVER_TOKEN = env("POSTMARK_SERVER_TOKEN", default="")
POSTMARK_ENABLED = env.bool(
    "POSTMARK_ENABLED",
    default=GOBII_PROPRIETARY_MODE and bool(POSTMARK_SERVER_TOKEN),
)

ANYMAIL: dict[str, Any] = {}

if MAILGUN_ENABLED:
    ANYMAIL["MAILGUN_API_KEY"] = MAILGUN_API_KEY
    # If you chose the EU region add:
    # "MAILGUN_API_URL": "https://api.eu.mailgun.net/v3",
    if MAILGUN_SENDER_DOMAIN:
        ANYMAIL["MAILGUN_SENDER_DOMAIN"] = MAILGUN_SENDER_DOMAIN  # type: ignore[index]

if POSTMARK_ENABLED:
    ANYMAIL["POSTMARK_SERVER_TOKEN"] = POSTMARK_SERVER_TOKEN

DEFAULT_FROM_EMAIL = env(
    "DEFAULT_FROM_EMAIL",
    default=_proprietary_default(
        "support",
        "DEFAULT_FROM_EMAIL",
        fallback=f"{PUBLIC_BRAND_NAME} <no-reply@example.invalid>",
    ),
)
SERVER_EMAIL = env("SERVER_EMAIL", default=DEFAULT_FROM_EMAIL)
ACCOUNT_EMAIL_SUBJECT_PREFIX = env(
    "ACCOUNT_EMAIL_SUBJECT_PREFIX",
    default=f"[{PUBLIC_BRAND_NAME}] " if PUBLIC_BRAND_NAME else "",
)

# dj-stripe / Stripe configuration
STRIPE_LIVE_SECRET_KEY = env("STRIPE_LIVE_SECRET_KEY", default="")
STRIPE_TEST_SECRET_KEY = env("STRIPE_TEST_SECRET_KEY", default="")
STRIPE_LIVE_MODE = env.bool("STRIPE_LIVE_MODE", default=False)  # Set to True in production
STRIPE_KEYS_PRESENT = bool(STRIPE_LIVE_SECRET_KEY or STRIPE_TEST_SECRET_KEY)
STRIPE_ENABLED = env.bool(
    "STRIPE_ENABLED",
    default=GOBII_PROPRIETARY_MODE and STRIPE_KEYS_PRESENT,
)
if not STRIPE_ENABLED:
    STRIPE_DISABLED_REASON = (
        "Stripe keys not configured"
        if not STRIPE_KEYS_PRESENT
        else "Stripe disabled by configuration"
    )
else:
    STRIPE_DISABLED_REASON = ""

STRIPE_CUSTOMER_PORTAL = env("STRIPE_CUSTOMER_PORTAL", default="")

DJSTRIPE_WEBHOOK_SECRET = env("STRIPE_WEBHOOK_SECRET", default="whsec_dummy")
DJSTRIPE_FOREIGN_KEY_TO_FIELD = "id"
DJSTRIPE_USE_NATIVE_JSONFIELD = True

# Credits configuration
# These environment defaults seed the DB-backed configuration.
# Keep as Decimal to support fractional credits (e.g., 0.1).
CREDITS_PER_TASK = Decimal(env("CREDITS_PER_TASK", default="0.4"))
DEFAULT_AGENT_DAILY_CREDIT_TARGET = env.int("DEFAULT_AGENT_DAILY_CREDIT_TARGET", default=5)
PAID_AGENT_DAILY_CREDIT_TARGET = env.int("PAID_AGENT_DAILY_CREDIT_TARGET", default=10)

# Optional per-tool credit overrides (case-insensitive keys).
# Example: {"search_web": Decimal("0.10"), "http_request": Decimal("0.05")}.
# Values are migrated into the database on deployment and serve only as fallback.
TOOL_CREDIT_COSTS = {
    "update_charter": Decimal("0.04"),
    "update_schedule": Decimal("0.04"),
    "sqlite_batch": Decimal("0.2"),
}

# Analytics
SEGMENT_WRITE_KEY = env(
    "SEGMENT_WRITE_KEY",
    default=_proprietary_default("analytics", "SEGMENT_WRITE_KEY"),
)
SEGMENT_WEB_WRITE_KEY = env(
    "SEGMENT_WEB_WRITE_KEY",
    default=_proprietary_default(
        "analytics",
        "SEGMENT_WEB_WRITE_KEY",
        fallback=SEGMENT_WRITE_KEY,
    ),
)

# Ad/Pixel IDs (empty disables)
REDDIT_PIXEL_ID = env(
    "REDDIT_PIXEL_ID",
    default=_proprietary_default("analytics", "REDDIT_PIXEL_ID"),
)
META_PIXEL_ID = env(
    "META_PIXEL_ID",
    default=_proprietary_default("analytics", "META_PIXEL_ID"),
)
LINKEDIN_PARTNER_ID = env(
    "LINKEDIN_PARTNER_ID",
    default=_proprietary_default("analytics", "LINKEDIN_PARTNER_ID"),
)

TIKTOK_PIXEL_ID = env(
    "TIKTOK_PIXEL_ID",
    default=_proprietary_default("analytics", "TIKTOK_PIXEL_ID"),
)

TIKTOK_ACCESS_TOKEN = env(
    "TIKTOK_ACCESS_TOKEN",
    default=_proprietary_default("analytics", "TIKTOK_ACCESS_TOKEN"),
)
TIKTOK_TEST_EVENT_CODE = env("TIKTOK_TEST_EVENT_CODE", default="")
TIKTOK_CAPI_TEST_MODE = env.bool("TIKTOK_CAPI_TEST_MODE", default=False)

FACEBOOK_PIXEL_ID = env(
    "FACEBOOK_PIXEL_ID",
    default=META_PIXEL_ID,
)
FACEBOOK_ACCESS_TOKEN = env("FACEBOOK_ACCESS_TOKEN", default="")
FACEBOOK_TEST_EVENT_CODE = env("FACEBOOK_TEST_EVENT_CODE", default="")
FACEBOOK_CAPI_TEST_MODE = env.bool("FACEBOOK_CAPI_TEST_MODE", default=False)

REDDIT_ADVERTISER_ID = env(
    "REDDIT_ADVERTISER_ID",
    default=REDDIT_PIXEL_ID,
)
REDDIT_ACCESS_TOKEN = env("REDDIT_ACCESS_TOKEN", default="")
REDDIT_CLIENT_ID = env("REDDIT_CLIENT_ID", default="")
REDDIT_CLIENT_SECRET = env("REDDIT_CLIENT_SECRET", default="")
REDDIT_REFRESH_TOKEN = env("REDDIT_REFRESH_TOKEN", default="")
REDDIT_USER_AGENT = env("REDDIT_USER_AGENT", default="gobii-platform/1.0")
REDDIT_TEST_EVENT_CODE = env("REDDIT_TEST_EVENT_CODE", default="")
REDDIT_CAPI_TEST_MODE = env.bool("REDDIT_CAPI_TEST_MODE", default=False)

# Marketing/CAPI value tuning
CAPI_LTV_MULTIPLE = env.float("CAPI_LTV_MULTIPLE", default=5.0)
CAPI_REGISTRATION_VALUE = env.float("CAPI_REGISTRATION_VALUE", default=10.0)

LINKEDIN_SIGNUP_CONVERSION_ID = env(
    "LINKEDIN_SIGNUP_CONVERSION_ID",
    default=_proprietary_default("analytics", "LINKEDIN_SIGNUP_CONVERSION_ID"),
)

# Task Credit Settings
INITIAL_TASK_CREDIT_EXPIRATION_DAYS=env("INITIAL_TASK_CREDIT_EXPIRATION_DAYS", default=30, cast=int)

# Support
SUPPORT_EMAIL = env(
    "SUPPORT_EMAIL",
    default=_proprietary_default("support", "SUPPORT_EMAIL"),
)

# OpenTelemetry Tracing
OTEL_EXPORTER_OTLP_PROTOCOL = env("OTEL_EXPORTER_OTLP_PROTOCOL", default="http/protobuf")
OTEL_EXPORTER_OTLP_ENDPOINT = env("OTEL_EXPORTER_OTLP_ENDPOINT", default="http://localhost:4317")
OTEL_PYTHON_LOGGING_AUTO_INSTRUMENTATION_ENABLED = env("OTEL_PYTHON_LOGGING_AUTO_INSTRUMENTATION_ENABLED", default="True")
OTEL_EXPORTER_OTLP_INSECURE = env.bool("OTEL_EXPORTER_OTLP_INSECURE", default=False)
OTEL_EXPORTER_OTLP_LOG_ENDPOINT = env("OTEL_EXPORTER_OTLP_LOG_ENDPOINT", default="http://localhost:4318/v1/logs")

# Postmark Inbound Webhook Token - this is a token we create, and add to header on email open/click webhooks in Postmark
# Infuriatingly, Postmark does not allow you to set it as a header for inbound delivery webhooks, so we have to use a query

# ────────── IMAP IDLE Runner ──────────
# Global enable for the management-command based IDLE watcher.
IMAP_IDLE_ENABLED = env.bool("IMAP_IDLE_ENABLED", default=False)
# Max local watchers per runner process; scale horizontally with multiple runners.
IMAP_IDLE_MAX_CONNECTIONS = env.int("IMAP_IDLE_MAX_CONNECTIONS", default=200)
# How often to rescan the DB for accounts to watch (seconds)
IMAP_IDLE_SCAN_INTERVAL_SEC = env.int("IMAP_IDLE_SCAN_INTERVAL_SEC", default=30)
# Re-issue IDLE at this interval to avoid server timeouts (seconds; ~25 minutes default)
IMAP_IDLE_REISSUE_SEC = env.int("IMAP_IDLE_REISSUE_SEC", default=1500)
# Debounce window to avoid enqueuing duplicate polls on bursty IDLE events (seconds)
IMAP_IDLE_DEBOUNCE_SEC = env.int("IMAP_IDLE_DEBOUNCE_SEC", default=10)
# Cross-runner lease TTL (seconds). Watchers refresh this periodically to ensure single watcher per account.
IMAP_IDLE_LEASE_TTL_SEC = env.int("IMAP_IDLE_LEASE_TTL_SEC", default=60)
# parameter on that one
POSTMARK_INCOMING_WEBHOOK_TOKEN = env("POSTMARK_INCOMING_WEBHOOK_TOKEN", default="dummy-postmark-incoming-token")
MAILGUN_INCOMING_WEBHOOK_TOKEN = env("MAILGUN_INCOMING_WEBHOOK_TOKEN", default="dummy-mailgun-incoming-token")

EXA_SEARCH_API_KEY = env("EXA_SEARCH_API_KEY", default="dummy-exa-search-api-key")
CAPSOLVER_API_KEY = env("CAPSOLVER_API_KEY", default="")

GOBII_RELEASE_ENV = env("GOBII_RELEASE_ENV", default="local")

# ────────── Sandbox Compute ──────────
SANDBOX_COMPUTE_ENABLED = env.bool("SANDBOX_COMPUTE_ENABLED", default=False)
SANDBOX_COMPUTE_BACKEND = env(
    "SANDBOX_COMPUTE_BACKEND",
    default=("local" if SANDBOX_COMPUTE_ENABLED else ""),
)
SANDBOX_COMPUTE_API_URL = env("SANDBOX_COMPUTE_API_URL", default="")
SANDBOX_COMPUTE_API_TOKEN = env("SANDBOX_COMPUTE_API_TOKEN", default="")
SANDBOX_COMPUTE_K8S_API_URL = env("SANDBOX_COMPUTE_K8S_API_URL", default="")
SANDBOX_COMPUTE_K8S_NAMESPACE = env("SANDBOX_COMPUTE_K8S_NAMESPACE", default="")
SANDBOX_COMPUTE_K8S_TIMEOUT_SECONDS = env.int("SANDBOX_COMPUTE_K8S_TIMEOUT_SECONDS", default=30)
SANDBOX_COMPUTE_POD_IMAGE = env(
    "SANDBOX_COMPUTE_POD_IMAGE",
    default="ghcr.io/gobii-ai/gobii-sandbox-compute:main",
)
SANDBOX_COMPUTE_POD_SERVICE_ACCOUNT = env(
    "SANDBOX_COMPUTE_POD_SERVICE_ACCOUNT",
    default="gobii-sa",
)
SANDBOX_COMPUTE_POD_RUNTIME_CLASS = env(
    "SANDBOX_COMPUTE_POD_RUNTIME_CLASS",
    default="gvisor",
)
SANDBOX_COMPUTE_POD_CONFIGMAP_NAME = env(
    "SANDBOX_COMPUTE_POD_CONFIGMAP_NAME",
    default="gobii-sandbox-common-env",
)
SANDBOX_COMPUTE_POD_SECRET_NAME = env(
    "SANDBOX_COMPUTE_POD_SECRET_NAME",
    default="gobii-sandbox-env",
)
SANDBOX_EGRESS_PROXY_POD_IMAGE = env(
    "SANDBOX_EGRESS_PROXY_POD_IMAGE",
    default="ghcr.io/gobii-ai/gobii-sandbox-egress-proxy:main",
)
SANDBOX_EGRESS_PROXY_POD_PORT = env.int("SANDBOX_EGRESS_PROXY_POD_PORT", default=3128)
SANDBOX_EGRESS_PROXY_SERVICE_PORT = env.int("SANDBOX_EGRESS_PROXY_SERVICE_PORT", default=3128)
SANDBOX_EGRESS_PROXY_POD_RUNTIME_CLASS = env(
    "SANDBOX_EGRESS_PROXY_POD_RUNTIME_CLASS",
    default="",
)
SANDBOX_EGRESS_PROXY_POD_SERVICE_ACCOUNT = env(
    "SANDBOX_EGRESS_PROXY_POD_SERVICE_ACCOUNT",
    default="",
)
SANDBOX_COMPUTE_POD_READY_TIMEOUT_SECONDS = env.int(
    "SANDBOX_COMPUTE_POD_READY_TIMEOUT_SECONDS",
    default=60,
)
SANDBOX_COMPUTE_PVC_SIZE = env("SANDBOX_COMPUTE_PVC_SIZE", default="1Gi")
SANDBOX_COMPUTE_PVC_STORAGE_CLASS = env("SANDBOX_COMPUTE_PVC_STORAGE_CLASS", default="")
SANDBOX_COMPUTE_SNAPSHOT_CLASS = env("SANDBOX_COMPUTE_SNAPSHOT_CLASS", default="")
SANDBOX_COMPUTE_SNAPSHOT_TIMEOUT_SECONDS = env.int(
    "SANDBOX_COMPUTE_SNAPSHOT_TIMEOUT_SECONDS",
    default=60,
)
SANDBOX_COMPUTE_IDLE_TTL_SECONDS = env.int("SANDBOX_COMPUTE_IDLE_TTL_SECONDS", default=60 * 60)
SANDBOX_COMPUTE_IDLE_SWEEP_INTERVAL_SECONDS = env.int(
    "SANDBOX_COMPUTE_IDLE_SWEEP_INTERVAL_SECONDS",
    default=300,
)
SANDBOX_COMPUTE_WORKSPACE_LIMIT_BYTES = env.int(
    "SANDBOX_COMPUTE_WORKSPACE_LIMIT_BYTES",
    default=1024 * 1024 * 1024,
)
SANDBOX_COMPUTE_RUN_COMMAND_TIMEOUT_SECONDS = env.int(
    "SANDBOX_COMPUTE_RUN_COMMAND_TIMEOUT_SECONDS",
    default=120,
)
SANDBOX_COMPUTE_PYTHON_DEFAULT_TIMEOUT_SECONDS = env.int(
    "SANDBOX_COMPUTE_PYTHON_DEFAULT_TIMEOUT_SECONDS",
    default=30,
)
SANDBOX_COMPUTE_PYTHON_MAX_TIMEOUT_SECONDS = env.int(
    "SANDBOX_COMPUTE_PYTHON_MAX_TIMEOUT_SECONDS",
    default=120,
)
SANDBOX_COMPUTE_HTTP_TIMEOUT_SECONDS = env.int(
    "SANDBOX_COMPUTE_HTTP_TIMEOUT_SECONDS",
    default=180,
)
SANDBOX_COMPUTE_STDIO_MAX_BYTES = env.int(
    "SANDBOX_COMPUTE_STDIO_MAX_BYTES",
    default=1024 * 1024,
)
SANDBOX_COMPUTE_ALLOWED_ENV_KEYS = env.list(
    "SANDBOX_COMPUTE_ALLOWED_ENV_KEYS",
    default=[
        "PATH",
        "HOME",
        "USER",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TMPDIR",
        "TERM",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "PYTHONUNBUFFERED",
        "PYTHONIOENCODING",
    ],
)
SANDBOX_COMPUTE_LOCAL_FALLBACK_TOOLS = env.list(
    "SANDBOX_COMPUTE_LOCAL_FALLBACK_TOOLS",
    default=["create_chart"],
)
SANDBOX_COMPUTE_LOCAL_FALLBACK_MCP = env.bool(
    "SANDBOX_COMPUTE_LOCAL_FALLBACK_MCP",
    default=True,
)
SANDBOX_COMPUTE_SYNC_ON_TOOL_CALL = env.bool(
    "SANDBOX_COMPUTE_SYNC_ON_TOOL_CALL",
    default=True,
)
SANDBOX_COMPUTE_SYNC_ON_MCP_CALL = env.bool(
    "SANDBOX_COMPUTE_SYNC_ON_MCP_CALL",
    default=True,
)
SANDBOX_COMPUTE_SYNC_ON_RUN_COMMAND = env.bool(
    "SANDBOX_COMPUTE_SYNC_ON_RUN_COMMAND",
    default=False,
)
SANDBOX_COMPUTE_REQUIRE_PROXY = env.bool(
    "SANDBOX_COMPUTE_REQUIRE_PROXY",
    default=GOBII_PROPRIETARY_MODE,
)
SANDBOX_COMPUTE_NO_PROXY = env("SANDBOX_COMPUTE_NO_PROXY", default="")

# In local/dev by default, simulate email delivery when no real provider is configured.
# This avoids blocking first‑run UX. If SMTP is configured per agent or
# POSTMARK_SERVER_TOKEN is set, real delivery is used instead.
SIMULATE_EMAIL_DELIVERY = env.bool(
    "SIMULATE_EMAIL_DELIVERY", default=(GOBII_RELEASE_ENV != "prod")
)


# Twilio
TWILIO_ACCOUNT_SID = env("TWILIO_ACCOUNT_SID", default="")
TWILIO_AUTH_TOKEN = env("TWILIO_AUTH_TOKEN", default="")
TWILIO_VERIFY_SERVICE_SID = env("TWILIO_VERIFY_SERVICE_SID", default="")
TWILIO_MESSAGING_SERVICE_SID = env("TWILIO_MESSAGING_SERVICE_SID", default="")
_TWILIO_FEATURE_FLAG = env.bool("TWILIO_ENABLED", default=GOBII_PROPRIETARY_MODE)
TWILIO_VERIFY_CONFIGURED = bool(TWILIO_VERIFY_SERVICE_SID)
TWILIO_CREDENTIALS_PRESENT = bool(
    TWILIO_ACCOUNT_SID
    and TWILIO_AUTH_TOKEN
    and TWILIO_MESSAGING_SERVICE_SID
)
TWILIO_ENABLED = _TWILIO_FEATURE_FLAG and TWILIO_CREDENTIALS_PRESENT
TWILIO_DISABLED_REASON = ""
if not TWILIO_ENABLED:
    if not _TWILIO_FEATURE_FLAG:
        TWILIO_DISABLED_REASON = "Twilio disabled by configuration"
    elif not TWILIO_CREDENTIALS_PRESENT:
        TWILIO_DISABLED_REASON = "Twilio credentials missing"

# Mixpanel
MIXPANEL_PROJECT_TOKEN = env(
    "MIXPANEL_PROJECT_TOKEN",
    default=_proprietary_default("analytics", "MIXPANEL_PROJECT_TOKEN"),
)

TWILIO_INCOMING_WEBHOOK_TOKEN = env("TWILIO_INCOMING_WEBHOOK_TOKEN", default="dummy-twilio-incoming-webhook-token")

# Messaging limits
WEB_CHAT_MESSAGE_MAX_LENGTH = env.int("WEB_CHAT_MESSAGE_MAX_LENGTH", default=100000)

# SMS Config
SMS_MAX_BODY_LENGTH = env.int("SMS_MAX_BODY_LENGTH", default=1450)  # Max length of SMS body


# SMS Parsing
EMAIL_STRIP_REPLIES = env.bool("EMAIL_STRIP_REPLIES", default=False)

# ────────── Pipedream MCP (Remote) ──────────
# These are optional; when set, Gobii will enable the Pipedream MCP server.
PIPEDREAM_CLIENT_ID = env("PIPEDREAM_CLIENT_ID", default="")
PIPEDREAM_CLIENT_SECRET = env("PIPEDREAM_CLIENT_SECRET", default="")
PIPEDREAM_PROJECT_ID = env("PIPEDREAM_PROJECT_ID", default="")

# Map Gobii release env → Pipedream Connect environment.
# Pipedream supports only two environments: "development" and "production".
def _default_pipedream_environment() -> str:
    rel = os.getenv("GOBII_RELEASE_ENV", "local").lower()
    # Treat only prod/production as production; everything else uses development.
    return "production" if rel in ("prod", "production") else "development"

PIPEDREAM_ENVIRONMENT = env("PIPEDREAM_ENVIRONMENT", default=_default_pipedream_environment())

# Comma-separated list of app slugs to prefetch tools for (e.g., "google_sheets,google_docs,greenhouse")
PIPEDREAM_PREFETCH_APPS = env("PIPEDREAM_PREFETCH_APPS", default="google_sheets,google_docs,greenhouse,trello")

# Pipedream Connect GC (batch cleanup)
PIPEDREAM_GC_ENABLED = env.bool(
    "PIPEDREAM_GC_ENABLED",
    default=bool(PIPEDREAM_CLIENT_ID and PIPEDREAM_CLIENT_SECRET and PIPEDREAM_PROJECT_ID),
)
PIPEDREAM_GC_DRY_RUN = env.bool(
    "PIPEDREAM_GC_DRY_RUN",
    default=(PIPEDREAM_ENVIRONMENT != "production"),
)
PIPEDREAM_GC_EXPIRED_RETENTION_DAYS = env.int("PIPEDREAM_GC_EXPIRED_RETENTION_DAYS", default=30)
PIPEDREAM_GC_DEACTIVATED_RETENTION_DAYS = env.int("PIPEDREAM_GC_DEACTIVATED_RETENTION_DAYS", default=60)
PIPEDREAM_GC_BATCH_SIZE = env.int("PIPEDREAM_GC_BATCH_SIZE", default=200)
PIPEDREAM_GC_MAX_DELETES_PER_RUN = env.int("PIPEDREAM_GC_MAX_DELETES_PER_RUN", default=200)

# Add GC beat schedule only when enabled
if PIPEDREAM_GC_ENABLED:
    CELERY_BEAT_SCHEDULE["pipedream-connect-gc-daily"] = {
        "task": "api.tasks.pipedream_connect_gc.gc_orphaned_users",
        "schedule": crontab(hour=4, minute=45),
    }

# File Handling

# Maximum file size (in bytes) for downloads and inbound attachments
# Default: 10 MB. Override with env var MAX_FILE_SIZE if needed.
MAX_FILE_SIZE = env.int("MAX_FILE_SIZE", default=10 * 1024 * 1024)
ALLOW_FILE_DOWNLOAD = env.bool("ALLOW_FILE_DOWNLOAD", default=True)
ALLOW_FILE_UPLOAD = env.bool("ALLOW_FILE_UPLOAD", default=True)
SIGNATURE_IMAGE_ATTACHMENT_PREFIXES = tuple(
    prefix.strip().lower()
    for prefix in env.list("SIGNATURE_IMAGE_ATTACHMENT_PREFIXES", default=["Outlook-"])
    if prefix and prefix.strip()
)
SIGNATURE_IMAGE_ATTACHMENT_EXTENSIONS = tuple(
    ext.strip().lower()
    for ext in env.list(
        "SIGNATURE_IMAGE_ATTACHMENT_EXTENSIONS",
        default=[".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"],
    )
    if ext and ext.strip()
)

# Manual whitelist limits
# Maximum number of manual allowlist entries per agent. Configurable via env.
MANUAL_WHITELIST_MAX_PER_AGENT = env.int("MANUAL_WHITELIST_MAX_PER_AGENT", default=100)
# Default domain used for auto-generated agent email endpoints in Gobii proprietary mode.
# Community/OSS deployments typically leave this unused.
DEFAULT_AGENT_EMAIL_DOMAIN = env(
    "DEFAULT_AGENT_EMAIL_DOMAIN",
    default="my.gobii.ai" if GOBII_PROPRIETARY_MODE else "agents.localhost",
)

# Dedicated IP configuration
DEDICATED_IP_ALLOW_MULTI_ASSIGN = env.bool(
    "DEDICATED_IP_ALLOW_MULTI_ASSIGN",
    default=True,
)

# Whether to auto-create agent-owned email endpoints during agent creation.
# Defaults follow Gobii proprietary mode: enabled when proprietary, disabled in OSS.
# Can be overridden explicitly via env if needed.
ENABLE_DEFAULT_AGENT_EMAIL = env.bool(
    "ENABLE_DEFAULT_AGENT_EMAIL", default=GOBII_PROPRIETARY_MODE
)
# DB-backed LLM config is always enabled; system falls back to legacy
# behavior only when DB has no usable tiers/endpoints.


# FBP Cookie
FBP_COOKIE_NAME = "_fbp"
FBP_MAX_AGE = 90 * 24 * 60 * 60  # 90 days

# Rewardful
REWARDFUL_ID = env.str("REWARDFUL_ID", default="")
REWARDFUL_AFFILIATE_LINK = env.str("REWARDFUL_AFFILIATE_LINK", default="")


SECURITY_TXT_EMAIL = env.str("SECURITY_TXT_EMAIL", default="")
SECURITY_TXT_EXPIRY = env.str("SECURITY_TXT_EXPIRY", default="")
