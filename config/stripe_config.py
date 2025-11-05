"""Helpers for accessing Stripe configuration with database overrides."""
import environ

from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Optional

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import OperationalError, ProgrammingError
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

env = environ.Env(
    DEBUG=(bool, False),
)


@dataclass(frozen=True)
class StripeSettings:
    release_env: str
    live_mode: bool
    live_secret_key: Optional[str]
    test_secret_key: Optional[str]
    webhook_secret: Optional[str]
    startup_price_id: str
    startup_additional_task_price_id: str
    startup_product_id: str
    scale_price_id: str
    scale_additional_task_price_id: str
    scale_product_id: str
    org_team_product_id: str
    org_team_price_id: str
    org_team_additional_task_price_id: str
    startup_dedicated_ip_product_id: str
    startup_dedicated_ip_price_id: str
    scale_dedicated_ip_product_id: str
    scale_dedicated_ip_price_id: str
    org_team_dedicated_ip_product_id: str
    org_team_dedicated_ip_price_id: str
    task_meter_id: str
    task_meter_event_name: str
    org_team_task_meter_id: str
    org_team_task_meter_event_name: str
    org_task_meter_id: str


def _env_defaults() -> StripeSettings:
    return StripeSettings(
        release_env=getattr(settings, "GOBII_RELEASE_ENV", "local"),
        live_mode=getattr(settings, "STRIPE_LIVE_MODE", False),
        live_secret_key=getattr(settings, "STRIPE_LIVE_SECRET_KEY", None),
        test_secret_key=getattr(settings, "STRIPE_TEST_SECRET_KEY", None),
        webhook_secret=getattr(settings, "STRIPE_WEBHOOK_SECRET", None),
        startup_price_id=env("STRIPE_STARTUP_PRICE_ID", default="price_dummy_startup"),
        startup_additional_task_price_id=env("STRIPE_STARTUP_ADDITIONAL_TASK_PRICE_ID", default="price_dummy_startup_additional_task"),
        startup_product_id=env("STRIPE_STARTUP_PRODUCT_ID", default="prod_dummy_startup"),
        scale_price_id=env("STRIPE_SCALE_PRICE_ID", default="price_dummy_scale"),
        scale_additional_task_price_id=env("STRIPE_SCALE_ADDITIONAL_TASK_PRICE_ID", default="price_dummy_scale_additional_task"),
        scale_product_id=env("STRIPE_SCALE_PRODUCT_ID", default="prod_dummy_scale"),
        startup_dedicated_ip_product_id=env("STRIPE_STARTUP_DEDICATED_IP_PRODUCT_ID", default="prod_dummy_startup_dedicated_ip"),
        startup_dedicated_ip_price_id=env("STRIPE_STARTUP_DEDICATED_IP_PRICE_ID", default="price_dummy_startup_dedicated_ip"),
        scale_dedicated_ip_product_id=env("STRIPE_SCALE_DEDICATED_IP_PRODUCT_ID", default="prod_dummy_scale_dedicated_ip"),
        scale_dedicated_ip_price_id=env("STRIPE_SCALE_DEDICATED_IP_PRICE_ID", default="price_dummy_scale_dedicated_ip"),
        org_team_product_id=env("STRIPE_ORG_TEAM_PRODUCT_ID", default="prod_dummy_org_team"),
        org_team_price_id=env("STRIPE_ORG_TEAM_PRICE_ID", default="price_dummy_org_team"),
        org_team_additional_task_price_id=env("STRIPE_ORG_TEAM_ADDITIONAL_TASK_PRICE_ID", default="price_dummy_org_team_additional_task"),
        org_team_dedicated_ip_product_id=env("STRIPE_ORG_TEAM_DEDICATED_IP_PRODUCT_ID", default="prod_dummy_org_dedicated_ip"),
        org_team_dedicated_ip_price_id=env("STRIPE_ORG_TEAM_DEDICATED_IP_PRICE_ID", default="price_dummy_org_dedicated_ip"),
        task_meter_id=env("STRIPE_TASK_METER_ID", default="meter_dummy_task"),
        task_meter_event_name=env("STRIPE_TASK_METER_EVENT_NAME", default="task"),
        org_team_task_meter_id=env("STRIPE_ORG_TASK_METER_ID", default="meter_dummy_org_task"),
        org_team_task_meter_event_name=env("STRIPE_ORG_TASK_METER_EVENT_NAME", default="task_org_team_task_meter_name"),
        org_task_meter_id=env("STRIPE_ORG_TASK_METER_ID", default="meter_dummy_org_task"),
    )


