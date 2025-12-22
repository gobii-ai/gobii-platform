"""Helpers for accessing Stripe configuration with database overrides."""
import environ
import json

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
    startup_task_pack_product_id: str
    startup_task_pack_price_id: str
    startup_task_pack_price_ids: tuple[str, ...]
    startup_contact_cap_product_id: str
    startup_contact_cap_price_id: str
    startup_contact_cap_price_ids: tuple[str, ...]
    startup_browser_task_limit_product_id: str
    startup_browser_task_limit_price_id: str
    startup_browser_task_limit_price_ids: tuple[str, ...]
    startup_product_id: str
    scale_price_id: str
    scale_additional_task_price_id: str
    scale_task_pack_product_id: str
    scale_task_pack_price_id: str
    scale_task_pack_price_ids: tuple[str, ...]
    scale_contact_cap_product_id: str
    scale_contact_cap_price_id: str
    scale_contact_cap_price_ids: tuple[str, ...]
    scale_browser_task_limit_product_id: str
    scale_browser_task_limit_price_id: str
    scale_browser_task_limit_price_ids: tuple[str, ...]
    scale_product_id: str
    org_team_product_id: str
    org_team_price_id: str
    org_team_additional_task_product_id: str
    org_team_additional_task_price_id: str
    org_team_task_pack_product_id: str
    org_team_task_pack_price_id: str
    org_team_task_pack_price_ids: tuple[str, ...]
    org_team_contact_cap_product_id: str
    org_team_contact_cap_price_id: str
    org_team_contact_cap_price_ids: tuple[str, ...]
    org_team_browser_task_limit_product_id: str
    org_team_browser_task_limit_price_id: str
    org_team_browser_task_limit_price_ids: tuple[str, ...]
    task_pack_delta_startup: int
    task_pack_delta_scale: int
    task_pack_delta_org_team: int
    contact_pack_delta_startup: int
    contact_pack_delta_scale: int
    contact_pack_delta_org_team: int
    browser_task_daily_delta_startup: int
    browser_task_daily_delta_scale: int
    browser_task_daily_delta_org_team: int
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


