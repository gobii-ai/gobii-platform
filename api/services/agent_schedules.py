import hashlib
import logging
import math
import os
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone as datetime_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from celery import current_app as celery_app
from celery.schedules import crontab, schedule as celery_schedule
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from kombu.exceptions import OperationalError as KombuOperationalError
from redbeat import RedBeatSchedulerEntry
from redbeat.schedules import rrule
from redis.exceptions import RedisError

from api.agent.core.schedule_parser import ScheduleParser
from api.models import PersistentAgent, PersistentAgentSchedule
from api.services.schedule_enforcement import cron_satisfies_min_interval
from api.services.tool_settings import get_tool_settings_for_owner


logger = logging.getLogger(__name__)

SCHEDULE_TASK_NAME = "api.agent.tasks.process_agent_schedule_trigger"
ENTRY_NAME_PREFIX = "persistent-agent-extra-schedule"
_SEMANTIC_FIELDS = (
    "name",
    "instruction",
    "kind",
    "expression",
    "timezone",
    "run_at",
    "enabled",
)


@dataclass(frozen=True)
class ClaimedScheduleOccurrence:
    schedule_id: str
    schedule_key: str
    name: str
    instruction: str
    kind: str
    expression: str | None
    timezone: str
    scheduled_for: datetime
    occurrence_key: str
    revision: int


def schedule_entry_name(agent_id, schedule_id) -> str:
    return f"{ENTRY_NAME_PREFIX}:{agent_id}:{schedule_id}"


def schedule_occurrence_key(schedule_id, revision: int, scheduled_for: datetime) -> str:
    normalized = _normalize_run_at(scheduled_for)
    if normalized is None:
        raise ValidationError({"scheduled_for": "A scheduled occurrence needs a datetime."})
    return hashlib.sha256(
        f"{schedule_id}:{revision}:{normalized.isoformat()}".encode("utf-8")
    ).hexdigest()


def _row_value(row, field, default=None):
    if isinstance(row, dict):
        return row.get(field, default)
    return getattr(row, field, default)


def _normalize_enabled(value) -> bool:
    if isinstance(value, bool):
        return value
    if value in (0, 1, "0", "1"):
        return bool(int(value))
    raise ValidationError({"enabled": "Use 0 or 1 for enabled."})


def _normalize_run_at(value) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        value = parse_datetime(value.strip())
        if value is None:
            raise ValidationError({"run_at": "Use an ISO-8601 datetime with a timezone offset."})
    if not isinstance(value, datetime):
        raise ValidationError({"run_at": "Use an ISO-8601 datetime with a timezone offset."})
    if timezone.is_naive(value):
        raise ValidationError({"run_at": "run_at must include a timezone offset."})
    # RedBeat's rrule backend schedules at second precision. Normalize here so
    # the persisted contract and the scheduler never disagree by microseconds.
    return value.astimezone(datetime_timezone.utc).replace(microsecond=0)


def _normalize_row(agent: PersistentAgent, row: dict) -> PersistentAgentSchedule:
    schedule_key = str(row.get("schedule_key") or "").strip().lower()
    name = str(row.get("name") or "").strip()
    kind = str(row.get("kind") or "").strip().lower()
    expression = row.get("expression")
    expression = str(expression).strip() if expression not in (None, "") else None
    timezone_name = str(row.get("timezone") or "UTC").strip()
    run_at = _normalize_run_at(row.get("run_at"))

    candidate = PersistentAgentSchedule(
        agent=agent,
        schedule_key=schedule_key,
        name=name,
        instruction=str(row.get("instruction") or "").strip(),
        kind=kind,
        expression=expression,
        timezone=timezone_name,
        run_at=run_at,
        enabled=_normalize_enabled(row.get("enabled", True)),
    )
    candidate.full_clean(validate_unique=False, validate_constraints=False)
    return candidate


def _normalized_cron_expression(expression: str) -> str:
    return ScheduleParser.SHORTHANDS.get(expression, expression)


