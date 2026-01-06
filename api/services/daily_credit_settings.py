import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional

from django.core.cache import cache

from constants.plans import PlanNamesChoices
from api.services.plan_settings import resolve_owner_plan_identifiers, select_plan_settings_payload

from api.models import DailyCreditConfig

DEFAULT_SLIDER_MIN = Decimal("0")
DEFAULT_SLIDER_MAX = Decimal("50")
DEFAULT_SLIDER_STEP = Decimal("1")
DEFAULT_BURN_RATE_THRESHOLD = Decimal("3")
DEFAULT_BURN_RATE_WINDOW_MINUTES = 60
DEFAULT_HARD_LIMIT_MULTIPLIER = Decimal("2")

_CACHE_KEY = "daily_credit_settings:v2"
_CACHE_TTL_SECONDS = 300

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DailyCreditSettings:
    slider_min: Decimal
    slider_max: Decimal
    slider_step: Decimal
    burn_rate_threshold_per_hour: Decimal
    burn_rate_window_minutes: int
    hard_limit_multiplier: Decimal


def _coalesce_decimal(value, fallback: Decimal) -> Decimal:
    if value is None:
        return fallback
    try:
        return Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return fallback


def _coalesce_int(value, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _serialise(configs) -> dict[str, dict[str, dict]]:
    by_plan_version: dict[str, dict] = {}
    by_plan_name: dict[str, dict] = {}
    for config in configs:
        payload = {
            "slider_min": _coalesce_decimal(config.slider_min, DEFAULT_SLIDER_MIN),
            "slider_max": _coalesce_decimal(config.slider_max, DEFAULT_SLIDER_MAX),
            "slider_step": _coalesce_decimal(config.slider_step, DEFAULT_SLIDER_STEP),
            "burn_rate_threshold_per_hour": _coalesce_decimal(
                config.burn_rate_threshold_per_hour,
                DEFAULT_BURN_RATE_THRESHOLD,
            ),
            "burn_rate_window_minutes": _coalesce_int(
                config.burn_rate_window_minutes,
                DEFAULT_BURN_RATE_WINDOW_MINUTES,
            ),
            "hard_limit_multiplier": _coalesce_decimal(
                config.hard_limit_multiplier,
                DEFAULT_HARD_LIMIT_MULTIPLIER,
            ),
        }
        if getattr(config, "plan_version_id", None):
            by_plan_version[str(config.plan_version_id)] = payload
        if config.plan_name:
            by_plan_name[config.plan_name] = payload
    return {"by_plan_version": by_plan_version, "by_plan_name": by_plan_name}


def _ensure_defaults_exist() -> None:
    for plan_name in PlanNamesChoices.values:
        DailyCreditConfig.objects.get_or_create(
            plan_name=plan_name,
            defaults={
                "slider_min": DEFAULT_SLIDER_MIN,
                "slider_max": DEFAULT_SLIDER_MAX,
                "slider_step": DEFAULT_SLIDER_STEP,
                "burn_rate_threshold_per_hour": DEFAULT_BURN_RATE_THRESHOLD,
                "burn_rate_window_minutes": DEFAULT_BURN_RATE_WINDOW_MINUTES,
                "hard_limit_multiplier": DEFAULT_HARD_LIMIT_MULTIPLIER,
            },
        )
    try:
        from django.apps import apps

        PlanVersion = apps.get_model("api", "PlanVersion")
    except Exception:
        return
    for plan_version in PlanVersion.objects.all():
        DailyCreditConfig.objects.get_or_create(
            plan_version=plan_version,
            defaults={
                "slider_min": DEFAULT_SLIDER_MIN,
                "slider_max": DEFAULT_SLIDER_MAX,
                "slider_step": DEFAULT_SLIDER_STEP,
                "burn_rate_threshold_per_hour": DEFAULT_BURN_RATE_THRESHOLD,
                "burn_rate_window_minutes": DEFAULT_BURN_RATE_WINDOW_MINUTES,
                "hard_limit_multiplier": DEFAULT_HARD_LIMIT_MULTIPLIER,
            },
        )


def _load_settings() -> dict:
    cached = cache.get(_CACHE_KEY)
    if cached:
        return cached

    _ensure_defaults_exist()
    configs = DailyCreditConfig.objects.all()
    payload = _serialise(configs)
    cache.set(_CACHE_KEY, payload, _CACHE_TTL_SECONDS)
    return payload


def get_daily_credit_settings_for_plan_version(
    plan_version_id: Optional[str],
    plan_name: Optional[str] = None,
) -> DailyCreditSettings:
    settings_map = _load_settings()
    config = select_plan_settings_payload(settings_map, plan_version_id, plan_name)
    return DailyCreditSettings(
        slider_min=_coalesce_decimal(config.get("slider_min"), DEFAULT_SLIDER_MIN),
        slider_max=_coalesce_decimal(config.get("slider_max"), DEFAULT_SLIDER_MAX),
        slider_step=_coalesce_decimal(config.get("slider_step"), DEFAULT_SLIDER_STEP),
        burn_rate_threshold_per_hour=_coalesce_decimal(
            config.get("burn_rate_threshold_per_hour"),
            DEFAULT_BURN_RATE_THRESHOLD,
        ),
        burn_rate_window_minutes=_coalesce_int(
            config.get("burn_rate_window_minutes"),
            DEFAULT_BURN_RATE_WINDOW_MINUTES,
        ),
        hard_limit_multiplier=_coalesce_decimal(
            config.get("hard_limit_multiplier"),
            DEFAULT_HARD_LIMIT_MULTIPLIER,
        ),
    )


def get_daily_credit_settings_for_plan(plan_name: Optional[str]) -> DailyCreditSettings:
    return get_daily_credit_settings_for_plan_version(None, plan_name)


def get_daily_credit_settings_for_owner(owner) -> DailyCreditSettings:
    plan_name, plan_version_id = resolve_owner_plan_identifiers(owner, logger=logger)
    return get_daily_credit_settings_for_plan_version(plan_version_id, plan_name)


def get_daily_credit_settings() -> DailyCreditSettings:
    """Backward-compatible wrapper returning free-plan settings when no plan is provided."""
    return get_daily_credit_settings_for_plan(None)


def invalidate_daily_credit_settings_cache() -> None:
    cache.delete(_CACHE_KEY)