def _parse_price_id_list(raw_value: str | list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    """Normalize stored/comma-separated/JSON values into a tuple of IDs."""
    if not raw_value:
        return tuple()

    if isinstance(raw_value, (list, tuple, set)):
        values = raw_value
    else:
        values = raw_value
        if isinstance(raw_value, str):
            try:
                parsed = json.loads(raw_value)
                if isinstance(parsed, (list, tuple, set)):
                    values = parsed
            except (TypeError, ValueError, json.JSONDecodeError):
                values = [part.strip() for part in raw_value.split(",")]

    ids: list[str] = []
    for candidate in values or []:
        if not candidate:
            continue
        text = str(candidate).strip()
        if text and text not in ids:
            ids.append(text)
    return tuple(ids)


def _env_defaults() -> StripeSettings:
    return StripeSettings(
        release_env=getattr(settings, "GOBII_RELEASE_ENV", "local"),
        live_mode=getattr(settings, "STRIPE_LIVE_MODE", False),
        live_secret_key=getattr(settings, "STRIPE_LIVE_SECRET_KEY", None),
        test_secret_key=getattr(settings, "STRIPE_TEST_SECRET_KEY", None),
        webhook_secret=getattr(settings, "STRIPE_WEBHOOK_SECRET", None),
        startup_price_id=env("STRIPE_STARTUP_PRICE_ID", default="price_dummy_startup"),
        startup_task_pack_product_id=env("STRIPE_STARTUP_TASK_PACK_PRODUCT_ID", default="prod_dummy_startup_task_pack_product"),
        startup_task_pack_price_id=env("STRIPE_STARTUP_TASK_PACK_PRICE_ID", default="prod_dummy_startup_task_pack_price"),
        startup_task_pack_price_ids=_parse_price_id_list(env.list("STRIPE_STARTUP_TASK_PACK_PRICE_IDS", default=[])),
        startup_additional_task_price_id=env("STRIPE_STARTUP_ADDITIONAL_TASK_PRICE_ID", default="price_dummy_startup_additional_task"),
        startup_contact_cap_product_id=env("STRIPE_STARTUP_CONTACT_CAP_PRODUCT_ID", default="prod_dummy_startup_contact_cap"),
        startup_contact_cap_price_id=env("STRIPE_STARTUP_CONTACT_CAP_PRICE_ID", default="price_dummy_startup_contact_cap"),
        startup_contact_cap_price_ids=_parse_price_id_list(env.list("STRIPE_STARTUP_CONTACT_CAP_PRICE_IDS", default=[])),
        startup_browser_task_limit_product_id=env(
            "STRIPE_STARTUP_BROWSER_TASK_LIMIT_PRODUCT_ID",
            default="prod_dummy_startup_browser_task_limit",
        ),
        startup_browser_task_limit_price_id=env(
            "STRIPE_STARTUP_BROWSER_TASK_LIMIT_PRICE_ID",
            default="price_dummy_startup_browser_task_limit",
        ),
        startup_browser_task_limit_price_ids=_parse_price_id_list(
            env.list("STRIPE_STARTUP_BROWSER_TASK_LIMIT_PRICE_IDS", default=[])
        ),
        startup_product_id=env("STRIPE_STARTUP_PRODUCT_ID", default="prod_dummy_startup"),
        scale_price_id=env("STRIPE_SCALE_PRICE_ID", default="price_dummy_scale"),
        scale_task_pack_product_id=env("STRIPE_SCALE_TASK_PACK_PRODUCT_ID", default="prod_dummy_scale_task_pack_product"),
        scale_task_pack_price_id=env("STRIPE_SCALE_TASK_PACK_PRICE_ID", default="prod_dummy_scale_task_pack_price"),
        scale_task_pack_price_ids=_parse_price_id_list(env.list("STRIPE_SCALE_TASK_PACK_PRICE_IDS", default=[])),
        scale_additional_task_price_id=env("STRIPE_SCALE_ADDITIONAL_TASK_PRICE_ID", default="price_dummy_scale_additional_task"),
        scale_contact_cap_product_id=env("STRIPE_SCALE_CONTACT_CAP_PRODUCT_ID", default="prod_dummy_scale_contact_cap"),
        scale_contact_cap_price_id=env("STRIPE_SCALE_CONTACT_CAP_PRICE_ID", default="price_dummy_scale_contact_cap"),
        scale_contact_cap_price_ids=_parse_price_id_list(env.list("STRIPE_SCALE_CONTACT_CAP_PRICE_IDS", default=[])),
        scale_browser_task_limit_product_id=env(
            "STRIPE_SCALE_BROWSER_TASK_LIMIT_PRODUCT_ID",
            default="prod_dummy_scale_browser_task_limit",
        ),
        scale_browser_task_limit_price_id=env(
            "STRIPE_SCALE_BROWSER_TASK_LIMIT_PRICE_ID",
            default="price_dummy_scale_browser_task_limit",
        ),
        scale_browser_task_limit_price_ids=_parse_price_id_list(
            env.list("STRIPE_SCALE_BROWSER_TASK_LIMIT_PRICE_IDS", default=[])
        ),
        scale_product_id=env("STRIPE_SCALE_PRODUCT_ID", default="prod_dummy_scale"),
        task_pack_delta_startup=env.int("STRIPE_TASK_PACK_DELTA_STARTUP", default=0),
        task_pack_delta_scale=env.int("STRIPE_TASK_PACK_DELTA_SCALE", default=0),
        task_pack_delta_org_team=env.int("STRIPE_TASK_PACK_DELTA_ORG_TEAM", default=0),
        contact_pack_delta_startup=env.int("STRIPE_CONTACT_PACK_DELTA_STARTUP", default=0),
        contact_pack_delta_scale=env.int("STRIPE_CONTACT_PACK_DELTA_SCALE", default=0),
        contact_pack_delta_org_team=env.int("STRIPE_CONTACT_PACK_DELTA_ORG_TEAM", default=0),
        browser_task_daily_delta_startup=env.int("STRIPE_BROWSER_TASK_DAILY_DELTA_STARTUP", default=0),
        browser_task_daily_delta_scale=env.int("STRIPE_BROWSER_TASK_DAILY_DELTA_SCALE", default=0),
        browser_task_daily_delta_org_team=env.int("STRIPE_BROWSER_TASK_DAILY_DELTA_ORG_TEAM", default=0),
        startup_dedicated_ip_product_id=env("STRIPE_STARTUP_DEDICATED_IP_PRODUCT_ID", default="prod_dummy_startup_dedicated_ip"),
        startup_dedicated_ip_price_id=env("STRIPE_STARTUP_DEDICATED_IP_PRICE_ID", default="price_dummy_startup_dedicated_ip"),
        scale_dedicated_ip_product_id=env("STRIPE_SCALE_DEDICATED_IP_PRODUCT_ID", default="prod_dummy_scale_dedicated_ip"),
        scale_dedicated_ip_price_id=env("STRIPE_SCALE_DEDICATED_IP_PRICE_ID", default="price_dummy_scale_dedicated_ip"),
        org_team_product_id=env("STRIPE_ORG_TEAM_PRODUCT_ID", default="prod_dummy_org_team"),
        org_team_price_id=env("STRIPE_ORG_TEAM_PRICE_ID", default="price_dummy_org_team"),
        org_team_additional_task_product_id=env("STRIPE_ORG_TEAM_ADDITIONAL_TASK_PRODUCT_ID", default="prod_dummy_org_team_additional_task"),
        org_team_task_pack_product_id=env("STRIPE_ORG_TEAM_TASK_PACK_PRODUCT_ID", default="prod_dummy_org_team_task_pack_product"),
        org_team_task_pack_price_id=env("STRIPE_ORG_TEAM_TASK_PACK_PRICE_ID", default="prod_dummy_org_team_task_pack_price"),
        org_team_task_pack_price_ids=_parse_price_id_list(env.list("STRIPE_ORG_TEAM_TASK_PACK_PRICE_IDS", default=[])),
        org_team_additional_task_price_id=env("STRIPE_ORG_TEAM_ADDITIONAL_TASK_PRICE_ID", default="price_dummy_org_team_additional_task"),
        org_team_contact_cap_product_id=env("STRIPE_ORG_TEAM_CONTACT_CAP_PRODUCT_ID", default="prod_dummy_org_team_contact_cap"),
        org_team_contact_cap_price_id=env("STRIPE_ORG_TEAM_CONTACT_CAP_PRICE_ID", default="price_dummy_org_team_contact_cap"),
        org_team_contact_cap_price_ids=_parse_price_id_list(env.list("STRIPE_ORG_TEAM_CONTACT_CAP_PRICE_IDS", default=[])),
        org_team_browser_task_limit_product_id=env(
            "STRIPE_ORG_TEAM_BROWSER_TASK_LIMIT_PRODUCT_ID",
            default="prod_dummy_org_team_browser_task_limit",
        ),
        org_team_browser_task_limit_price_id=env(
            "STRIPE_ORG_TEAM_BROWSER_TASK_LIMIT_PRICE_ID",
            default="price_dummy_org_team_browser_task_limit",
        ),
        org_team_browser_task_limit_price_ids=_parse_price_id_list(
            env.list("STRIPE_ORG_TEAM_BROWSER_TASK_LIMIT_PRICE_IDS", default=[])
        ),
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
    try:
        org_team_contact_cap_product_id = config.org_team_contact_cap_product_id or ""
        org_team_contact_cap_price_id = config.org_team_contact_cap_price_id or ""
    except Exception:
        org_team_contact_cap_product_id = ""
        org_team_contact_cap_price_id = ""
    try:
        startup_task_pack_price_ids = _parse_price_id_list(getattr(config, "startup_task_pack_price_ids", None))
    except Exception:
        startup_task_pack_price_ids = tuple()
    try:
        startup_contact_cap_price_ids = _parse_price_id_list(getattr(config, "startup_contact_cap_price_ids", None))
    except Exception:
        startup_contact_cap_price_ids = tuple()
    try:
        startup_browser_task_limit_price_ids = _parse_price_id_list(
            getattr(config, "startup_browser_task_limit_price_ids", None)
        )
    except Exception:
        startup_browser_task_limit_price_ids = tuple()
    try:
        scale_task_pack_price_ids = _parse_price_id_list(getattr(config, "scale_task_pack_price_ids", None))
    except Exception:
        scale_task_pack_price_ids = tuple()
    try:
        scale_contact_cap_price_ids = _parse_price_id_list(getattr(config, "scale_contact_cap_price_ids", None))
    except Exception:
        scale_contact_cap_price_ids = tuple()
    try:
        scale_browser_task_limit_price_ids = _parse_price_id_list(
            getattr(config, "scale_browser_task_limit_price_ids", None)
        )
    except Exception:
        scale_browser_task_limit_price_ids = tuple()
    try:
        org_team_task_pack_price_ids = _parse_price_id_list(getattr(config, "org_team_task_pack_price_ids", None))
    except Exception:
        org_team_task_pack_price_ids = tuple()
    try:
        org_team_contact_cap_price_ids = _parse_price_id_list(getattr(config, "org_team_contact_cap_price_ids", None))
    except Exception:
        org_team_contact_cap_price_ids = tuple()
    try:
        org_team_browser_task_limit_price_ids = _parse_price_id_list(
            getattr(config, "org_team_browser_task_limit_price_ids", None)
        )
    except Exception:
        org_team_browser_task_limit_price_ids = tuple()

    return replace(
        env_defaults,
        release_env=config.release_env,
        live_mode=bool(config.live_mode),
        webhook_secret=webhook_secret,
        startup_price_id=config.startup_price_id or "",
        startup_task_pack_product_id=config.startup_task_pack_product_id or "",
        startup_task_pack_price_id=config.startup_task_pack_price_id or "",
        startup_task_pack_price_ids=startup_task_pack_price_ids or env_defaults.startup_task_pack_price_ids,
        startup_additional_task_price_id=config.startup_additional_task_price_id or "",
        startup_contact_cap_product_id=config.startup_contact_cap_product_id or "",
        startup_contact_cap_price_id=config.startup_contact_cap_price_id or "",
        startup_contact_cap_price_ids=startup_contact_cap_price_ids or env_defaults.startup_contact_cap_price_ids,
        startup_browser_task_limit_product_id=config.startup_browser_task_limit_product_id or "",
        startup_browser_task_limit_price_id=config.startup_browser_task_limit_price_id or "",
        startup_browser_task_limit_price_ids=(
            startup_browser_task_limit_price_ids or env_defaults.startup_browser_task_limit_price_ids
        ),
        startup_product_id=config.startup_product_id or "",
        scale_price_id=config.scale_price_id or "",
        scale_task_pack_product_id=config.scale_task_pack_product_id or "",
        scale_task_pack_price_id=config.scale_task_pack_price_id or "",
        scale_task_pack_price_ids=scale_task_pack_price_ids or env_defaults.scale_task_pack_price_ids,
        scale_additional_task_price_id=config.scale_additional_task_price_id or "",
        scale_contact_cap_product_id=config.scale_contact_cap_product_id or "",
        scale_contact_cap_price_id=config.scale_contact_cap_price_id or "",
        scale_contact_cap_price_ids=scale_contact_cap_price_ids or env_defaults.scale_contact_cap_price_ids,
        scale_browser_task_limit_product_id=config.scale_browser_task_limit_product_id or "",
        scale_browser_task_limit_price_id=config.scale_browser_task_limit_price_id or "",
        scale_browser_task_limit_price_ids=(
            scale_browser_task_limit_price_ids or env_defaults.scale_browser_task_limit_price_ids
        ),
        scale_product_id=config.scale_product_id or "",
        startup_dedicated_ip_product_id=config.startup_dedicated_ip_product_id or "",
        startup_dedicated_ip_price_id=config.startup_dedicated_ip_price_id or "",
        scale_dedicated_ip_product_id=config.scale_dedicated_ip_product_id or "",
        scale_dedicated_ip_price_id=config.scale_dedicated_ip_price_id or "",
        org_team_product_id=config.org_team_product_id or "",
        org_team_price_id=config.org_team_price_id or "",
        org_team_additional_task_product_id=getattr(config, "org_team_additional_task_product_id", "") or "",
        org_team_task_pack_product_id=config.org_team_task_pack_product_id or "",
        org_team_task_pack_price_id=config.org_team_task_pack_price_id or "",
        org_team_task_pack_price_ids=org_team_task_pack_price_ids or env_defaults.org_team_task_pack_price_ids,
        task_pack_delta_startup=getattr(config, "task_pack_delta_startup", env_defaults.task_pack_delta_startup),
        task_pack_delta_scale=getattr(config, "task_pack_delta_scale", env_defaults.task_pack_delta_scale),
        task_pack_delta_org_team=getattr(config, "task_pack_delta_org_team", env_defaults.task_pack_delta_org_team),
        contact_pack_delta_startup=getattr(config, "contact_pack_delta_startup", env_defaults.contact_pack_delta_startup),
        contact_pack_delta_scale=getattr(config, "contact_pack_delta_scale", env_defaults.contact_pack_delta_scale),
        contact_pack_delta_org_team=getattr(config, "contact_pack_delta_org_team", env_defaults.contact_pack_delta_org_team),
        browser_task_daily_delta_startup=getattr(
            config, "browser_task_daily_delta_startup", env_defaults.browser_task_daily_delta_startup
        ),
        browser_task_daily_delta_scale=getattr(
            config, "browser_task_daily_delta_scale", env_defaults.browser_task_daily_delta_scale
        ),
        browser_task_daily_delta_org_team=getattr(
            config, "browser_task_daily_delta_org_team", env_defaults.browser_task_daily_delta_org_team
        ),
        org_team_additional_task_price_id=org_team_additional_price,
        org_team_contact_cap_product_id=org_team_contact_cap_product_id,
        org_team_contact_cap_price_id=org_team_contact_cap_price_id,
        org_team_contact_cap_price_ids=org_team_contact_cap_price_ids or env_defaults.org_team_contact_cap_price_ids,
        org_team_browser_task_limit_product_id=config.org_team_browser_task_limit_product_id or "",
        org_team_browser_task_limit_price_id=config.org_team_browser_task_limit_price_id or "",
        org_team_browser_task_limit_price_ids=(
            org_team_browser_task_limit_price_ids or env_defaults.org_team_browser_task_limit_price_ids
        ),
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
