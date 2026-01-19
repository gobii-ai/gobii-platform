import logging
from dataclasses import dataclass
from typing import Optional

from django.conf import settings
from django.core.cache import cache
from django.db import OperationalError, ProgrammingError

logger = logging.getLogger(__name__)

DEFAULT_MAX_WORK_TASK_STEPS = getattr(settings, "WORK_TASK_MAX_STEPS", 8)
DEFAULT_MAX_WORK_TASK_TOOL_CALLS = getattr(settings, "WORK_TASK_MAX_TOOL_CALLS", 12)
DEFAULT_MAX_WORK_TASKS_PER_DAY = getattr(settings, "WORK_TASK_MAX_TASKS_PER_DAY", 100)
DEFAULT_MAX_ACTIVE_WORK_TASKS = getattr(settings, "WORK_TASK_MAX_ACTIVE_TASKS", 5)

_CACHE_KEY = "work_task_settings:v1"
_CACHE_TTL_SECONDS = 300


@dataclass(frozen=True)
class WorkTaskSettings:
    max_steps: int
    max_tool_calls: Optional[int]
    max_tasks_per_day: Optional[int]
    max_active_tasks: Optional[int]


def _get_work_task_config_model():
    from api.models import WorkTaskConfig

    return WorkTaskConfig


def _normalize_optional_limit(value: Optional[int]) -> Optional[int]:
    try:
        int_value = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return int_value if int_value > 0 else None


def _normalize_step_limit(value: Optional[int]) -> int:
    try:
        int_value = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_MAX_WORK_TASK_STEPS
    return int_value if int_value > 0 else DEFAULT_MAX_WORK_TASK_STEPS


def _load_settings() -> dict:
    cached = cache.get(_CACHE_KEY)
    if cached:
        return cached

    payload = {
        "max_steps": DEFAULT_MAX_WORK_TASK_STEPS,
        "max_tool_calls": DEFAULT_MAX_WORK_TASK_TOOL_CALLS,
        "max_tasks_per_day": DEFAULT_MAX_WORK_TASKS_PER_DAY,
        "max_active_tasks": DEFAULT_MAX_ACTIVE_WORK_TASKS,
    }

    WorkTaskConfig = _get_work_task_config_model()
    try:
        config, _ = WorkTaskConfig.objects.get_or_create(
            singleton_id=1,
            defaults={
                "max_work_task_steps": DEFAULT_MAX_WORK_TASK_STEPS,
                "max_work_task_tool_calls": DEFAULT_MAX_WORK_TASK_TOOL_CALLS,
                "max_work_tasks_per_day": DEFAULT_MAX_WORK_TASKS_PER_DAY,
                "max_active_work_tasks": DEFAULT_MAX_ACTIVE_WORK_TASKS,
            },
        )
        payload = {
            "max_steps": config.max_work_task_steps,
            "max_tool_calls": config.max_work_task_tool_calls,
            "max_tasks_per_day": config.max_work_tasks_per_day,
            "max_active_tasks": config.max_active_work_tasks,
        }
    except (OperationalError, ProgrammingError):
        # Database tables may not exist yet (e.g. during migrations)
        pass
    except Exception:
        logger.debug("Failed to load WorkTaskConfig; using defaults", exc_info=True)

    cache.set(_CACHE_KEY, payload, _CACHE_TTL_SECONDS)
    return payload


def get_work_task_settings() -> WorkTaskSettings:
    settings_map = _load_settings()
    return WorkTaskSettings(
        max_steps=_normalize_step_limit(settings_map.get("max_steps")),
        max_tool_calls=_normalize_optional_limit(settings_map.get("max_tool_calls")),
        max_tasks_per_day=_normalize_optional_limit(settings_map.get("max_tasks_per_day")),
        max_active_tasks=_normalize_optional_limit(settings_map.get("max_active_tasks")),
    )


def invalidate_work_task_settings_cache() -> None:
    cache.delete(_CACHE_KEY)
