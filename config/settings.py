"""
Gobii settings – dev profile
"""

from pathlib import Path
import environ, os
from decimal import Decimal
from celery.schedules import crontab
from django.core.exceptions import ImproperlyConfigured

LOG_LEVEL = os.getenv("DJANGO_LOG_LEVEL", "INFO")

GA_MEASUREMENT_ID = os.getenv("GA_MEASUREMENT_ID", "")  # e.g. G-2PCKFMF85B

BASE_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = BASE_DIR.parent
env = environ.Env(
    DEBUG=(bool, False),
)
# loads infra/local/.env when running locally
env_file = ROOT_DIR / "infra" / "platform" / "local" / ".env"
if env_file.exists():
    environ.Env.read_env(env_file)

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

# ────────── Core ──────────
DEBUG = env.bool("DEBUG", default=False)
SECRET_KEY = env("DJANGO_SECRET_KEY")
ALLOWED_HOSTS = ["*"]  # tighten in prod
CSRF_TRUSTED_ORIGINS = [
    'https://gobii.ai',
    'https://gobii.ai:443',
    'https://www.gobii.ai',
    'https://www.gobii.ai:443',
    'https://getgobii.com',
    'https://getgobii.com:443',
    'https://www.getgobii.com',
    'https://www.getgobii.com:443',
]
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
USE_X_FORWARDED_HOST = True
SITE_ID = 1

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
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "waffle.middleware.WaffleMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
    "middleware.user_id_baggage.UserIdBaggageMiddleware",
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
                "pages.context_processors.analytics"
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
        "CONN_MAX_AGE": env.int("DJANGO_DB_CONN_MAX_AGE", default=600),  # 10 minutes
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
MAILGUN_API_KEY = env("MAILGUN_API_KEY", default=None)

# Community Edition disables email verification by default to avoid external email providers
ACCOUNT_EMAIL_VERIFICATION = env(
    "ACCOUNT_EMAIL_VERIFICATION",
    default="mandatory" if GOBII_PROPRIETARY_MODE and MAILGUN_API_KEY else "none",
)
ACCOUNT_LOGOUT_ON_GET = True
ACCOUNT_ADAPTER = "config.account_adapter.GobiiAccountAdapter"

# TODO: Test the removal of this; got deprecation warning
#ACCOUNT_EMAIL_REQUIRED = True
ACCOUNT_UNIQUE_EMAIL  = True

ACCOUNT_CONFIRM_EMAIL_ON_GET = True  # auto-confirm as soon as user hits the link
ACCOUNT_DEFAULT_HTTP_PROTOCOL = "https"
ACCOUNT_EMAIL_CONFIRMATION_EXPIRE_DAYS = 10


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
}

# Conditionally enable Twilio sync task only when explicitly enabled
TWILIO_ENABLED = env.bool("TWILIO_ENABLED", default=False)
if TWILIO_ENABLED and env("TWILIO_MESSAGING_SERVICE_SID", default=""):
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
AGENT_SOFT_EXPIRATION_INACTIVITY_DAYS = env.int("AGENT_SOFT_EXPIRATION_INACTIVITY_DAYS", default=60)
# Hours of grace after a user downgrades to Free before expiration checks apply
AGENT_SOFT_EXPIRATION_DOWNGRADE_GRACE_HOURS = env.int("AGENT_SOFT_EXPIRATION_DOWNGRADE_GRACE_HOURS", default=48)

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

SOCIALACCOUNT_LOGIN_ON_GET = True

# Proprietary mode uses Mailgun in production, but devs often run locally without
# credentials. Fall back to the console backend when no API key is configured so
# login/signup flows do not hard-error while still exercising the email code.
EMAIL_BACKEND = (
    "anymail.backends.mailgun.EmailBackend"
    if GOBII_PROPRIETARY_MODE and MAILGUN_API_KEY
    else "django.core.mail.backends.console.EmailBackend"
)

ANYMAIL = {
    "MAILGUN_API_KEY": MAILGUN_API_KEY,
    "MAILGUN_SENDER_DOMAIN": os.getenv(
        "MAILGUN_SENDER_DOMAIN", "mg.getgobii.com"
    ),  # Changed from MAILGUN_DOMAIN to MAILGUN_SENDER_DOMAIN as per Anymail's common setting.
    # If you chose the EU region add:
    # "MAILGUN_API_URL": "https://api.eu.mailgun.net/v3",
    "POSTMARK_SERVER_TOKEN": env("POSTMARK_SERVER_TOKEN", default=None),
}

