"""
SQLite-backed agent config helpers.

Seeds an ephemeral config table for each LLM invocation and applies updates
after tool execution. This keeps charter/schedule changes in SQLite while
persisting final values to Postgres.
"""

import logging
from dataclasses import dataclass
from typing import Optional, Sequence

from .charter_updater import execute_update_charter
from .schedule_updater import execute_update_schedule
from .sqlite_guardrails import clear_guarded_connection, open_guarded_sqlite_connection
from .sqlite_state import AGENT_CONFIG_TABLE, get_sqlite_db_path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentConfigSnapshot:
    charter: str
    schedule: Optional[str]


@dataclass(frozen=True)
class AgentConfigApplyResult:
    updated_fields: Sequence[str]
    errors: Sequence[str]


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
        conn.execute(
            f"""
            CREATE TABLE "{AGENT_CONFIG_TABLE}" (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                charter TEXT,
                schedule TEXT
            );
            """
        )
        charter = agent.charter or ""
        schedule = agent.schedule
        conn.execute(
            f'INSERT INTO "{AGENT_CONFIG_TABLE}" (id, charter, schedule) VALUES (1, ?, ?);',
            (charter, schedule),
        )
        conn.commit()
        return AgentConfigSnapshot(charter=charter, schedule=schedule)
    except Exception:
        logger.exception("Failed to seed agent config table for agent %s", getattr(agent, "id", None))
        return None
    finally:
        if conn is not None:
            try:
                clear_guarded_connection(conn)
                conn.close()
            except Exception:
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

    _drop_agent_config_table()
    return AgentConfigApplyResult(updated_fields=updated_fields, errors=errors)


def _read_agent_config_snapshot() -> Optional[AgentConfigSnapshot]:
    db_path = get_sqlite_db_path()
    if not db_path:
        return None

    conn = None
    try:
        conn = open_guarded_sqlite_connection(db_path)
        cur = conn.cursor()
        cur.execute(f'SELECT charter, schedule FROM "{AGENT_CONFIG_TABLE}" WHERE id = 1;')
        row = cur.fetchone()
        if not row:
            return None
        return AgentConfigSnapshot(charter=row[0] or "", schedule=row[1])
    except Exception:
        logger.exception("Failed to read agent config table.")
        return None
    finally:
        if conn is not None:
            try:
                clear_guarded_connection(conn)
                conn.close()
            except Exception:
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
    except Exception:
        logger.exception("Failed to drop agent config table.")
    finally:
        if conn is not None:
            try:
                clear_guarded_connection(conn)
                conn.close()
            except Exception:
                pass


def _normalize_charter(value: Optional[str]) -> str:
    return (value or "").strip()


def _normalize_schedule(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None
