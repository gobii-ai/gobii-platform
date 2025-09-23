"""Helpers for accessing Stripe configuration with database overrides."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import OperationalError, ProgrammingError
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver


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
    org_team_product_id: str
    task_meter_id: str
    task_meter_event_name: str
    org_task_meter_id: str

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

    try:
        live_secret = _coalesce(config.live_secret_key)
    except Exception:
        live_secret = None
    try:
        test_secret = _coalesce(config.test_secret_key)
    except Exception:
        test_secret = None
    try:
        webhook_secret = _coalesce(config.webhook_secret)
    except Exception:
        webhook_secret = None

    return StripeSettings(
        release_env=config.release_env,
        live_mode=bool(config.live_mode),
        live_secret_key=live_secret,
        test_secret_key=test_secret,
        webhook_secret=webhook_secret,
        startup_price_id=config.startup_price_id or "",
        startup_additional_task_price_id=config.startup_additional_task_price_id or "",
        startup_product_id=config.startup_product_id or "",
        org_team_product_id=config.org_team_product_id or "",
        task_meter_id=config.task_meter_id or "",
        task_meter_event_name=config.task_meter_event_name or "",
        org_task_meter_id=config.org_task_meter_id or "",
    )


@lru_cache(maxsize=1)
def _cached_stripe_settings() -> StripeSettings:
    from_db = _load_from_database()
    if from_db is None:
        raise ImproperlyConfigured("StripeConfig not found in database.")
    return from_db


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