DEFAULT_FROM_EMAIL = "Gobii <noreply@mg.getgobii.com>"
SERVER_EMAIL = DEFAULT_FROM_EMAIL
ACCOUNT_EMAIL_SUBJECT_PREFIX = "[Gobii] "
ACCOUNT_DEFAULT_HTTP_PROTOCOL = "https"

# dj-stripe / Stripe configuration
STRIPE_LIVE_SECRET_KEY = os.environ.get("STRIPE_LIVE_SECRET_KEY")
STRIPE_TEST_SECRET_KEY = os.environ.get("STRIPE_TEST_SECRET_KEY")
STRIPE_LIVE_MODE = env.bool("STRIPE_LIVE_MODE", default=False)  # Set to True in production

DJSTRIPE_WEBHOOK_SECRET = env("STRIPE_WEBHOOK_SECRET", default="whsec_dummy")
DJSTRIPE_FOREIGN_KEY_TO_FIELD = "id"
DJSTRIPE_USE_NATIVE_JSONFIELD = True

# Credits configuration
# These environment defaults seed the DB-backed configuration.
# Keep as Decimal to support fractional credits (e.g., 0.1).
CREDITS_PER_TASK = Decimal(env("CREDITS_PER_TASK", default="0.4"))

# Optional per-tool credit overrides (case-insensitive keys).
# Example: {"search_web": Decimal("0.10"), "http_request": Decimal("0.05")}.
# Values are migrated into the database on deployment and serve only as fallback.
TOOL_CREDIT_COSTS = {
    "update_charter": Decimal("0.04"),
    "update_schedule": Decimal("0.04"),
    "sqlite_batch": Decimal("0.8"),
}

# Analytics
SEGMENT_WRITE_KEY = env("SEGMENT_WRITE_KEY", default="")
SEGMENT_WEB_WRITE_KEY = env("SEGMENT_WEB_WRITE_KEY", default=SEGMENT_WRITE_KEY)

# Ad/Pixel IDs (empty disables)
REDDIT_PIXEL_ID = env("REDDIT_PIXEL_ID", default="")
META_PIXEL_ID = env("META_PIXEL_ID", default="")


# Task Credit Settings
INITIAL_TASK_CREDIT_EXPIRATION_DAYS=env("INITIAL_TASK_CREDIT_EXPIRATION_DAYS", default=30, cast=int)

# Support
SUPPORT_EMAIL = env("SUPPORT_EMAIL", default="support@gobii.ai")

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

GOBII_RELEASE_ENV = env("GOBII_RELEASE_ENV", default="local")

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

# Mixpanel
MIXPANEL_PROJECT_TOKEN = env("MIXPANEL_PROJECT_TOKEN", default="")

TWILIO_INCOMING_WEBHOOK_TOKEN = env("TWILIO_INCOMING_WEBHOOK_TOKEN", default="dummy-twilio-incoming-webhook-token")

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

# Comma-separated list of app slugs to prefetch tools for (e.g., "google_sheets,greenhouse,trello")
PIPEDREAM_PREFETCH_APPS = env("PIPEDREAM_PREFETCH_APPS", default="google_sheets,greenhouse,trello")

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

# Manual whitelist limits
# Maximum number of manual allowlist entries per agent. Configurable via env.
MANUAL_WHITELIST_MAX_PER_AGENT = env.int("MANUAL_WHITELIST_MAX_PER_AGENT", default=100)
# Default domain used for auto-generated agent email endpoints in Gobii proprietary mode.
# Community/OSS deployments typically leave this unused.
DEFAULT_AGENT_EMAIL_DOMAIN = env("DEFAULT_AGENT_EMAIL_DOMAIN", default="my.gobii.ai")

# Whether to auto-create agent-owned email endpoints during agent creation.
# Defaults follow Gobii proprietary mode: enabled when proprietary, disabled in OSS.
# Can be overridden explicitly via env if needed.
ENABLE_DEFAULT_AGENT_EMAIL = env.bool(
    "ENABLE_DEFAULT_AGENT_EMAIL", default=GOBII_PROPRIETARY_MODE
)
# DB-backed LLM config is always enabled; system falls back to legacy
# behavior only when DB has no usable tiers/endpoints.