def _cron_next_run(expression: str, timezone_name: str, after: datetime) -> datetime:
    parsed = ScheduleParser.parse(expression)
    if not isinstance(parsed, crontab):
        raise ValidationError({"expression": "Expected a cron expression."})

    zone = ZoneInfo(timezone_name)
    after_utc = after.astimezone(datetime_timezone.utc)
    local_after = after_utc.astimezone(zone)
    first_day = local_after.date()

    # Eight years includes the next valid leap-day occurrence while keeping a
    # firm bound for impossible combinations such as February 30.
    for day_offset in range(366 * 8):
        local_day = first_day + timedelta(days=day_offset)
        day_of_week = local_day.isoweekday() % 7
        if (
            local_day.month not in parsed.month_of_year
            or local_day.day not in parsed.day_of_month
            or day_of_week not in parsed.day_of_week
        ):
            continue

        for hour in sorted(parsed.hour):
            for minute in sorted(parsed.minute):
                local_candidate = datetime.combine(
                    local_day,
                    time(hour=hour, minute=minute),
                    tzinfo=zone,
                )
                candidate_utc = local_candidate.astimezone(datetime_timezone.utc)
                round_trip = candidate_utc.astimezone(zone)
                if (
                    round_trip.date() != local_day
                    or round_trip.hour != hour
                    or round_trip.minute != minute
                ):
                    # The wall-clock time does not exist during a DST jump.
                    continue
                if candidate_utc > after_utc:
                    return candidate_utc

    raise ValidationError(
        {"expression": "This cron expression has no valid occurrence in the next eight years."}
    )


def compute_next_run(schedule, *, after: datetime | None = None) -> datetime | None:
    """Return the next exact UTC occurrence strictly after ``after``."""

    after = after or timezone.now()
    if timezone.is_naive(after):
        raise ValidationError({"after": "after must be timezone-aware."})

    kind = _row_value(schedule, "kind")
    if kind == PersistentAgentSchedule.Kind.ONCE:
        run_at = _normalize_run_at(_row_value(schedule, "run_at"))
        return run_at if run_at and run_at > after else None

    expression = _row_value(schedule, "expression")
    timezone_name = _row_value(schedule, "timezone", "UTC") or "UTC"
    try:
        ZoneInfo(timezone_name)
    except (TypeError, ValueError, ZoneInfoNotFoundError):
        raise ValidationError({"timezone": "Use a valid IANA timezone."})

    parsed = ScheduleParser.parse(expression)
    if isinstance(parsed, celery_schedule):
        return after.astimezone(datetime_timezone.utc) + parsed.run_every
    return _cron_next_run(_normalized_cron_expression(expression), timezone_name, after)


def _peak_runs_per_day(expression: str) -> int:
    parsed = ScheduleParser.parse(expression)
    if isinstance(parsed, celery_schedule):
        return int(math.ceil(86400 / parsed.run_every.total_seconds()))
    if isinstance(parsed, crontab):
        return len(parsed.minute) * len(parsed.hour)
    return 0


def _validate_minimum_recurrence(agent: PersistentAgent, candidate: PersistentAgentSchedule) -> None:
    owner = agent.organization or agent.user
    min_minutes = get_tool_settings_for_owner(owner).min_cron_schedule_minutes
    if not min_minutes:
        return

    parsed = ScheduleParser.parse(candidate.expression)
    minimum_seconds = min_minutes * 60
    if isinstance(parsed, celery_schedule):
        valid = parsed.run_every.total_seconds() >= minimum_seconds
    else:
        valid = cron_satisfies_min_interval(parsed, minimum_seconds)
    if not valid:
        raise ValidationError(
            {
                "expression": (
                    f"Schedule '{candidate.schedule_key}' must run no more often than "
                    f"every {min_minutes} minutes."
                )
            }
        )


