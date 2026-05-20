"""
SQLite-backed agent config helpers.

Seeds an ephemeral config table for each LLM invocation and applies updates
after tool execution. This keeps charter, schedule, and permanent instruction
changes in SQLite while persisting final values to Postgres.
"""

import logging
import sqlite3
from dataclasses import dataclass
from typing import Optional, Sequence

from django.core.exceptions import ValidationError
from django.db import DatabaseError

from ...models import PersistentAgent
from .charter_updater import execute_update_charter
from .schedule_updater import execute_update_schedule
from .sqlite_guardrails import clear_guarded_connection, open_guarded_sqlite_connection
from .sqlite_state import AGENT_CONFIG_TABLE, get_sqlite_db_path

logger = logging.getLogger(__name__)

AGENT_CONFIG_VALUE_COLUMNS = ("charter", "schedule", "permanent_instructions")
AGENT_CONFIG_COLUMN_DEFINITIONS = {
    "charter": "TEXT",
    "schedule": "TEXT",
    "permanent_instructions": "TEXT",
}


@dataclass(frozen=True)
class AgentConfigSnapshot:
    charter: str
    schedule: Optional[str]
    permanent_instructions: str


@dataclass(frozen=True)
class AgentConfigApplyResult:
    updated_fields: Sequence[str]
    errors: Sequence[str]


def _agent_config_create_sql() -> str:
    value_columns_sql = ", ".join(
        f"{column} {AGENT_CONFIG_COLUMN_DEFINITIONS[column]}"
        for column in AGENT_CONFIG_VALUE_COLUMNS
    )
    return (
        f'CREATE TABLE "{AGENT_CONFIG_TABLE}" '
        f"(id INTEGER PRIMARY KEY CHECK (id = 1), {value_columns_sql});"
    )


def _agent_config_insert_sql() -> str:
    columns = ("id", *AGENT_CONFIG_VALUE_COLUMNS)
    placeholders = ", ".join("?" for _column in columns)
    column_sql = ", ".join(columns)
    return f'INSERT INTO "{AGENT_CONFIG_TABLE}" ({column_sql}) VALUES ({placeholders});'


def _agent_config_select_sql() -> str:
    column_sql = ", ".join(AGENT_CONFIG_VALUE_COLUMNS)
    return f'SELECT {column_sql} FROM "{AGENT_CONFIG_TABLE}" WHERE id = 1;'


def seed_sqlite_agent_config(agent) -> Optional[AgentConfigSnapshot]:
    """Create/reset the agent config table and seed it with current values."""
    db_path = get_sqlite_db_path()
    if not db_path:
        logger.warning("SQLite DB path unavailable; cannot seed agent config.")
        return None

    conn = None
    try:
        conn = open_guarded_sqlite_connection(db_path)
        conn.execute(f'DROP TABLE IF EXISTS "{AGENT_CONFIG_TABLE}";')
        conn.execute(_agent_config_create_sql())
        charter = agent.charter or ""
        schedule = agent.schedule
        permanent_instructions = agent.permanent_instructions or ""
        conn.execute(
            _agent_config_insert_sql(),
            (1, charter, schedule, permanent_instructions),
        )
        conn.commit()
        return AgentConfigSnapshot(
            charter=charter,
            schedule=schedule,
            permanent_instructions=permanent_instructions,
        )
    except sqlite3.Error:
        logger.exception("Failed to seed agent config table for agent %s", getattr(agent, "id", None))
        return None
    finally:
        if conn is not None:
            try:
                clear_guarded_connection(conn)
                conn.close()
            except sqlite3.Error:
                pass


