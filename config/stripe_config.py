"""Helpers for accessing Stripe configuration with database overrides."""

import environ

from cryptography.exceptions import InvalidTag
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Optional

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import OperationalError, ProgrammingError
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from config.stripe_fields import (
    STRIPE_CONFIG_FIELDS,
    StripeConfigFieldSpec,
    StripeValueKind,
    first_string,
    parse_nonnegative_integer,
    parse_string_list,
)

env = environ.Env(DEBUG=(bool, False))


@dataclass(frozen=True)
class StripeSettings:
    release_env: str
    live_mode: bool
    live_secret_key: Optional[str]
    test_secret_key: Optional[str]
    webhook_secret: Optional[str]
    startup_price_id: str
    startup_trial_days: int
    startup_additional_task_price_id: str
    startup_task_pack_product_id: str
    startup_task_pack_price_ids: tuple[str, ...]
    startup_contact_cap_product_id: str
    startup_contact_cap_price_ids: tuple[str, ...]
    startup_browser_task_limit_product_id: str
    startup_browser_task_limit_price_ids: tuple[str, ...]
    startup_advanced_captcha_resolution_product_id: str
    startup_advanced_captcha_resolution_price_id: str
    startup_product_id: str
    scale_price_id: str
    scale_trial_days: int
    scale_additional_task_price_id: str
    scale_task_pack_product_id: str
    scale_task_pack_price_ids: tuple[str, ...]
    scale_contact_cap_product_id: str
    scale_contact_cap_price_ids: tuple[str, ...]
    scale_browser_task_limit_product_id: str
    scale_browser_task_limit_price_ids: tuple[str, ...]
    scale_advanced_captcha_resolution_product_id: str
    scale_advanced_captcha_resolution_price_id: str
    scale_product_id: str
    org_team_product_id: str
    org_team_price_id: str
    org_team_additional_task_product_id: str
    org_team_additional_task_price_id: str
    org_team_task_pack_product_id: str
    org_team_task_pack_price_ids: tuple[str, ...]
    org_team_contact_cap_product_id: str
    org_team_contact_cap_price_ids: tuple[str, ...]
    org_team_browser_task_limit_product_id: str
    org_team_browser_task_limit_price_ids: tuple[str, ...]
    org_team_advanced_captcha_resolution_product_id: str
    org_team_advanced_captcha_resolution_price_id: str
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


def _environment_value(spec: StripeConfigFieldSpec):
    raw_default = "" if isinstance(spec.env_default, tuple) else str(spec.env_default)
    raw_value = env.str(spec.env_name, default=raw_default)
    if spec.value_kind == StripeValueKind.NONNEGATIVE_INTEGER:
        return parse_nonnegative_integer(raw_value)
    if spec.value_kind == StripeValueKind.STRING_LIST:
        return parse_string_list(raw_value)
    if spec.value_kind == StripeValueKind.SINGULAR_WITH_LEGACY_LIST:
        return first_string(raw_value) or first_string(env.str(spec.legacy_env_name, default=""))
    return raw_value


def _env_defaults() -> StripeSettings:
    configured_values = {spec.name: _environment_value(spec) for spec in STRIPE_CONFIG_FIELDS}
    return StripeSettings(
        release_env=settings.GOBII_RELEASE_ENV,
        live_mode=settings.STRIPE_LIVE_MODE,
        live_secret_key=settings.STRIPE_LIVE_SECRET_KEY,
        test_secret_key=settings.STRIPE_TEST_SECRET_KEY,
        # This optional name predates DJSTRIPE_WEBHOOK_SECRET and remains a compatibility fallback.
        webhook_secret=getattr(settings, "STRIPE_WEBHOOK_SECRET", None),
        **configured_values,
    )


def _database_value(config, defaults: StripeSettings, spec: StripeConfigFieldSpec):
    value = getattr(config, spec.name)
    if spec.value_kind == StripeValueKind.NONNEGATIVE_INTEGER:
        return parse_nonnegative_integer(value)
    if spec.value_kind == StripeValueKind.STRING_LIST:
        return parse_string_list(value) or getattr(defaults, spec.name)
    if spec.value_kind == StripeValueKind.SINGULAR_WITH_LEGACY_LIST:
        return (
            first_string(value)
            or first_string(getattr(config, spec.legacy_entry_name))
            or getattr(defaults, spec.name)
        )
    return value or ""


def _load_from_database() -> Optional[StripeSettings]:
    try:
        StripeConfig = apps.get_model("api", "StripeConfig")
    except (LookupError, ImproperlyConfigured):
        return None

    try:
        config = StripeConfig.objects.prefetch_related("entries").get(
            release_env=settings.GOBII_RELEASE_ENV
        )
    except StripeConfig.DoesNotExist:
        return None
    except (OperationalError, ProgrammingError):
        # Database tables may not exist yet during migrations or collectstatic.
        return None

    defaults = _env_defaults()
    configured_values = {
        spec.name: _database_value(config, defaults, spec) for spec in STRIPE_CONFIG_FIELDS
    }
    try:
        webhook_secret = config.webhook_secret or None
    except (InvalidTag, TypeError, UnicodeDecodeError, ValueError):
        webhook_secret = None
    return replace(
        defaults,
        release_env=config.release_env,
        live_mode=bool(config.live_mode),
        webhook_secret=webhook_secret,
        **configured_values,
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
