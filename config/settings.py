"""
Gobii settings – dev profile
"""

from pathlib import Path
import environ, os
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
    "django.contrib.staticfiles",

    # 3rd-party
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
            },
        },
    },
]


ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

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
# Community Edition disables email verification by default to avoid external email providers
ACCOUNT_EMAIL_VERIFICATION = "mandatory" if GOBII_PROPRIETARY_MODE else "none"
ACCOUNT_LOGOUT_ON_GET = True

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
TURNSTILE_SECRET = env("TURNSTILE_SECRET", default="1x00000000000000000000AA")

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
    "twilio-sync-numbers": {
        "task": "api.tasks.sms_tasks.sync_twilio_numbers",
        "schedule": crontab(minute="*/60"),   # every 30 min
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

# RedBeat scheduler configuration
CELERY_BEAT_SCHEDULER = "redbeat.RedBeatScheduler"
CELERY_TIMEZONE       = "UTC"
CELERY_ENABLE_UTC     = True

# ────────── Misc ──────────
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

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

EMAIL_BACKEND = (
    "anymail.backends.mailgun.EmailBackend"
    if GOBII_PROPRIETARY_MODE
    else "django.core.mail.backends.console.EmailBackend"
)

ANYMAIL = {
    "MAILGUN_API_KEY": os.getenv("MAILGUN_API_KEY"),
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
STRIPE_LIVE_SECRET_KEY = os.environ.get("STRIPE_LIVE_SECRET_KEY", "<your secret key>")
STRIPE_TEST_SECRET_KEY = os.environ.get("STRIPE_TEST_SECRET_KEY", "<your secret key>")
STRIPE_LIVE_MODE = env.bool("STRIPE_LIVE_MODE", default=False)  # Set to True in production

DJSTRIPE_WEBHOOK_SECRET = env("STRIPE_WEBHOOK_SECRET", default="whsec_dummy")
DJSTRIPE_FOREIGN_KEY_TO_FIELD = "id"
DJSTRIPE_USE_NATIVE_JSONFIELD = True

# Stripe Product and Price IDs - used for billing plans
STRIPE_STARTUP_PRICE_ID = env("STRIPE_STARTUP_PRICE_ID", default="price_dummy_startup")
STRIPE_STARTUP_ADDITIONAL_TASK_PRICE_ID = env("STRIPE_STARTUP_ADDITIONAL_TASK_PRICE_ID", default="price_dummy_startup_additional_task")
STRIPE_STARTUP_PRODUCT_ID = env("STRIPE_STARTUP_PRODUCT_ID", default="prod_dummy_startup")
STRIPE_TASK_METER_ID = env("STRIPE_TASK_METER_ID", default="meter_dummy_task")
STRIPE_TASK_METER_EVENT_NAME = env("STRIPE_TASK_METER_EVENT_NAME", default="task")

# Analytics
SEGMENT_WRITE_KEY = env("SEGMENT_WRITE_KEY", default="")


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
# parameter on that one
POSTMARK_INCOMING_WEBHOOK_TOKEN = env("POSTMARK_INCOMING_WEBHOOK_TOKEN", default="dummy-postmark-incoming-token")

EXA_SEARCH_API_KEY = env("EXA_SEARCH_API_KEY", default="dummy-exa-search-api-key")

GOBII_RELEASE_ENV = env("GOBII_RELEASE_ENV", default="local")


# Twilio
TWILIO_ACCOUNT_SID = env("TWILIO_ACCOUNT_SID", default="")
TWILIO_AUTH_TOKEN = env("TWILIO_AUTH_TOKEN", default="")
TWILIO_VERIFY_SERVICE_SID = env("TWILIO_VERIFY_SERVICE_SID", default="")
TWILIO_MESSAGING_SERVICE_SID = env("TWILIO_MESSAGING_SERVICE_SID", default="")

# Mixpanel
MIXPANEL_PROJECT_TOKEN = env("MIXPANEL_PROJECT_TOKEN", default="dummy_mixpanel_project_token")

TWILIO_INCOMING_WEBHOOK_TOKEN = env("TWILIO_INCOMING_WEBHOOK_TOKEN", default="dummy-twilio-incoming-webhook-token")

# SMS Config
SMS_MAX_BODY_LENGTH = env.int("SMS_MAX_BODY_LENGTH", default=1450)  # Max length of SMS body


# SMS Parsing
EMAIL_STRIP_REPLIES = env.bool("EMAIL_STRIP_REPLIES", default=False)

# File Handling

# Maximum file size (in bytes) for downloads and inbound attachments
# Default: 10 MB. Override with env var MAX_FILE_SIZE if needed.
MAX_FILE_SIZE = env.int("MAX_FILE_SIZE", default=10 * 1024 * 1024)
ALLOW_FILE_DOWNLOAD = env.bool("ALLOW_FILE_DOWNLOAD", default=False)
ALLOW_FILE_UPLOAD = env.bool("ALLOW_FILE_UPLOAD", default=False)

# Manual whitelist limits
# Maximum number of manual allowlist entries per agent. Configurable via env.
MANUAL_WHITELIST_MAX_PER_AGENT = env.int("MANUAL_WHITELIST_MAX_PER_AGENT", default=100)
