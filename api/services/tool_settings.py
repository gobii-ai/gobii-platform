import logging
from dataclasses import dataclass
from typing import Optional

from django.conf import settings
from django.core.cache import cache

from constants.plans import PlanNames
from util.subscription_helper import get_owner_plan


DEFAULT_MIN_CRON_SCHEDULE_MINUTES = getattr(settings, "PERSISTENT_AGENT_MIN_SCHEDULE_MINUTES", 30)

_CACHE_KEY = "tool_settings:v1"
_CACHE_TTL_SECONDS = 300

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolPlanSettings:
    min_cron_schedule_minutes: Optional[int]


def _get_tool_config_model():
    from api.models import ToolConfig

    return ToolConfig


def _serialise(configs) -> dict:
    return {
        config.plan_name: {
            "min_cron_schedule_minutes": config.min_cron_schedule_minutes,
        }
        for config in configs
    }


def _ensure_defaults_exist() -> None:
    ToolConfig = _get_tool_config_model()
    for plan_name in (PlanNames.FREE, PlanNames.STARTUP, PlanNames.SCALE, PlanNames.ORG_TEAM):
        ToolConfig.objects.get_or_create(
            plan_name=plan_name,
            defaults={"min_cron_schedule_minutes": DEFAULT_MIN_CRON_SCHEDULE_MINUTES},
        )


def _load_settings() -> dict:
    cached = cache.get(_CACHE_KEY)
    if cached:
        return cached

    ToolConfig = _get_tool_config_model()
    _ensure_defaults_exist()
    configs = ToolConfig.objects.all()
    payload = _serialise(configs)
    cache.set(_CACHE_KEY, payload, _CACHE_TTL_SECONDS)
    return payload


def _normalize_min_interval_minutes(value: Optional[int]) -> Optional[int]:
    try:
        int_value = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_MIN_CRON_SCHEDULE_MINUTES
    if int_value <= 0:
        return None
    return int_value


def get_tool_settings_for_plan(plan_name: Optional[str]) -> ToolPlanSettings:
    settings_map = _load_settings()
    normalized_plan = (plan_name or PlanNames.FREE).lower()
    config = settings_map.get(normalized_plan) or settings_map.get(PlanNames.FREE)

    return ToolPlanSettings(
        min_cron_schedule_minutes=_normalize_min_interval_minutes(
            config.get("min_cron_schedule_minutes") if config else None
        )
    )


def get_tool_settings_for_owner(owner) -> ToolPlanSettings:
    plan_name = None
    if owner:
        try:
            plan = get_owner_plan(owner)
            plan_name = plan.get("id")
        except Exception as exc:
            logger.warning("Failed to get owner plan for %s: %s", owner, exc, exc_info=True)
            plan_name = None
    return get_tool_settings_for_plan(plan_name)


def invalidate_tool_settings_cache() -> None:
    cache.delete(_CACHE_KEY)