def apply_sqlite_agent_config_updates(
    agent,
    baseline: Optional[AgentConfigSnapshot],
) -> AgentConfigApplyResult:
    """Apply any SQLite config updates to the persistent agent record."""
    updated_fields: list[str] = []
    errors: list[str] = []
    current = _read_agent_config_snapshot()

    if baseline is None or current is None:
        _drop_agent_config_table()
        return AgentConfigApplyResult(updated_fields=updated_fields, errors=errors)

    if _normalize_charter(current.charter) != _normalize_charter(baseline.charter):
        result = execute_update_charter(agent, {"new_charter": _normalize_charter(current.charter)})
        if isinstance(result, dict) and result.get("status") == "ok":
            updated_fields.append("charter")
        else:
            errors.append(result.get("message", "Charter update failed.") if isinstance(result, dict) else "Charter update failed.")

    if _normalize_schedule(current.schedule) != _normalize_schedule(baseline.schedule):
        result = execute_update_schedule(agent, {"new_schedule": current.schedule})
        if isinstance(result, dict) and result.get("status") == "ok":
            updated_fields.append("schedule")
        else:
            errors.append(result.get("message", "Schedule update failed.") if isinstance(result, dict) else "Schedule update failed.")

    new_permanent_instructions = _normalize_permanent_instructions(current.permanent_instructions)
    if new_permanent_instructions != _normalize_permanent_instructions(baseline.permanent_instructions):
        if _permanent_instructions_update_blocked_by_planning(agent, new_permanent_instructions):
            errors.append(_planning_mode_permanent_instructions_message())
        else:
            try:
                agent.permanent_instructions = new_permanent_instructions
                agent.save(update_fields=["permanent_instructions"])
                updated_fields.append("permanent_instructions")
            except (DatabaseError, ValidationError, ValueError) as exc:
                logger.exception(
                    "Failed to update permanent instructions for agent %s",
                    getattr(agent, "id", None),
                )
                errors.append(f"Permanent instructions update failed: {exc}")

    _drop_agent_config_table()
    return AgentConfigApplyResult(updated_fields=updated_fields, errors=errors)


def _planning_mode_permanent_instructions_message() -> str:
    return (
        "Permanent instructions updates are unavailable while planning mode is active. "
        "Complete or skip planning first."
    )


def _permanent_instructions_update_blocked_by_planning(agent, new_value: str) -> bool:
    return (
        getattr(agent, "planning_state", None) == PersistentAgent.PlanningState.PLANNING
        and _normalize_permanent_instructions(new_value)
        != _normalize_permanent_instructions(getattr(agent, "permanent_instructions", ""))
    )


def _read_agent_config_snapshot() -> Optional[AgentConfigSnapshot]:
    db_path = get_sqlite_db_path()
    if not db_path:
        return None

    conn = None
    try:
        conn = open_guarded_sqlite_connection(db_path)
        cur = conn.cursor()
        cur.execute(_agent_config_select_sql())
        row = cur.fetchone()
        if not row:
            return None
        values = dict(zip(AGENT_CONFIG_VALUE_COLUMNS, row))
        return AgentConfigSnapshot(
            charter=values["charter"] or "",
            schedule=values["schedule"],
            permanent_instructions=values["permanent_instructions"] or "",
        )
    except sqlite3.Error:
        logger.exception("Failed to read agent config table.")
        return None
    finally:
        if conn is not None:
            try:
                clear_guarded_connection(conn)
                conn.close()
            except sqlite3.Error:
                pass


def _drop_agent_config_table() -> None:
    db_path = get_sqlite_db_path()
    if not db_path:
        return

    conn = None
    try:
        conn = open_guarded_sqlite_connection(db_path)
        conn.execute(f'DROP TABLE IF EXISTS "{AGENT_CONFIG_TABLE}";')
        conn.commit()
    except sqlite3.Error:
        logger.exception("Failed to drop agent config table.")
    finally:
        if conn is not None:
            try:
                clear_guarded_connection(conn)
                conn.close()
            except sqlite3.Error:
                pass


def _normalize_charter(value: Optional[str]) -> str:
    return (value or "").strip()


def _normalize_schedule(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


def _normalize_permanent_instructions(value: Optional[str]) -> str:
    return (value or "").strip()
