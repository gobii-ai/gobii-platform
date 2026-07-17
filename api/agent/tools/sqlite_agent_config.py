"""
SQLite-backed agent config helpers.

Seeds an ephemeral config table for each LLM invocation and applies updates
after tool execution. This keeps charter/schedule changes in SQLite while
persisting final values to Postgres.
"""

import logging
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Optional

from .charter_updater import execute_update_charter
from .schedule_updater import execute_update_schedule
from .sqlite_guardrails import clear_guarded_connection, open_guarded_sqlite_connection
from .sqlite_state import AGENT_CONFIG_TABLE, get_sqlite_db_path

logger = logging.getLogger(__name__)

AGENT_CONFIG_UPDATE_RE = re.compile(
    r'''\bupdate\s+["`\[]?__agent_config["`\]]?\s+.*?\bset\b'''
    r'''(?P<assignments>(?:(?:'(?:[^']|'')*'|"(?:[^"]|"")*")|'''
    r'''(?!(?:\bwhere\b|\breturning\b))[\s\S])*)''',
    re.IGNORECASE,
)
AGENT_CONFIG_INSERT_RE = re.compile(
    r'\b(?:insert|replace)\s+(?:or\s+\w+\s+)?into\s+["`\[]?__agent_config["`\]]?\s*\((?P<columns>[^)]*)\)',
    re.IGNORECASE | re.DOTALL,
)


@contextmanager
def _guarded_connection(db_path: str):
    conn = open_guarded_sqlite_connection(db_path)
    try:
        yield conn
    finally:
        clear_guarded_connection(conn)
        conn.close()


@dataclass(frozen=True)
class AgentConfigSnapshot:
    charter: str
    schedule: Optional[str]


@dataclass(frozen=True)
class AgentConfigApplyResult:
    updated_fields: tuple[str, ...]
    errors: dict[str, str]


def sqlite_statement_assigns_agent_config_field(statement: str, field_name: str) -> bool:
    field = field_name.lower()
    update_match = AGENT_CONFIG_UPDATE_RE.search(statement or "")
    if update_match:
        assignments = update_match.group("assignments")
        return bool(
            re.search(
                rf'(?<![\w"`\]])["`\[]?{re.escape(field)}["`\]]?\s*=',
                assignments,
                re.IGNORECASE,
            )
        )

    insert_match = AGENT_CONFIG_INSERT_RE.search(statement or "")
    if not insert_match:
        return False
    columns = {
        column.strip().strip('"`[]').lower()
        for column in insert_match.group("columns").split(",")
    }
    return field in columns


def seed_sqlite_agent_config(agent) -> Optional[AgentConfigSnapshot]:
    """Create/reset the agent config table and seed it with current values."""
    db_path = get_sqlite_db_path()
    if not db_path:
        logger.warning("SQLite DB path unavailable; cannot seed agent config.")
        return None

    try:
        with _guarded_connection(db_path) as conn:
            conn.execute(f'DROP TABLE IF EXISTS "{AGENT_CONFIG_TABLE}";')
            conn.execute(
                f"""
                CREATE TABLE "{AGENT_CONFIG_TABLE}" (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    charter TEXT,
                    schedule TEXT
                );
                """
            )
            snapshot = AgentConfigSnapshot(charter=agent.charter or "", schedule=agent.schedule)
            conn.execute(
                f'INSERT INTO "{AGENT_CONFIG_TABLE}" (id, charter, schedule) VALUES (1, ?, ?);',
                (snapshot.charter, snapshot.schedule),
            )
            conn.commit()
            return snapshot
    except (RuntimeError, sqlite3.Error):
        logger.exception("Failed to seed agent config table for agent %s", getattr(agent, "id", None))
        return None


def apply_sqlite_agent_config_updates(
    agent,
    baseline: Optional[AgentConfigSnapshot],
) -> AgentConfigApplyResult:
    """Apply any SQLite config updates to the persistent agent record."""
    updated_fields: list[str] = []
    errors: dict[str, str] = {}
    current = read_sqlite_agent_config_snapshot()

    if baseline is None or current is None:
        _drop_agent_config_table()
        return AgentConfigApplyResult(updated_fields=(), errors=errors)

    if _normalize_charter(current.charter) != _normalize_charter(baseline.charter):
        result = execute_update_charter(agent, {"new_charter": _normalize_charter(current.charter)})
        if isinstance(result, dict) and result.get("status") == "ok":
            updated_fields.append("charter")
        else:
            errors["charter"] = result.get("message", "Charter update failed.") if isinstance(result, dict) else "Charter update failed."

    if _normalize_schedule(current.schedule) != _normalize_schedule(baseline.schedule):
        result = execute_update_schedule(agent, {"new_schedule": current.schedule})
        if isinstance(result, dict) and result.get("status") == "ok":
            updated_fields.append("schedule")
        else:
            errors["schedule"] = result.get("message", "Schedule update failed.") if isinstance(result, dict) else "Schedule update failed."

    _drop_agent_config_table()
    return AgentConfigApplyResult(updated_fields=tuple(updated_fields), errors=errors)


def read_sqlite_agent_config_snapshot() -> Optional[AgentConfigSnapshot]:
    db_path = get_sqlite_db_path()
    if not db_path:
        return None

    try:
        with _guarded_connection(db_path) as conn:
            row = conn.execute(
                f'SELECT charter, schedule FROM "{AGENT_CONFIG_TABLE}" WHERE id = 1;'
            ).fetchone()
            return AgentConfigSnapshot(charter=row[0] or "", schedule=row[1]) if row else None
    except (RuntimeError, sqlite3.Error):
        logger.exception("Failed to read agent config table.")
        return None


def _drop_agent_config_table() -> None:
    db_path = get_sqlite_db_path()
    if not db_path:
        return

    try:
        with _guarded_connection(db_path) as conn:
            conn.execute(f'DROP TABLE IF EXISTS "{AGENT_CONFIG_TABLE}";')
            conn.commit()
    except (RuntimeError, sqlite3.Error):
        logger.exception("Failed to drop agent config table.")


def _normalize_charter(value: Optional[str]) -> str:
    return (value or "").strip()


def _normalize_schedule(value: Optional[str]) -> Optional[str]:
    return value.strip() or None if value is not None else None
