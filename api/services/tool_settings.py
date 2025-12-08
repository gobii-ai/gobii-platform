import logging
from dataclasses import dataclass, field
from typing import Dict, Optional

from django.conf import settings
from django.core.cache import cache
from django.db import DatabaseError

from constants.plans import PlanNames, PlanNamesChoices
from util.subscription_helper import get_owner_plan


DEFAULT_MIN_CRON_SCHEDULE_MINUTES = getattr(settings, "PERSISTENT_AGENT_MIN_SCHEDULE_MINUTES", 30)

_CACHE_KEY = "tool_settings:v2"
_CACHE_TTL_SECONDS = 300

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolPlanSettings:
    min_cron_schedule_minutes: Optional[int]
    rate_limits: Dict[str, Optional[int]] = field(default_factory=dict)

    def hourly_limit_for_tool(self, tool_name: str) -> Optional[int]:
        """Return the hourly limit for the given tool or None if unlimited."""
        key = (tool_name or "").strip().lower()
        return self.rate_limits.get(key)


def _get_tool_config_model():
    from api.models import ToolConfig

    return ToolConfig


def _serialise(configs) -> dict:
    payload = {}
    for config in configs:
        try:
            rate_limits = {
                rate.tool_name: rate.max_calls_per_hour
                for rate in list(getattr(config, "rate_limits").all())
            }
        except (AttributeError, DatabaseError):
            logger.error("Failed to serialize rate limits for plan %s", config.plan_name, exc_info=True)
            rate_limits = {}
        payload[config.plan_name] = {
            "min_cron_schedule_minutes": config.min_cron_schedule_minutes,
            "rate_limits": rate_limits,
        }
    return payload


def _ensure_defaults_exist() -> None:
    ToolConfig = _get_tool_config_model()
    for plan_name in PlanNamesChoices.values:
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
    configs = ToolConfig.objects.prefetch_related("rate_limits").all()
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


def _normalize_rate_limit(value: Optional[int]) -> Optional[int]:
    try:
        int_value = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if int_value <= 0:
        return None
    return int_value


def _normalize_rate_limits(rate_limits: Optional[dict]) -> Dict[str, Optional[int]]:
    normalized: Dict[str, Optional[int]] = {}
    if not rate_limits:
        return normalized
    for tool_name, raw in rate_limits.items():
        key = (tool_name or "").strip().lower()
        if not key:
            continue
        normalized[key] = _normalize_rate_limit(raw)
    return normalized


def get_tool_settings_for_plan(plan_name: Optional[str]) -> ToolPlanSettings:
    settings_map = _load_settings()
    normalized_plan = (plan_name or PlanNames.FREE).lower()
    config = settings_map.get(normalized_plan) or settings_map.get(PlanNames.FREE)

    return ToolPlanSettings(
        min_cron_schedule_minutes=_normalize_min_interval_minutes(
            config.get("min_cron_schedule_minutes") if config else None
        ),
        rate_limits=_normalize_rate_limits(config.get("rate_limits") if config else {}),
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