def validate_schedule_set(
    agent: PersistentAgent,
    candidates: list[PersistentAgentSchedule],
    *,
    now: datetime | None = None,
    existing_by_key: dict[str, PersistentAgentSchedule] | None = None,
) -> None:
    now = now or timezone.now()
    existing_by_key = existing_by_key or {}
    keys = [candidate.schedule_key for candidate in candidates]
    if len(keys) != len(set(keys)):
        raise ValidationError({"schedule_key": "Each schedule_key must be unique."})

    legacy_active = int(bool((agent.schedule or "").strip()))
    if len(candidates) + legacy_active > settings.PERSISTENT_AGENT_SCHEDULE_MAX_TOTAL:
        raise ValidationError(
            f"An agent can keep at most {settings.PERSISTENT_AGENT_SCHEDULE_MAX_TOTAL} schedules."
        )

    active_count = sum(candidate.enabled for candidate in candidates) + legacy_active
    if active_count > settings.PERSISTENT_AGENT_SCHEDULE_MAX_ACTIVE:
        raise ValidationError(
            f"An agent can have at most {settings.PERSISTENT_AGENT_SCHEDULE_MAX_ACTIVE} active schedules."
        )

    recurring_runs_per_day = 0
    if legacy_active:
        recurring_runs_per_day += _peak_runs_per_day(agent.schedule)

    min_run_at = now + timedelta(seconds=settings.PERSISTENT_AGENT_SCHEDULE_MIN_ONCE_LEAD_SECONDS)
    for candidate in candidates:
        if not candidate.enabled:
            continue
        if candidate.kind == PersistentAgentSchedule.Kind.RECURRING:
            _validate_minimum_recurrence(agent, candidate)
            recurring_runs_per_day += _peak_runs_per_day(candidate.expression)
            continue

        current = existing_by_key.get(candidate.schedule_key)
        unchanged_existing_timer = (
            current is not None
            and current.enabled
            and candidate.enabled
            and current.run_at == candidate.run_at
        )
        if candidate.run_at < min_run_at and not unchanged_existing_timer:
            raise ValidationError(
                {
                    "run_at": (
                        f"Schedule '{candidate.schedule_key}' must be at least "
                        f"{settings.PERSISTENT_AGENT_SCHEDULE_MIN_ONCE_LEAD_SECONDS} seconds in the future."
                    )
                }
            )

    if recurring_runs_per_day > settings.PERSISTENT_AGENT_SCHEDULE_MAX_RECURRING_RUNS_PER_DAY:
        raise ValidationError(
            (
                "Combined recurring schedules can run at most "
                f"{settings.PERSISTENT_AGENT_SCHEDULE_MAX_RECURRING_RUNS_PER_DAY} times on an active day; "
                f"this set could run {recurring_runs_per_day} times."
            )
        )


def remove_schedule_entry(agent_id, schedule_id) -> None:
    entry_name = schedule_entry_name(agent_id, schedule_id)
    try:
        with celery_app.connection():
            RedBeatSchedulerEntry.from_key(f"redbeat:{entry_name}", app=celery_app).delete()
    except KeyError:
        return
    except (KombuOperationalError, RedisError):
        logger.error("Unable to remove schedule entry %s", entry_name, exc_info=True)


def remove_agent_schedule_entries(agent_id, schedule_ids) -> None:
    for schedule_id in schedule_ids:
        remove_schedule_entry(agent_id, schedule_id)


def sync_schedule_entry(schedule_or_id) -> None:
    if isinstance(schedule_or_id, PersistentAgentSchedule):
        schedule = schedule_or_id
    else:
        try:
            schedule = PersistentAgentSchedule.objects.select_related("agent").get(pk=schedule_or_id)
        except PersistentAgentSchedule.DoesNotExist:
            return

    agent = schedule.agent
    current_env = os.getenv("GOBII_RELEASE_ENV", "local")
    if agent.execution_environment != current_env:
        return
    if (
        not agent.is_active
        or agent.is_deleted
        or agent.life_state != PersistentAgent.LifeState.ACTIVE
        or not schedule.enabled
        or schedule.next_run_at is None
    ):
        remove_schedule_entry(agent.id, schedule.id)
        return

    entry = RedBeatSchedulerEntry(
        name=schedule_entry_name(agent.id, schedule.id),
        task=SCHEDULE_TASK_NAME,
        schedule=rrule(
            "SECONDLY",
            dtstart=schedule.next_run_at,
            count=1,
            app=celery_app,
        ),
        args=[
            str(agent.id),
            str(schedule.id),
            schedule.revision,
            schedule.next_run_at.isoformat(),
        ],
        app=celery_app,
    )
    try:
        entry.save()
        # RedBeat's save() intentionally preserves existing metadata with
        # HSETNX. A stable per-schedule key therefore needs an explicit
        # reschedule so the new one-shot's last_run_at and zset score agree.
        entry.reschedule(last_run_at=timezone.now())
    except (KombuOperationalError, RedisError):
        logger.error("Unable to sync schedule entry %s", entry.name, exc_info=True)