def _coalesce(value: str | None) -> Optional[str]:
    if not value:
        return None
    return value


def _load_from_database() -> Optional[StripeSettings]:
    try:
        StripeConfig = apps.get_model("api", "StripeConfig")
    except (LookupError, ImproperlyConfigured):
        return None

    release_env = getattr(settings, "GOBII_RELEASE_ENV", "local")

    try:
        config = StripeConfig.objects.prefetch_related("entries").get(release_env=release_env)
    except StripeConfig.DoesNotExist:
        return None
    except (OperationalError, ProgrammingError):
        # Database not ready (e.g., during migrations or collectstatic)
        return None

    env_defaults = _env_defaults()
    try:
        # Webhook secret can still be managed from the admin UI
        webhook_secret = _coalesce(config.webhook_secret)
    except Exception:
        webhook_secret = None
    try:
        org_team_additional_price = config.org_team_additional_task_price_id or ""
    except Exception:
        org_team_additional_price = ""

    return replace(
        env_defaults,
        release_env=config.release_env,
        live_mode=bool(config.live_mode),
        webhook_secret=webhook_secret,
        startup_price_id=config.startup_price_id or "",
        startup_additional_task_price_id=config.startup_additional_task_price_id or "",
        startup_product_id=config.startup_product_id or "",
        scale_price_id=config.scale_price_id or "",
        scale_additional_task_price_id=config.scale_additional_task_price_id or "",
        scale_product_id=config.scale_product_id or "",
        startup_dedicated_ip_product_id=config.startup_dedicated_ip_product_id or "",
        startup_dedicated_ip_price_id=config.startup_dedicated_ip_price_id or "",
        scale_dedicated_ip_product_id=config.scale_dedicated_ip_product_id or "",
        scale_dedicated_ip_price_id=config.scale_dedicated_ip_price_id or "",
        org_team_product_id=config.org_team_product_id or "",
        org_team_price_id=config.org_team_price_id or "",
        org_team_additional_task_price_id=org_team_additional_price,
        org_team_dedicated_ip_product_id=config.org_team_dedicated_ip_product_id or "",
        org_team_dedicated_ip_price_id=config.org_team_dedicated_ip_price_id or "",
        task_meter_id=config.task_meter_id or "",
        task_meter_event_name=config.task_meter_event_name or "",
        org_team_task_meter_id=config.org_team_task_meter_id or "",
        org_team_task_meter_event_name=config.org_team_task_meter_event_name or "",
        org_task_meter_id=config.org_task_meter_id or "",
    )


@lru_cache(maxsize=1)
def _cached_stripe_settings() -> StripeSettings:
    from_db = _load_from_database()
    if from_db is not None:
        return from_db
    return _env_defaults()


def get_stripe_settings(force_reload: bool = False) -> StripeSettings:
    """Return Stripe settings, preferring the stored StripeConfig."""
    if force_reload:
        _cached_stripe_settings.cache_clear()
    return _cached_stripe_settings()


def invalidate_stripe_settings_cache(*_args, **_kwargs) -> None:
    _cached_stripe_settings.cache_clear()


@receiver(post_save, sender="api.StripeConfig")
@receiver(post_delete, sender="api.StripeConfig")
def _stripe_config_changed(**_kwargs) -> None:
    invalidate_stripe_settings_cache()
