"""
Schedule updater tool for persistent agents.

This module provides functionality for agents to update their own cron schedules.
"""
import logging
from celery.schedules import crontab, schedule as celery_schedule
from django.core.exceptions import ValidationError

from ..core.schedule_parser import ScheduleParser
from api.services.tool_settings import (
    DEFAULT_MIN_CRON_SCHEDULE_MINUTES,
    get_tool_settings_for_owner,
)
from api.services.schedule_enforcement import cron_satisfies_min_interval, cron_interval_seconds

logger = logging.getLogger(__name__)


def _should_continue_work(params: dict) -> bool:
    """Return True if the agent indicates more work right after this schedule update."""
    raw = params.get("will_continue_work")
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        return normalized in {"1", "true", "yes"}
    return bool(raw)


def _too_frequent_message(min_interval_minutes: int) -> str:
    min_minutes = min_interval_minutes or DEFAULT_MIN_CRON_SCHEDULE_MINUTES
    return f"Schedule is too frequent. Minimum interval is {min_minutes} minutes."


def _min_interval_minutes_for_agent(agent) -> int | None:
    """Return the enforced minimum interval in minutes for the agent's owner."""
    owner = getattr(agent, "organization", None) or getattr(agent, "user", None)
    try:
        settings = get_tool_settings_for_owner(owner)
        min_interval_minutes = settings.min_cron_schedule_minutes
        if min_interval_minutes is None:
            return None
        return max(int(min_interval_minutes), 0) or None
    except Exception as exc:  # pragma: no cover - defensive fallback
        logger.warning(
            "Falling back to default cron frequency for agent %s: %s",
            getattr(agent, "id", None),
            exc,
            exc_info=True,
        )
        return DEFAULT_MIN_CRON_SCHEDULE_MINUTES


def _cron_interval_seconds(schedule_obj: crontab, sample_runs: int = 5) -> float:
    """Backward-compatible shim for code that calls this helper directly."""
    return cron_interval_seconds(schedule_obj, sample_runs=sample_runs)


def _cron_satisfies_min_interval(schedule_obj: crontab, min_interval_seconds: float) -> bool:
    return cron_satisfies_min_interval(schedule_obj, min_interval_seconds)


def execute_update_schedule(agent, params: dict) -> dict:
    """Execute schedule update for a persistent agent.
    
    Args:
        agent: PersistentAgent instance
        params: Dictionary containing:
            - new_schedule: String cron expression or special format, or None/empty to disable
    
    Returns:
        Dictionary with status and message
    """
    new_schedule_str = params.get("new_schedule") or None
    # Strip whitespace and treat empty strings as None
    if new_schedule_str is not None:
        new_schedule_str = new_schedule_str.strip() or None
    original_schedule = agent.schedule
    will_continue = _should_continue_work(params)
    
    # Log schedule update attempt
    logger.info(
        "Agent %s updating schedule from '%s' to '%s'",
        agent.id, original_schedule or "None", new_schedule_str or "None"
    )

    try:
        if new_schedule_str:
            schedule_obj = ScheduleParser.parse(new_schedule_str)
            min_interval_minutes = _min_interval_minutes_for_agent(agent)
            min_interval_seconds = (min_interval_minutes or 0) * 60

            # Validate schedule frequency
            if min_interval_minutes:
                if isinstance(schedule_obj, celery_schedule):
                    interval = schedule_obj.run_every.total_seconds() if hasattr(schedule_obj.run_every, 'total_seconds') else float(schedule_obj.run_every)
                    if interval < min_interval_seconds:
                        raise ValueError(_too_frequent_message(min_interval_minutes))

                elif isinstance(schedule_obj, crontab):
                    if not _cron_satisfies_min_interval(schedule_obj, min_interval_seconds):
                        raise ValueError(_too_frequent_message(min_interval_minutes))

        agent.schedule = new_schedule_str
        # Only validate the schedule field using the model's custom clean method
        agent.clean()  # This only validates the schedule field
        agent.save(update_fields=['schedule'])
        if new_schedule_str:
            return {
                "status": "ok",
                "message": f"Schedule updated to '{new_schedule_str}'.",
                "auto_sleep_ok": not will_continue,
            }
        return {
            "status": "ok",
            "message": "Schedule has been disabled.",
            "auto_sleep_ok": not will_continue,
        }

    except (ValidationError, ValueError) as e:
        agent.schedule = original_schedule
        msg = (
            e.message_dict.get("schedule", [str(e)])[0]
            if isinstance(e, ValidationError)
            else str(e)
        )
        logger.warning("Invalid schedule format for agent %s: %s", agent.id, msg)
        return {"status": "error", "message": f"Invalid schedule format: {msg}"}
    except Exception as e:
        agent.schedule = original_schedule
        logger.exception("Failed to update schedule for agent %s", agent.id)
        return {"status": "error", "message": f"Failed to update schedule: {e}"}


def get_update_schedule_tool() -> dict:
    """Return the update_schedule tool definition for LLM function calling."""
    return {
        "type": "function",
        "function": {
            "name": "update_schedule",
            "description": "Updates the agent's cron schedule. RANDOMIZE IF POSSIBLE TO AVOID THUNDERING HERD. REMEMBER, HOWEVER, SOME ASSIGNMENTS REQUIRE VERY PRECISING TIMING.",
            "parameters": {
                "type": "object",
                "properties": {
                    "new_schedule": {
                        "type": "string",
                        "description": "Cron expression or '@daily', '@every 2h'. Use '' or null to disable. RANDOMIZE IF POSSIBLE TO AVOID THUNDERING HERD. REMEMBER, HOWEVER, SOME ASSIGNMENTS REQUIRE VERY PRECISING TIMING.",
                    },
                    "will_continue_work": {
                        "type": "boolean",
                        "description": "REQUIRED. true = work remains (cards in todo/doing). false = all work done AND marked done, report sent.",
                    },
                },
                "required": ["will_continue_work"],
            },
        },
    } 
