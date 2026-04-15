import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional

from django.core.cache import cache

from constants.plans import PlanNames, PlanNamesChoices, PlanSlugs
from api.services.plan_settings import resolve_owner_plan_identifiers, select_plan_settings_payload

from api.models import DailyCreditConfig

DEFAULT_SLIDER_MIN = Decimal("0")
DEFAULT_SLIDER_MAX = Decimal("50")
DEFAULT_SLIDER_STEP = Decimal("1")
DEFAULT_BURN_RATE_THRESHOLD = Decimal("3")
DEFAULT_OFFPEAK_BURN_RATE_THRESHOLD = DEFAULT_BURN_RATE_THRESHOLD
DEFAULT_BURN_RATE_WINDOW_MINUTES = 60
DEFAULT_HARD_LIMIT_MULTIPLIER = Decimal("2")
DEFAULT_DEFAULT_DAILY_CREDIT_TARGET = 5
DEFAULT_DAILY_CREDIT_TARGET_BY_PLAN = {
    PlanNames.FREE: 5,
    PlanNames.STARTUP: 10,
    PlanNames.SCALE: 10,
    PlanNames.ORG_TEAM: 10,
    PlanSlugs.FREE: 5,
    PlanSlugs.STARTUP: 10,
    PlanSlugs.SCALE: 10,
    PlanSlugs.ORG_TEAM: 10,
}

_CACHE_KEY = "daily_credit_settings:v2"
_CACHE_TTL_SECONDS = 300

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DailyCreditSettings:
    slider_min: Decimal
    slider_max: Decimal
    slider_step: Decimal
    default_daily_credit_target: int
    burn_rate_threshold_per_hour: Decimal
    offpeak_burn_rate_threshold_per_hour: Decimal
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


def get_default_daily_credit_target_for_plan(plan_name: Optional[str]) -> int:
    normalized_plan = (plan_name or PlanNames.FREE).lower()
    return DEFAULT_DAILY_CREDIT_TARGET_BY_PLAN.get(normalized_plan, DEFAULT_DEFAULT_DAILY_CREDIT_TARGET)


def get_default_daily_credit_target_for_plan_version(plan_version) -> int:
    if plan_version is None:
        return DEFAULT_DEFAULT_DAILY_CREDIT_TARGET

    legacy_plan_code = getattr(plan_version, "legacy_plan_code", None)
    if legacy_plan_code:
        return get_default_daily_credit_target_for_plan(legacy_plan_code)

    plan = getattr(plan_version, "plan", None)
    return get_default_daily_credit_target_for_plan(getattr(plan, "slug", None))


def serialize_daily_credit_configs(configs) -> dict[str, dict[str, dict]]:
    by_plan_version: dict[str, dict] = {}
    by_plan_name: dict[str, dict] = {}
    for config in configs:
        burn_rate_threshold = _coalesce_decimal(
            config.burn_rate_threshold_per_hour,
            DEFAULT_BURN_RATE_THRESHOLD,
        )
        fallback_plan_name = getattr(config, "plan_name", None)
        if not fallback_plan_name:
            fallback_plan_name = getattr(getattr(config, "plan_version", None), "legacy_plan_code", None)
        if not fallback_plan_name:
            fallback_plan_name = getattr(getattr(getattr(config, "plan_version", None), "plan", None), "slug", None)
        default_daily_credit_target = _coalesce_int(
            getattr(config, "default_daily_credit_target", None),
            get_default_daily_credit_target_for_plan(fallback_plan_name),
        )
        payload = {
            "slider_min": _coalesce_decimal(config.slider_min, DEFAULT_SLIDER_MIN),
            "slider_max": _coalesce_decimal(config.slider_max, DEFAULT_SLIDER_MAX),
            "slider_step": _coalesce_decimal(config.slider_step, DEFAULT_SLIDER_STEP),
            "default_daily_credit_target": default_daily_credit_target,
            "burn_rate_threshold_per_hour": burn_rate_threshold,
            "offpeak_burn_rate_threshold_per_hour": _coalesce_decimal(
                config.offpeak_burn_rate_threshold_per_hour,
                burn_rate_threshold,
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


def daily_credit_settings_from_payload(
    payload: dict,
    *,
    plan_name: Optional[str] = None,
) -> DailyCreditSettings:
    burn_rate_threshold = _coalesce_decimal(
        payload.get("burn_rate_threshold_per_hour"),
        DEFAULT_BURN_RATE_THRESHOLD,
    )
    return DailyCreditSettings(
        slider_min=_coalesce_decimal(payload.get("slider_min"), DEFAULT_SLIDER_MIN),
        slider_max=_coalesce_decimal(payload.get("slider_max"), DEFAULT_SLIDER_MAX),
        slider_step=_coalesce_decimal(payload.get("slider_step"), DEFAULT_SLIDER_STEP),
        default_daily_credit_target=_coalesce_int(
            payload.get("default_daily_credit_target"),
            get_default_daily_credit_target_for_plan(plan_name),
        ),
        burn_rate_threshold_per_hour=burn_rate_threshold,
        offpeak_burn_rate_threshold_per_hour=_coalesce_decimal(
            payload.get("offpeak_burn_rate_threshold_per_hour"),
            burn_rate_threshold,
        ),
        burn_rate_window_minutes=_coalesce_int(
            payload.get("burn_rate_window_minutes"),
            DEFAULT_BURN_RATE_WINDOW_MINUTES,
        ),
        hard_limit_multiplier=_coalesce_decimal(
            payload.get("hard_limit_multiplier"),
            DEFAULT_HARD_LIMIT_MULTIPLIER,
        ),
    )


def _ensure_defaults_exist() -> None:
    for plan_name in PlanNamesChoices.values:
        DailyCreditConfig.objects.get_or_create(
            plan_name=plan_name,
            defaults={
                "slider_min": DEFAULT_SLIDER_MIN,
                "slider_max": DEFAULT_SLIDER_MAX,
                "slider_step": DEFAULT_SLIDER_STEP,
                "default_daily_credit_target": get_default_daily_credit_target_for_plan(plan_name),
                "burn_rate_threshold_per_hour": DEFAULT_BURN_RATE_THRESHOLD,
                "offpeak_burn_rate_threshold_per_hour": DEFAULT_OFFPEAK_BURN_RATE_THRESHOLD,
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
                "default_daily_credit_target": get_default_daily_credit_target_for_plan_version(plan_version),
                "burn_rate_threshold_per_hour": DEFAULT_BURN_RATE_THRESHOLD,
                "offpeak_burn_rate_threshold_per_hour": DEFAULT_OFFPEAK_BURN_RATE_THRESHOLD,
                "burn_rate_window_minutes": DEFAULT_BURN_RATE_WINDOW_MINUTES,
                "hard_limit_multiplier": DEFAULT_HARD_LIMIT_MULTIPLIER,
            },
        )


def _load_settings() -> dict:
    cached = cache.get(_CACHE_KEY)
    if cached:
        return cached

    _ensure_defaults_exist()
    configs = DailyCreditConfig.objects.select_related("plan_version__plan").all()
    payload = serialize_daily_credit_configs(configs)
    cache.set(_CACHE_KEY, payload, _CACHE_TTL_SECONDS)
    return payload


def get_daily_credit_settings_for_plan_version(
    plan_version_id: Optional[str],
    plan_name: Optional[str] = None,
) -> DailyCreditSettings:
    settings_map = _load_settings()
    config = select_plan_settings_payload(settings_map, plan_version_id, plan_name)
    return daily_credit_settings_from_payload(config, plan_name=plan_name)


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