def sync_agent_schedules(agent_id) -> None:
    try:
        agent = PersistentAgent.objects.get(pk=agent_id)
    except PersistentAgent.DoesNotExist:
        return
    schedules = list(agent.additional_schedules.all())
    if not agent.is_active:
        remove_agent_schedule_entries(agent.id, [schedule.id for schedule in schedules])
        return
    for schedule in schedules:
        sync_schedule_entry(schedule)


def claim_schedule_occurrence(
    agent_id,
    schedule_id,
    expected_revision: int,
    scheduled_for,
    *,
    claimed_at: datetime | None = None,
) -> ClaimedScheduleOccurrence | None:
    """Claim one due occurrence and atomically advance or close its schedule.

    Callers may wrap this helper and event creation in an outer ``atomic`` block;
    its RedBeat side effect will then wait for that outer transaction to commit.
    """

    claimed_at = claimed_at or timezone.now()
    scheduled_for = _normalize_run_at(scheduled_for)
    if scheduled_for is None:
        return None

    with transaction.atomic():
        try:
            schedule = (
                PersistentAgentSchedule.objects.select_for_update()
                .select_related("agent")
                .get(pk=schedule_id, agent_id=agent_id)
            )
        except PersistentAgentSchedule.DoesNotExist:
            return None

        agent = schedule.agent
        if (
            not schedule.enabled
            or not agent.is_active
            or agent.is_deleted
            or agent.life_state != PersistentAgent.LifeState.ACTIVE
            or agent.execution_environment != os.getenv("GOBII_RELEASE_ENV", "local")
            or schedule.revision != int(expected_revision)
            or schedule.next_run_at is None
            or _normalize_run_at(schedule.next_run_at) != scheduled_for
        ):
            return None

        occurrence_key = schedule_occurrence_key(
            schedule.id,
            schedule.revision,
            scheduled_for,
        )
        claimed = ClaimedScheduleOccurrence(
            schedule_id=str(schedule.id),
            schedule_key=schedule.schedule_key,
            name=schedule.name,
            instruction=schedule.instruction,
            kind=schedule.kind,
            expression=schedule.expression,
            timezone=schedule.timezone,
            scheduled_for=scheduled_for,
            occurrence_key=occurrence_key,
            revision=schedule.revision,
        )

        schedule.last_fired_at = claimed_at
        schedule.revision += 1
        if schedule.kind == PersistentAgentSchedule.Kind.ONCE:
            schedule.enabled = False
            schedule.next_run_at = None
            schedule.save(
                update_fields=["last_fired_at", "revision", "enabled", "next_run_at", "updated_at"]
            )
            transaction.on_commit(
                lambda: remove_schedule_entry(schedule.agent_id, schedule.id)
            )
        else:
            schedule.next_run_at = compute_next_run(
                schedule,
                after=max(scheduled_for, claimed_at),
            )
            schedule.save(
                update_fields=["last_fired_at", "revision", "next_run_at", "updated_at"]
            )
            transaction.on_commit(lambda: sync_schedule_entry(schedule.id))

    return claimed


def create_default_onboarding_schedule(
    agent: PersistentAgent,
    *,
    now: datetime | None = None,
) -> PersistentAgentSchedule | None:
    """Create the bounded first check-in timer used by the provisioning path."""

    delay_seconds = settings.PERSISTENT_AGENT_DEFAULT_CHECKIN_DELAY_SECONDS
    if not agent.is_active or delay_seconds <= 0:
        return None
    now = now or timezone.now()
    run_at = (now + timedelta(seconds=delay_seconds)).replace(microsecond=0)
    with transaction.atomic():
        schedule, created = PersistentAgentSchedule.objects.get_or_create(
            agent=agent,
            schedule_key="onboarding_checkin",
            defaults={
                "name": "First check-in",
                "instruction": (
                    "Check in with your owner about how things are going and whether "
                    "this timing or cadence should be adjusted."
                ),
                "kind": PersistentAgentSchedule.Kind.ONCE,
                "expression": None,
                "timezone": "UTC",
                "run_at": run_at,
                "next_run_at": run_at,
                "enabled": True,
            },
        )
        if not created:
            return schedule
        schedule.full_clean()
        transaction.on_commit(lambda: sync_schedule_entry(schedule.id))
    return schedule


