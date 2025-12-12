"""
Utilities for enforcing minimum cron/interval schedules on persistent agents.

This centralises schedule validation so updates from multiple entry points
(admin tools, agent tool calls, provisioning) can share the same logic.
"""
from dataclasses import dataclass
import logging
import math
from typing import Iterable, Optional

from celery.schedules import crontab, schedule as celery_schedule
from api.agent.core.schedule_parser import ScheduleParser
from constants.plans import PlanNames
from util.subscription_helper import get_owner_plan

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NormalizedSchedule:
    """Result of normalizing a schedule against a minimum interval."""

    normalized: Optional[str]
    changed: bool
    reason: Optional[str] = None


@dataclass(frozen=True)
class AgentScheduleChange:
    agent_id: str
    schedule_before: Optional[str]
    schedule_after: Optional[str]
    snapshot_before: Optional[str]
    snapshot_after: Optional[str]
    reason: Optional[str]


def cron_interval_seconds(schedule_obj: crontab, sample_runs: int = 5) -> float:
    """Return the smallest interval (seconds) between consecutive cron executions."""

    def _next_run_from(start_time):
        probe_time = start_time
        original_nowfun = getattr(schedule_obj, "nowfun", None)
        safety = 0
        try:
            schedule_obj.nowfun = lambda: probe_time
            delta = schedule_obj.remaining_estimate(probe_time)
            while delta.total_seconds() <= 0 and safety < 10:
                probe_time = probe_time + abs(delta)
                schedule_obj.nowfun = lambda: probe_time
                delta = schedule_obj.remaining_estimate(probe_time)
                safety += 1
            return probe_time + delta
        finally:
            schedule_obj.nowfun = original_nowfun

    from django.utils import timezone

    reference_time = timezone.now().replace(second=0, microsecond=0)
    runs = [_next_run_from(reference_time)]
    for _ in range(max(sample_runs - 1, 1)):
        runs.append(_next_run_from(runs[-1]))

    intervals = [
        float((runs[idx + 1] - runs[idx]).total_seconds())
        for idx in range(len(runs) - 1)
    ]
    return min(intervals) if intervals else float("inf")


def cron_satisfies_min_interval(schedule_obj: crontab, min_interval_seconds: float) -> bool:
    """Return True when the cron spacing is greater than or equal to the minimum."""
    try:
        interval_seconds = cron_interval_seconds(schedule_obj)
    except Exception as exc:  # pragma: no cover - defensive fallback
        logger.warning("Falling back to basic cron frequency check: %s", exc, exc_info=True)
        minute_values = sorted(list(schedule_obj.minute))
        if not minute_values:
            return True
        if len(minute_values) == 1:
            interval_seconds = 3600.0
        else:
            gaps = []
            for idx, minute in enumerate(minute_values):
                next_minute = minute_values[(idx + 1) % len(minute_values)]
                gap = (next_minute - minute) % 60
                if gap == 0:
                    gap = 60
                gaps.append(gap * 60)
            interval_seconds = min(gaps)
    return interval_seconds >= min_interval_seconds


def normalize_schedule(schedule_str: Optional[str], min_interval_minutes: Optional[int]) -> NormalizedSchedule:
    """
    Normalize a schedule string to satisfy the minimum interval.

    Returns unchanged schedules when already compliant. For cron expressions,
    the normalizer attempts progressively less aggressive fixes:
    - Reduce minute list to a single minute (preserves time-of-hour)
    - If hours are wild-carded and min is hour-aligned, widen the hour step
    - Reduce to the first hour value when still too frequent
    - Fallback to '@every {min}m'
    """
    if not schedule_str or not min_interval_minutes:
        return NormalizedSchedule(schedule_str, False, None)

    schedule_str = schedule_str.strip()
    min_interval_seconds = max(int(min_interval_minutes), 0) * 60
    if min_interval_seconds <= 0:
        return NormalizedSchedule(schedule_str, False, None)

    try:
        schedule_obj = ScheduleParser.parse(schedule_str)
    except ValueError:
        return NormalizedSchedule(schedule_str, False, "invalid")

    if schedule_obj is None:
        return NormalizedSchedule(None, False, None)

    if isinstance(schedule_obj, celery_schedule):
        interval_seconds = schedule_obj.run_every.total_seconds() if hasattr(schedule_obj.run_every, "total_seconds") else float(schedule_obj.run_every)
        if interval_seconds >= min_interval_seconds:
            return NormalizedSchedule(schedule_str, False, None)
        normalized = f"@every {min_interval_minutes}m"
        return NormalizedSchedule(normalized, True, "interval_clamped")

    if isinstance(schedule_obj, crontab):
        if cron_satisfies_min_interval(schedule_obj, min_interval_seconds):
            return NormalizedSchedule(schedule_str, False, None)

        parts = schedule_str.split()
        if len(parts) == 5:
            minute_values = sorted(list(schedule_obj.minute))
            hour_values = sorted(list(schedule_obj.hour))
            first_minute = minute_values[0] if minute_values else 0
            first_hour = hour_values[0] if hour_values else 0

            # 1) Reduce minutes to a single value, keep other fields intact
            reduced_minute = f"{first_minute} {parts[1]} {parts[2]} {parts[3]} {parts[4]}"
            try:
                reduced_obj = ScheduleParser.parse(reduced_minute)
                if cron_satisfies_min_interval(reduced_obj, min_interval_seconds):
                    return NormalizedSchedule(reduced_minute, True, "minute_reduced")
            except ValueError:
                pass

            # 2) If min is hour-aligned and hours are wild-carded, widen the hour step
            if parts[1] == "*" and min_interval_minutes % 60 == 0:
                hour_step = max(int(math.ceil(min_interval_minutes / 60)), 1)
                stepped_hours = f"{first_minute} */{hour_step} {parts[2]} {parts[3]} {parts[4]}"
                try:
                    stepped_obj = ScheduleParser.parse(stepped_hours)
                    if cron_satisfies_min_interval(stepped_obj, min_interval_seconds):
                        return NormalizedSchedule(stepped_hours, True, "hour_step_adjusted")
                except ValueError:
                    pass

            # 3) Reduce hours to the first value (worst-case daily)
            reduced_hour = f"{first_minute} {first_hour} {parts[2]} {parts[3]} {parts[4]}"
            try:
                reduced_hour_obj = ScheduleParser.parse(reduced_hour)
                if cron_satisfies_min_interval(reduced_hour_obj, min_interval_seconds):
                    return NormalizedSchedule(reduced_hour, True, "hour_reduced")
            except ValueError:
                pass

        normalized = f"@every {min_interval_minutes}m"
        return NormalizedSchedule(normalized, True, "interval_fallback")

    return NormalizedSchedule(schedule_str, False, None)


