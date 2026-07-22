"""SQLite-backed agent configuration helpers."""

import logging
import math
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Optional

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from ..emotions import (
    MAX_EMOTION_LENGTH,
    MAX_EMOTION_TIMEOUT_SECONDS,
    normalize_emotion_update,
)
from .charter_text import count_literal_newlines
from .charter_updater import execute_update_charter
from .schedule_updater import execute_update_schedule
from .sqlite_config_statements import sqlite_statement_assigns_agent_config_field
from .sqlite_guardrails import clear_guarded_connection, open_guarded_sqlite_connection
from .sqlite_state import AGENT_CONFIG_TABLE, AGENT_SCHEDULES_TABLE, get_sqlite_db_path

logger = logging.getLogger(__name__)

PRIMARY_SCHEDULE_KEY = "primary"
PRIMARY_SCHEDULE_NAME = "Primary cadence"
SCHEDULE_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

AGENT_SCHEDULES_MUTATION_RE = re.compile(
    r'''\b(?:'''
    r'''(?:insert|replace)\s+(?:or\s+\w+\s+)?into|'''
    r'''update(?:\s+or\s+\w+)?|'''
    r'''delete\s+from|'''
    r'''(?:create|drop|alter)\s+table(?:\s+if\s+(?:not\s+)?exists)?'''
    r''')\s+["`\[]?__agent_schedules["`\]]?\b''',
    re.IGNORECASE,
)
SQL_SINGLE_QUOTED_VALUE_RE = re.compile(r"'(?:''|[^'])*'", re.DOTALL)
SQL_COMMENT_RE = re.compile(r"--[^\n]*(?:\n|$)|/\*.*?\*/", re.DOTALL)


@contextmanager
def _guarded_connection(db_path: str):
    conn = open_guarded_sqlite_connection(db_path)
    try:
        yield conn
    finally:
        clear_guarded_connection(conn)
        conn.close()


@dataclass(frozen=True)
class AgentScheduleSnapshot:
    schedule_key: str
    name: str
    kind: str
    schedule: Optional[str]
    timezone: str
    run_at: Optional[str]
    instruction: str
    enabled: bool
    next_run_at: Optional[str] = None
    last_fired_at: Optional[str] = None


@dataclass(frozen=True)
class AgentConfigSnapshot:
    charter: str
    schedule: Optional[str]
    emotion: Optional[str] = None
    emotion_timeout_seconds: Optional[int] = None
    emotion_write_count: int = 0
    schedules: tuple[AgentScheduleSnapshot, ...] = ()


@dataclass(frozen=True)
class AgentConfigApplyResult:
    updated_fields: tuple[str, ...]
    errors: dict[str, str]
    schedules: tuple[AgentScheduleSnapshot, ...] = ()


class _ScheduleApplyError(Exception):
    pass


def sqlite_statement_mutates_agent_schedules(statement: str) -> bool:
    """Return whether SQL targets a mutation at the schedule control table."""
    structural = SQL_SINGLE_QUOTED_VALUE_RE.sub("''", statement or "")
    structural = SQL_COMMENT_RE.sub(" ", structural)
    return bool(AGENT_SCHEDULES_MUTATION_RE.search(structural))
