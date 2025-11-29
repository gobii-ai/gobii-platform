from dataclasses import dataclass
from typing import Optional

from django.conf import settings
from django.core.cache import cache

from constants.plans import PlanNames
from util.subscription_helper import get_owner_plan


DEFAULT_MAX_BROWSER_STEPS = getattr(settings, "BROWSER_AGENT_MAX_STEPS", 100)
DEFAULT_MAX_BROWSER_TASKS = getattr(settings, "BROWSER_AGENT_DAILY_MAX_TASKS", 60)
DEFAULT_MAX_ACTIVE_BROWSER_TASKS = getattr(settings, "BROWSER_AGENT_MAX_ACTIVE_TASKS", 3)

_CACHE_KEY = "browser_settings:v1"
_CACHE_TTL_SECONDS = 300


@dataclass(frozen=True)
class BrowserPlanSettings:
    max_browser_steps: int
    max_browser_tasks: Optional[int]
    max_active_browser_tasks: Optional[int]


def _get_browser_config_model():
    from api.models import BrowserConfig

    return BrowserConfig


def _normalise_optional_limit(value: Optional[int]) -> Optional[int]:
    try:
        int_value = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return int_value if int_value > 0 else None


def _normalise_step_limit(value: Optional[int]) -> int:
    try:
        int_value = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_MAX_BROWSER_STEPS
    return int_value if int_value > 0 else DEFAULT_MAX_BROWSER_STEPS


def _serialise(configs) -> dict:
    return {
        config.plan_name: {
            "max_browser_steps": config.max_browser_steps,
            "max_browser_tasks": config.max_browser_tasks,
            "max_active_browser_tasks": config.max_active_browser_tasks,
        }
        for config in configs
    }


def _ensure_defaults_exist() -> None:
    BrowserConfig = _get_browser_config_model()
    for plan_name in (PlanNames.FREE, PlanNames.STARTUP, PlanNames.SCALE, PlanNames.ORG_TEAM):
        BrowserConfig.objects.get_or_create(
            plan_name=plan_name,
            defaults={
                "max_browser_steps": DEFAULT_MAX_BROWSER_STEPS,
                "max_browser_tasks": DEFAULT_MAX_BROWSER_TASKS,
                "max_active_browser_tasks": DEFAULT_MAX_ACTIVE_BROWSER_TASKS,
            },
        )


def _load_settings() -> dict:
    cached = cache.get(_CACHE_KEY)
    if cached:
        return cached

    BrowserConfig = _get_browser_config_model()
    _ensure_defaults_exist()
    configs = BrowserConfig.objects.all()
    payload = _serialise(configs)
    cache.set(_CACHE_KEY, payload, _CACHE_TTL_SECONDS)
    return payload


def get_browser_settings_for_plan(plan_name: Optional[str]) -> BrowserPlanSettings:
    settings_map = _load_settings()
    normalized_plan = (plan_name or PlanNames.FREE).lower()
    config = settings_map.get(normalized_plan) or settings_map.get(PlanNames.FREE)

    return BrowserPlanSettings(
        max_browser_steps=_normalise_step_limit(config.get("max_browser_steps") if config else None),
        max_browser_tasks=_normalise_optional_limit(config.get("max_browser_tasks") if config else None),
        max_active_browser_tasks=_normalise_optional_limit(
            config.get("max_active_browser_tasks") if config else None
        ),
    )


def get_browser_settings_for_owner(owner) -> BrowserPlanSettings:
    plan_name = None
    if owner:
        try:
            plan = get_owner_plan(owner)
            plan_name = plan.get("id")
        except Exception as e:
            logger.warning("Failed to get owner plan for owner %s: %s", owner, e, exc_info=True)
            plan_name = None
    return get_browser_settings_for_plan(plan_name)


def invalidate_browser_settings_cache() -> None:
    cache.delete(_CACHE_KEY)