def enforce_minimum_for_agents(
    agents: Iterable,
    min_interval_minutes: Optional[int],
    *,
    dry_run: bool,
    include_snapshots: bool = True,
) -> dict:
    """
    Apply minimum schedule enforcement to a collection of agents.

    Returns a summary containing counts and sample changes.
    """
    min_minutes = max(int(min_interval_minutes or 0), 0)
    summary = {
        "min_interval_minutes": min_minutes,
        "dry_run": dry_run,
        "scanned": 0,
        "updated": 0,
        "snapshot_updated": 0,
        "unchanged": 0,
        "errors": 0,
        "sample_changes": [],
    }

    if min_minutes <= 0:
        return summary

    from api.models import PersistentAgent  # local import to avoid circulars

    sample_limit = 20

    for agent in agents:
        summary["scanned"] += 1
        original_schedule = getattr(agent, "schedule", None)
        original_snapshot = getattr(agent, "schedule_snapshot", None)
        try:
            normalized_schedule = normalize_schedule(original_schedule, min_minutes)
            normalized_snapshot = (
                normalize_schedule(original_snapshot, min_minutes)
                if include_snapshots
                else NormalizedSchedule(agent.schedule_snapshot, False, None)
            )
        except Exception:  # pragma: no cover - defensive
            summary["errors"] += 1
            logger.exception("Failed to normalize schedule for agent %s", getattr(agent, "id", None))
            continue

        changed = normalized_schedule.changed or normalized_snapshot.changed
        if not changed:
            summary["unchanged"] += 1
            continue

        if dry_run:
            summary["updated"] += int(normalized_schedule.changed)
            summary["snapshot_updated"] += int(normalized_snapshot.changed)
        else:
            update_fields: list[str] = []
            if normalized_schedule.changed:
                agent.schedule = normalized_schedule.normalized
                update_fields.append("schedule")
            if include_snapshots and normalized_snapshot.changed:
                agent.schedule_snapshot = normalized_snapshot.normalized
                update_fields.append("schedule_snapshot")

            if update_fields:
                agent.save(update_fields=update_fields)
                summary["updated"] += int(normalized_schedule.changed)
                summary["snapshot_updated"] += int(normalized_snapshot.changed)

        if len(summary["sample_changes"]) < sample_limit:
            summary["sample_changes"].append(
                AgentScheduleChange(
                    agent_id=str(getattr(agent, "id", "")),
                    schedule_before=original_schedule,
                    schedule_after=normalized_schedule.normalized,
                    snapshot_before=original_snapshot,
                    snapshot_after=normalized_snapshot.normalized,
                    reason=normalized_schedule.reason or normalized_snapshot.reason,
                )
            )

    return summary


def tool_config_min_for_plan(plan_name: str) -> Optional[int]:
    """Return the configured minimum minutes for a plan."""
    from api.models import ToolConfig

    cfg = ToolConfig.objects.filter(plan_name=plan_name).first()
    if not cfg:
        return None
    return cfg.min_cron_schedule_minutes


def agents_for_plan(plan_name: str):
    """Yield agents whose owner plan matches the provided plan name."""
    from api.models import PersistentAgent

    qs = PersistentAgent.objects.non_eval().select_related("user", "organization")
    for agent in qs.iterator(chunk_size=500):
        owner = getattr(agent, "organization", None) or getattr(agent, "user", None)
        try:
            plan = get_owner_plan(owner)
        except Exception:
            plan = None
        plan_id = (plan or {}).get("id") or PlanNames.FREE
        if str(plan_id).lower() == str(plan_name).lower():
            yield agent