def seed_sqlite_agent_config(agent) -> Optional[AgentConfigSnapshot]:
    """Reset and seed the writable configuration tables for one LLM turn."""
    db_path = get_sqlite_db_path()
    if not db_path:
        logger.warning("SQLite DB path unavailable; cannot seed agent config.")
        return None

    # Never leave a previous turn's writable control rows visible if the
    # persistent snapshot cannot be loaded.
    _drop_agent_config_tables()
    try:
        schedules = _persistent_schedule_snapshots(agent)
        with _guarded_connection(db_path) as conn:
            conn.execute(
                f"""
                CREATE TABLE "{AGENT_CONFIG_TABLE}" (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    charter TEXT,
                    schedule TEXT,
                    emotion TEXT CHECK (
                        emotion IS NULL OR length(emotion) BETWEEN 1 AND {MAX_EMOTION_LENGTH}
                    ),
                    emotion_timeout_seconds INTEGER CHECK (
                        emotion_timeout_seconds IS NULL OR (
                            typeof(emotion_timeout_seconds) = 'integer' AND
                            emotion_timeout_seconds BETWEEN 1 AND {MAX_EMOTION_TIMEOUT_SECONDS}
                        )
                    ),
                    _emotion_write_count INTEGER NOT NULL DEFAULT 0 CHECK (_emotion_write_count >= 0),
                    CHECK (
                        (emotion IS NULL AND emotion_timeout_seconds IS NULL) OR
                        (emotion IS NOT NULL AND emotion_timeout_seconds IS NOT NULL)
                    )
                );
                """
            )
            conn.execute(
                f"""
                CREATE TABLE "{AGENT_SCHEDULES_TABLE}" (
                    schedule_key TEXT PRIMARY KEY
                        CHECK (
                            length(schedule_key) BETWEEN 1 AND 64 AND
                            substr(schedule_key, 1, 1) GLOB '[a-z0-9]' AND
                            schedule_key NOT GLOB '*[^a-z0-9_-]*'
                        ),
                    name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
                    kind TEXT NOT NULL CHECK (kind IN ('recurring', 'once')),
                    schedule TEXT,
                    timezone TEXT NOT NULL DEFAULT 'UTC'
                        CHECK (length(timezone) BETWEEN 1 AND 64),
                    run_at TEXT,
                    instruction TEXT NOT NULL DEFAULT '' CHECK (length(instruction) <= 500),
                    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
                    next_run_at TEXT,
                    last_fired_at TEXT,
                    CHECK (
                        schedule_key != '{PRIMARY_SCHEDULE_KEY}' OR (
                            name = '{PRIMARY_SCHEDULE_NAME}' AND
                            kind = 'recurring' AND
                            timezone = 'UTC' AND
                            run_at IS NULL AND
                            instruction = ''
                        )
                    )
                );
                """
            )
            seeded_at = timezone.now()
            active_emotion, emotion_expires_at = agent.get_active_emotion_state(seeded_at)
            emotion_timeout_seconds = (
                min(
                    MAX_EMOTION_TIMEOUT_SECONDS,
                    max(1, math.ceil((emotion_expires_at - seeded_at).total_seconds())),
                )
                if emotion_expires_at is not None
                else None
            )
            snapshot = AgentConfigSnapshot(
                charter=agent.charter or "",
                schedule=agent.schedule,
                emotion=active_emotion,
                emotion_timeout_seconds=emotion_timeout_seconds,
                schedules=schedules,
            )
            conn.execute(
                f'''INSERT INTO "{AGENT_CONFIG_TABLE}"
                    (id, charter, schedule, emotion, emotion_timeout_seconds)
                    VALUES (1, ?, ?, ?, ?);''',
                (
                    snapshot.charter,
                    snapshot.schedule,
                    snapshot.emotion,
                    snapshot.emotion_timeout_seconds,
                ),
            )
            conn.execute(
                f'''CREATE TRIGGER "{AGENT_CONFIG_TABLE}_emotion_write"
                    AFTER UPDATE OF emotion, emotion_timeout_seconds ON "{AGENT_CONFIG_TABLE}"
                    BEGIN
                        UPDATE "{AGENT_CONFIG_TABLE}"
                        SET _emotion_write_count = _emotion_write_count + 1
                        WHERE id = NEW.id;
                    END;'''
            )
            conn.execute(
                f'''CREATE TRIGGER "{AGENT_CONFIG_TABLE}_update_only" BEFORE INSERT ON "{AGENT_CONFIG_TABLE}"
                    BEGIN SELECT RAISE(ABORT, '__agent_config is update-only; use UPDATE'); END;'''
            )
            conn.execute(
                f'''CREATE TRIGGER "{AGENT_CONFIG_TABLE}_no_delete" BEFORE DELETE ON "{AGENT_CONFIG_TABLE}"
                    BEGIN SELECT RAISE(ABORT, '__agent_config is update-only; use UPDATE'); END;'''
            )
            conn.executemany(
                f"""
                INSERT INTO "{AGENT_SCHEDULES_TABLE}" (
                    schedule_key, name, kind, schedule, timezone, run_at,
                    instruction, enabled, next_run_at, last_fired_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                [_schedule_snapshot_values(row) for row in schedules],
            )
            _create_schedule_table_guards(conn)
            conn.commit()
            return snapshot
    except (RuntimeError, sqlite3.Error):
        logger.exception("Failed to seed agent config tables for agent %s", getattr(agent, "id", None))
        return None


def apply_sqlite_agent_config_updates(
    agent,
    baseline: Optional[AgentConfigSnapshot],
) -> AgentConfigApplyResult:
    """Validate and persist configuration table changes, then drop the tables."""
    updated_fields: list[str] = []
    errors: dict[str, str] = {}
    current = read_sqlite_agent_config_snapshot()

    if baseline is None:
        _drop_agent_config_tables()
        return AgentConfigApplyResult(updated_fields=(), errors=errors)
    if current is None:
        _drop_agent_config_tables()
        return AgentConfigApplyResult(
            updated_fields=(),
            errors={"schedules": "Schedule configuration was malformed; no configuration changes were applied."},
            schedules=baseline.schedules,
        )

    normalized_current_charter = _normalize_charter(current.charter)
    normalized_baseline_charter = _normalize_charter(baseline.charter)
    if normalized_current_charter != normalized_baseline_charter:
        if count_literal_newlines(normalized_current_charter) > count_literal_newlines(
            normalized_baseline_charter
        ):
            errors["charter"] = (
                "Charter update rejected because it introduced literal \\n text. "
                "Use actual newline characters in charter Markdown."
            )
        else:
            result = execute_update_charter(agent, {"new_charter": normalized_current_charter})
            if isinstance(result, dict) and result.get("status") == "ok":
                updated_fields.append("charter")
            else:
                errors["charter"] = (
                    result.get("message", "Charter update failed.")
                    if isinstance(result, dict)
                    else "Charter update failed."
                )

    if (
        current.emotion_write_count != baseline.emotion_write_count
        or _emotion_value(current) != _emotion_value(baseline)
    ):
        try:
            emotion, emotion_expires_at = normalize_emotion_update(
                current.emotion,
                current.emotion_timeout_seconds,
            )
            agent.emotion = emotion
            agent.emotion_expires_at = emotion_expires_at
            agent.save(update_fields=["emotion", "emotion_expires_at"])
        except ValidationError as exc:
            errors["emotion"] = _validation_message(exc)
        else:
            updated_fields.append("emotion")

    legacy_schedule_changed = _normalize_schedule(current.schedule) != _normalize_schedule(baseline.schedule)
    schedule_rows_changed = _schedule_user_rows(current.schedules) != _schedule_user_rows(baseline.schedules)
    if legacy_schedule_changed or schedule_rows_changed:
        schedule_error_key = "schedules" if schedule_rows_changed else "schedule"
        schedule_error = _validate_schedule_mutation(baseline, current)
        if schedule_error:
            errors[schedule_error_key] = schedule_error
        elif _planning_mode_active(agent):
            errors[schedule_error_key] = (
                "Schedule updates are unavailable while planning mode is active. "
                "Complete or skip planning first."
            )
        else:
            try:
                _apply_schedule_mutation(
                    agent,
                    baseline,
                    current,
                )
            except (ValidationError, ValueError, _ScheduleApplyError) as exc:
                errors[schedule_error_key] = _validation_message(exc)
                agent.refresh_from_db(fields=["schedule"])
            else:
                if legacy_schedule_changed:
                    updated_fields.append("schedule")
                if schedule_rows_changed:
                    updated_fields.append("schedules")

    result_schedules = (
        _persistent_schedule_snapshots(agent)
        if not (errors.get("schedule") or errors.get("schedules"))
        else baseline.schedules
    )
    _drop_agent_config_tables()
    return AgentConfigApplyResult(
        updated_fields=tuple(updated_fields),
        errors=errors,
        schedules=result_schedules,
    )


def read_sqlite_agent_config_snapshot() -> Optional[AgentConfigSnapshot]:
    db_path = get_sqlite_db_path()
    if not db_path:
        return None

    try:
        with _guarded_connection(db_path) as conn:
            config_row = conn.execute(
                f'''SELECT charter, schedule, emotion, emotion_timeout_seconds, _emotion_write_count
                    FROM "{AGENT_CONFIG_TABLE}" WHERE id = 1;'''
            ).fetchone()
            if not config_row:
                return None
            emotion, emotion_timeout_seconds = _sqlite_emotion_value(config_row[2], config_row[3])
            emotion_write_count = config_row[4]
            if type(emotion_write_count) is not int or emotion_write_count < 0:
                raise ValueError("emotion write count is invalid")
            schedule_rows = conn.execute(
                f"""
                SELECT schedule_key, name, kind, schedule, timezone, run_at,
                       instruction, enabled, next_run_at, last_fired_at
                FROM "{AGENT_SCHEDULES_TABLE}"
                ORDER BY schedule_key;
                """
            ).fetchall()
            schedules = tuple(_sqlite_row_to_schedule_snapshot(row) for row in schedule_rows)
            return AgentConfigSnapshot(
                charter=config_row[0] or "",
                schedule=config_row[1],
                emotion=emotion,
                emotion_timeout_seconds=emotion_timeout_seconds,
                emotion_write_count=emotion_write_count,
                schedules=schedules,
            )
    except (RuntimeError, sqlite3.Error, ValueError, TypeError):
        logger.exception("Failed to read agent config tables.")
        return None


def _persistent_schedule_snapshots(agent) -> tuple[AgentScheduleSnapshot, ...]:
    from api.models import PersistentAgentSchedule

    primary = AgentScheduleSnapshot(
        schedule_key=PRIMARY_SCHEDULE_KEY,
        name=PRIMARY_SCHEDULE_NAME,
        kind="recurring",
        schedule=_normalize_schedule(agent.schedule),
        timezone="UTC",
        run_at=None,
        instruction="",
        enabled=bool(_normalize_schedule(agent.schedule)),
    )
    additional = (
        AgentScheduleSnapshot(
            schedule_key=row.schedule_key,
            name=row.name,
            kind=row.kind,
            schedule=row.expression,
            timezone=row.timezone,
            run_at=_serialize_datetime(row.run_at),
            instruction=row.instruction,
            enabled=row.enabled,
            next_run_at=_serialize_datetime(row.next_run_at),
            last_fired_at=_serialize_datetime(row.last_fired_at),
        )
        for row in PersistentAgentSchedule.objects.filter(agent=agent).order_by("schedule_key")
    )
    return tuple(sorted((primary, *additional), key=lambda row: row.schedule_key))


def _create_schedule_table_guards(conn) -> None:
    conn.execute(
        f"""
        CREATE TRIGGER __agent_schedules_derived_insert
        BEFORE INSERT ON "{AGENT_SCHEDULES_TABLE}"
        WHEN NEW.next_run_at IS NOT NULL OR NEW.last_fired_at IS NOT NULL
        BEGIN
            SELECT RAISE(ABORT, 'next_run_at and last_fired_at are read-only');
        END;
        """
    )
    conn.execute(
        f"""
        CREATE TRIGGER __agent_schedules_derived_update
        BEFORE UPDATE OF next_run_at, last_fired_at ON "{AGENT_SCHEDULES_TABLE}"
        BEGIN
            SELECT RAISE(ABORT, 'next_run_at and last_fired_at are read-only');
        END;
        """
    )
    conn.execute(
        f"""
        CREATE TRIGGER __agent_schedules_key_update
        BEFORE UPDATE OF schedule_key ON "{AGENT_SCHEDULES_TABLE}"
        BEGIN
            SELECT RAISE(ABORT, 'schedule_key is immutable; delete and insert instead');
        END;
        """
    )


def _validate_schedule_mutation(
    baseline: AgentConfigSnapshot,
    current: AgentConfigSnapshot,
) -> Optional[str]:
    baseline_by_key = {row.schedule_key: row for row in baseline.schedules}
    current_by_key = {row.schedule_key: row for row in current.schedules}
    for row in current.schedules:
        if not SCHEDULE_KEY_RE.fullmatch(row.schedule_key):
            return f"Invalid schedule_key '{row.schedule_key}'."
        if not row.name.strip() or len(row.name) > 120:
            return f"Schedule '{row.schedule_key}' needs a name of at most 120 characters."
        if row.kind not in {"recurring", "once"}:
            return f"Schedule '{row.schedule_key}' has an invalid kind."
        if not row.timezone.strip() or len(row.timezone) > 64:
            return f"Schedule '{row.schedule_key}' needs a valid timezone."
        if len(row.instruction) > 500:
            return f"Schedule '{row.schedule_key}' instruction cannot exceed 500 characters."
        if row.schedule_key == PRIMARY_SCHEDULE_KEY:
            if (
                row.name != PRIMARY_SCHEDULE_NAME
                or row.kind != "recurring"
                or row.timezone != "UTC"
                or row.run_at is not None
                or row.instruction
            ):
                return "The primary row only supports schedule and enabled changes."
            if row.enabled != bool(_normalize_schedule(row.schedule)):
                return "The primary row must be enabled when it has a schedule and disabled when empty."
        elif row.kind == "recurring":
            if not _normalize_schedule(row.schedule) or row.run_at is not None:
                return f"Recurring schedule '{row.schedule_key}' needs schedule and no run_at."
        elif not row.run_at or _normalize_schedule(row.schedule) is not None:
            return f"One-time schedule '{row.schedule_key}' needs run_at and no schedule."

        baseline_row = baseline_by_key.get(row.schedule_key)
        if baseline_row:
            if (
                row.next_run_at != baseline_row.next_run_at
                or row.last_fired_at != baseline_row.last_fired_at
            ):
                return "next_run_at and last_fired_at are server-managed and cannot be changed."
        elif row.next_run_at is not None or row.last_fired_at is not None:
            return "New schedules cannot set next_run_at or last_fired_at."

    table_primary_changed = _schedule_row_user_value(current_by_key.get(PRIMARY_SCHEDULE_KEY)) != (
        _schedule_row_user_value(baseline_by_key.get(PRIMARY_SCHEDULE_KEY))
    )
    legacy_changed = _normalize_schedule(current.schedule) != _normalize_schedule(baseline.schedule)
    if table_primary_changed and legacy_changed:
        table_value = _primary_schedule_value(current_by_key.get(PRIMARY_SCHEDULE_KEY))
        if table_value != _normalize_schedule(current.schedule):
            return "Conflicting primary schedule changes were made in both configuration tables."
    return None


def _apply_schedule_mutation(
    agent,
    baseline: AgentConfigSnapshot,
    current: AgentConfigSnapshot,
) -> None:
    from api.services.agent_schedules import reconcile_agent_schedules

    baseline_by_key = {row.schedule_key: row for row in baseline.schedules}
    current_by_key = {row.schedule_key: row for row in current.schedules}
    table_primary_changed = _schedule_row_user_value(current_by_key.get(PRIMARY_SCHEDULE_KEY)) != (
        _schedule_row_user_value(baseline_by_key.get(PRIMARY_SCHEDULE_KEY))
    )
    desired_primary = (
        _primary_schedule_value(current_by_key.get(PRIMARY_SCHEDULE_KEY))
        if table_primary_changed
        else _normalize_schedule(current.schedule)
    )
    baseline_additional = tuple(
        row for row in baseline.schedules if row.schedule_key != PRIMARY_SCHEDULE_KEY
    )
    current_additional = tuple(
        row for row in current.schedules if row.schedule_key != PRIMARY_SCHEDULE_KEY
    )
    additional_changed = _schedule_user_rows(current_additional) != _schedule_user_rows(baseline_additional)
    primary_changed = desired_primary != _normalize_schedule(baseline.schedule)

    with transaction.atomic():
        if primary_changed:
            result = execute_update_schedule(agent, {"new_schedule": desired_primary})
            if not isinstance(result, dict) or result.get("status") != "ok":
                message = (
                    result.get("message", "Primary schedule update failed.")
                    if isinstance(result, dict)
                    else "Primary schedule update failed."
                )
                raise _ScheduleApplyError(message)
        if additional_changed or primary_changed:
            reconcile_agent_schedules(
                agent,
                [_schedule_service_row(row) for row in current_additional],
            )


def _planning_mode_active(agent) -> bool:
    from api.models import PersistentAgent

    return agent.planning_state == PersistentAgent.PlanningState.PLANNING


def _schedule_service_row(row: AgentScheduleSnapshot) -> dict:
    return {
        "schedule_key": row.schedule_key,
        "name": row.name.strip(),
        "instruction": row.instruction.strip(),
        "kind": row.kind,
        "expression": _normalize_schedule(row.schedule),
        "timezone": row.timezone.strip(),
        "run_at": row.run_at,
        "enabled": row.enabled,
    }


def _schedule_snapshot_values(row: AgentScheduleSnapshot) -> tuple:
    return (
        row.schedule_key,
        row.name,
        row.kind,
        row.schedule,
        row.timezone,
        row.run_at,
        row.instruction,
        int(row.enabled),
        row.next_run_at,
        row.last_fired_at,
    )


def _sqlite_row_to_schedule_snapshot(row) -> AgentScheduleSnapshot:
    if not isinstance(row[7], int) or row[7] not in {0, 1}:
        raise ValueError("enabled must be 0 or 1")
    values = (row[0], row[1], row[2], row[4], row[6])
    if not all(isinstance(value, str) for value in values):
        raise ValueError("schedule text fields have invalid types")
    optional_values = (row[3], row[5], row[8], row[9])
    if any(value is not None and not isinstance(value, str) for value in optional_values):
        raise ValueError("schedule optional fields have invalid types")
    return AgentScheduleSnapshot(
        schedule_key=row[0],
        name=row[1],
        kind=row[2],
        schedule=row[3],
        timezone=row[4],
        run_at=row[5],
        instruction=row[6],
        enabled=bool(row[7]),
        next_run_at=row[8],
        last_fired_at=row[9],
    )


def _schedule_user_rows(rows: tuple[AgentScheduleSnapshot, ...]) -> tuple:
    return tuple(sorted((_schedule_row_user_value(row) for row in rows), key=lambda row: row[0]))


def _schedule_row_user_value(row: Optional[AgentScheduleSnapshot]) -> Optional[tuple]:
    if row is None:
        return None
    return (
        row.schedule_key,
        row.name.strip(),
        row.kind,
        _normalize_schedule(row.schedule),
        row.timezone.strip(),
        row.run_at.strip() if row.run_at else None,
        row.instruction.strip(),
        row.enabled,
    )


def _primary_schedule_value(row: Optional[AgentScheduleSnapshot]) -> Optional[str]:
    if row is None or not row.enabled:
        return None
    return _normalize_schedule(row.schedule)


def _serialize_datetime(value) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _validation_message(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        if hasattr(exc, "message_dict"):
            return " ".join(
                str(message)
                for messages in exc.message_dict.values()
                for message in messages
            )
        return " ".join(str(message) for message in exc.messages)
    return str(exc)


def _sqlite_emotion_value(emotion, timeout_seconds) -> tuple[Optional[str], Optional[int]]:
    if emotion is None and timeout_seconds is None:
        return None, None
    if not isinstance(emotion, str) or type(timeout_seconds) is not int:
        raise ValueError("emotion and emotion_timeout_seconds have invalid types")
    return emotion, timeout_seconds


def _emotion_value(snapshot: AgentConfigSnapshot) -> tuple[Optional[str], Optional[int]]:
    return snapshot.emotion, snapshot.emotion_timeout_seconds


def _drop_agent_config_tables() -> None:
    db_path = get_sqlite_db_path()
    if not db_path:
        return

    try:
        with _guarded_connection(db_path) as conn:
            conn.execute(f'DROP TABLE IF EXISTS "{AGENT_CONFIG_TABLE}";')
            conn.execute(f'DROP TABLE IF EXISTS "{AGENT_SCHEDULES_TABLE}";')
            conn.commit()
    except (RuntimeError, sqlite3.Error):
        logger.exception("Failed to drop agent config tables.")


def _normalize_charter(value: Optional[str]) -> str:
    return (value or "").strip()


def _normalize_schedule(value: Optional[str]) -> Optional[str]:
    return value.strip() or None if value is not None else None
