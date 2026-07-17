"""
SQLite-backed agent config helpers.

Seeds an ephemeral config table for each LLM invocation and applies updates
after tool execution. This keeps charter/schedule changes in SQLite while
persisting final values to Postgres.
"""

import logging
import re
from dataclasses import dataclass
from hashlib import sha256
from typing import Optional, Sequence

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


@dataclass(frozen=True)
class AgentConfigSnapshot:
    charter: str
    schedule: Optional[str]


@dataclass(frozen=True)
class AgentConfigApplyResult:
    attempted_fields: Sequence[str]
    updated_fields: Sequence[str]
    unchanged_fields: Sequence[str]
    errors: Sequence[str]
    charter_hash_before: str
    charter_hash_after: str


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
    attempted_fields: Sequence[str] = (),
) -> AgentConfigApplyResult:
    """Apply any SQLite config updates to the persistent agent record."""
    field_order = ("charter", "schedule")
    attempted_field_set = set(attempted_fields)
    normalized_attempted_fields = tuple(
        field for field in field_order if field in attempted_field_set
    )
    errors: list[str] = []
    current = read_sqlite_agent_config_snapshot()
    persisted_before = AgentConfigSnapshot(
        charter=agent.charter or "",
        schedule=agent.schedule,
    )

    if baseline is None or current is None:
        _drop_agent_config_table()
        if normalized_attempted_fields:
            errors.append("Agent config state was unavailable; no attempted updates were persisted.")
        return _build_apply_result(
            attempted_fields=normalized_attempted_fields,
            before=persisted_before,
            after=persisted_before,
            errors=errors,
        )

    if _normalize_charter(current.charter) != _normalize_charter(baseline.charter):
        result = execute_update_charter(agent, {"new_charter": _normalize_charter(current.charter)})
        if not isinstance(result, dict) or result.get("status") != "ok":
            errors.append(result.get("message", "Charter update failed.") if isinstance(result, dict) else "Charter update failed.")

    if _normalize_schedule(current.schedule) != _normalize_schedule(baseline.schedule):
        result = execute_update_schedule(agent, {"new_schedule": current.schedule})
        if not isinstance(result, dict) or result.get("status") != "ok":
            errors.append(result.get("message", "Schedule update failed.") if isinstance(result, dict) else "Schedule update failed.")

    _drop_agent_config_table()
    agent.refresh_from_db(fields=["charter", "schedule"])
    persisted_after = AgentConfigSnapshot(
        charter=agent.charter or "",
        schedule=agent.schedule,
    )
    effective_attempted_fields = tuple(
        field
        for field in field_order
        if field in normalized_attempted_fields or _snapshot_field_changed(baseline, current, field)
    )
    return _build_apply_result(
        attempted_fields=effective_attempted_fields,
        before=persisted_before,
        after=persisted_after,
        errors=errors,
    )


def _build_apply_result(
    *,
    attempted_fields: Sequence[str],
    before: AgentConfigSnapshot,
    after: AgentConfigSnapshot,
    errors: Sequence[str],
) -> AgentConfigApplyResult:
    updated_fields = tuple(
        field for field in attempted_fields if _snapshot_field_changed(before, after, field)
    )
    unchanged_fields = tuple(field for field in attempted_fields if field not in updated_fields)
    return AgentConfigApplyResult(
        attempted_fields=tuple(attempted_fields),
        updated_fields=updated_fields,
        unchanged_fields=unchanged_fields,
        errors=tuple(errors),
        charter_hash_before=_charter_hash(before.charter),
        charter_hash_after=_charter_hash(after.charter),
    )


def _snapshot_field_changed(
    before: AgentConfigSnapshot,
    after: AgentConfigSnapshot,
    field: str,
) -> bool:
    if field == "charter":
        return _normalize_charter(before.charter) != _normalize_charter(after.charter)
    if field == "schedule":
        return _normalize_schedule(before.schedule) != _normalize_schedule(after.schedule)
    return False


def _charter_hash(value: Optional[str]) -> str:
    return sha256(_normalize_charter(value).encode("utf-8")).hexdigest()


def read_sqlite_agent_config_snapshot() -> Optional[AgentConfigSnapshot]:
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
