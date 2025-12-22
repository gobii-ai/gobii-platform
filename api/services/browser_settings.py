import logging

from dataclasses import dataclass
from typing import Optional

from django.conf import settings
from django.core.cache import cache

from constants.plans import PlanNames
from util.subscription_helper import get_owner_plan

logger = logging.getLogger(__name__)


DEFAULT_MAX_BROWSER_STEPS = getattr(settings, "BROWSER_AGENT_MAX_STEPS", 100)
DEFAULT_MAX_BROWSER_TASKS = getattr(settings, "BROWSER_AGENT_DAILY_MAX_TASKS", 60)
DEFAULT_MAX_ACTIVE_BROWSER_TASKS = getattr(settings, "BROWSER_AGENT_MAX_ACTIVE_TASKS", 3)
_DEFAULT_VISION_LEVEL = getattr(settings, "BROWSER_AGENT_VISION_DETAIL_LEVEL", "auto")
_VALID_VISION_LEVELS = {"auto", "low", "high"}
DEFAULT_VISION_DETAIL_LEVEL = _DEFAULT_VISION_LEVEL.lower() if str(_DEFAULT_VISION_LEVEL).lower() in _VALID_VISION_LEVELS else "auto"

_CACHE_KEY = "browser_settings:v2"
_CACHE_TTL_SECONDS = 300


@dataclass(frozen=True)
class BrowserPlanSettings:
    max_browser_steps: int
    max_browser_tasks: Optional[int]
    max_active_browser_tasks: Optional[int]
    vision_detail_level: str


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


def _normalise_vision_detail_level(value: Optional[str]) -> str:
    if not value:
        return DEFAULT_VISION_DETAIL_LEVEL
    normalized = str(value).lower()
    if normalized not in _VALID_VISION_LEVELS:
        return DEFAULT_VISION_DETAIL_LEVEL
    return normalized


def _serialise(configs) -> dict:
    return {
        config.plan_name: {
            "max_browser_steps": config.max_browser_steps,
            "max_browser_tasks": config.max_browser_tasks,
            "max_active_browser_tasks": config.max_active_browser_tasks,
            "vision_detail_level": getattr(config, "vision_detail_level", DEFAULT_VISION_DETAIL_LEVEL),
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
                "vision_detail_level": DEFAULT_VISION_DETAIL_LEVEL,
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
        vision_detail_level=_normalise_vision_detail_level(config.get("vision_detail_level") if config else None),
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
    plan_settings = get_browser_settings_for_plan(plan_name)
    max_browser_tasks = _apply_browser_task_daily_uplift(plan_settings.max_browser_tasks, owner)

    return BrowserPlanSettings(
        max_browser_steps=plan_settings.max_browser_steps,
        max_browser_tasks=max_browser_tasks,
        max_active_browser_tasks=plan_settings.max_active_browser_tasks,
        vision_detail_level=plan_settings.vision_detail_level,
    )


def _apply_browser_task_daily_uplift(base_limit: Optional[int], owner) -> Optional[int]:
    if base_limit is None or not owner:
        return base_limit
    try:
        from billing.addons import AddonEntitlementService

        uplift = AddonEntitlementService.get_browser_task_daily_uplift(owner)
    except Exception as exc:
        logger.warning("Failed to load browser task daily uplift for owner %s: %s", owner, exc, exc_info=True)
        return base_limit

    if uplift <= 0:
        return base_limit
    return base_limit + uplift


def invalidate_browser_settings_cache() -> None:
    cache.delete(_CACHE_KEY)