def _semantic_values(schedule: PersistentAgentSchedule) -> tuple:
    return tuple(getattr(schedule, field) for field in _SEMANTIC_FIELDS)


def reconcile_agent_schedules(
    agent: PersistentAgent,
    rows: list[dict],
    *,
    now: datetime | None = None,
) -> dict[str, int]:
    """Atomically replace an agent's desired additional schedule set."""

    now = now or timezone.now()
    if timezone.is_naive(now):
        raise ValidationError({"now": "now must be timezone-aware."})
    if len(rows) > settings.PERSISTENT_AGENT_SCHEDULE_MAX_TOTAL:
        raise ValidationError(
            f"An agent can keep at most {settings.PERSISTENT_AGENT_SCHEDULE_MAX_TOTAL} schedules."
        )

    with transaction.atomic():
        locked_agent = PersistentAgent.objects.select_for_update().get(pk=agent.pk)
        existing_by_key = {
            schedule.schedule_key: schedule
            for schedule in PersistentAgentSchedule.objects.select_for_update().filter(agent=locked_agent)
        }
        candidates = [_normalize_row(locked_agent, row) for row in rows]
        validate_schedule_set(
            locked_agent,
            candidates,
            now=now,
            existing_by_key=existing_by_key,
        )

        created = 0
        updated = 0
        unchanged = 0
        touched_ids = []
        disabled_ids = []
        desired_keys = set()

        for candidate in candidates:
            desired_keys.add(candidate.schedule_key)
            current = existing_by_key.get(candidate.schedule_key)
            if current is None:
                candidate.next_run_at = compute_next_run(candidate, after=now) if candidate.enabled else None
                candidate.full_clean()
                candidate.save()
                created += 1
                (touched_ids if candidate.enabled else disabled_ids).append(candidate.id)
                continue

            previous_values = _semantic_values(current)
            for field in _SEMANTIC_FIELDS:
                setattr(current, field, getattr(candidate, field))
            semantic_changed = previous_values != _semantic_values(current)
            if semantic_changed:
                was_run_at = previous_values[_SEMANTIC_FIELDS.index("run_at")]
                was_enabled = previous_values[_SEMANTIC_FIELDS.index("enabled")]
                current.revision += 1
                if current.kind == PersistentAgentSchedule.Kind.ONCE and (
                    current.run_at != was_run_at or (current.enabled and not was_enabled)
                ):
                    current.last_fired_at = None
                current.next_run_at = compute_next_run(current, after=now) if current.enabled else None
                current.full_clean()
                current.save()
                updated += 1
                (touched_ids if current.enabled else disabled_ids).append(current.id)
            elif current.enabled and current.next_run_at is None and current.last_fired_at is None:
                current.next_run_at = compute_next_run(current, after=now)
                current.save(update_fields=["next_run_at", "updated_at"])
                updated += 1
                touched_ids.append(current.id)
            else:
                unchanged += 1

        deleted = [
            schedule
            for key, schedule in existing_by_key.items()
            if key not in desired_keys
        ]
        deleted_ids = [schedule.id for schedule in deleted]
        if deleted_ids:
            PersistentAgentSchedule.objects.filter(id__in=deleted_ids).delete()

        agent_id = locked_agent.id
        if deleted_ids or disabled_ids:
            transaction.on_commit(
                lambda: remove_agent_schedule_entries(agent_id, [*deleted_ids, *disabled_ids])
            )
        for schedule_id in touched_ids:
            transaction.on_commit(lambda schedule_id=schedule_id: sync_schedule_entry(schedule_id))

    active = sum(candidate.enabled for candidate in candidates)
    return {
        "created": created,
        "updated": updated,
        "deleted": len(deleted_ids),
        "unchanged": unchanged,
        "total": len(candidates),
        "active": active,
    }
