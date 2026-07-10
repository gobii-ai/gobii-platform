"""
SQLite-backed agent config helpers.

Seeds an ephemeral config table for each LLM invocation and applies updates
after tool execution. This keeps charter/schedule changes in SQLite while
persisting final values to Postgres.
"""

import logging
import re
from dataclasses import dataclass
from typing import Any, Optional, Sequence

import sqlparse
from django.db import transaction
from sqlglot import exp, parse
from sqlglot.errors import ParseError

from .charter_updater import execute_update_charter
from .schedule_updater import execute_update_schedule
from .sqlite_guardrails import clear_guarded_connection, open_guarded_sqlite_connection
from .sqlite_state import AGENT_CONFIG_TABLE, get_sqlite_db_path

logger = logging.getLogger(__name__)
_CONFIG_MUTATION_RE = re.compile(r"\b(?:insert|update|delete|replace|alter|drop|create)\b", re.IGNORECASE)
_CONFIG_TABLE_RE = re.compile(r"\b__agent_config\b", re.IGNORECASE)


@dataclass(frozen=True)
class AgentConfigSnapshot:
    charter: str
    schedule: Optional[str]


@dataclass(frozen=True)
class AgentConfigApplyResult:
    updated_fields: Sequence[str]
    errors: Sequence[str]


def sqlite_batch_mutates_agent_config(params: dict[str, Any]) -> bool:
    return any(
        _CONFIG_TABLE_RE.search(statement) and _CONFIG_MUTATION_RE.search(statement)
        for raw_item in _sqlite_batch_sql_items(params)
        for statement in sqlparse.split(raw_item)
    )


def _sqlite_batch_sql_items(params: dict[str, Any]) -> tuple[str, ...]:
    raw_sql = params.get("queries", params.get("sql", params.get("query")))
    seen_wrappers: set[int] = set()
    while isinstance(raw_sql, dict) and id(raw_sql) not in seen_wrappers:
        seen_wrappers.add(id(raw_sql))
        raw_sql = raw_sql.get("queries", raw_sql.get("sql", raw_sql.get("query")))
    raw_items = raw_sql if isinstance(raw_sql, list) else [raw_sql]
    return tuple(raw_item for raw_item in raw_items if isinstance(raw_item, str))


def sqlite_batch_has_destructive_charter_replacement(params: dict[str, Any], baseline: str) -> bool:
    """Detect charter writes that discard the existing bytes instead of patching them."""
    if not baseline:
        return False
    for raw_item in _sqlite_batch_sql_items(params):
        try:
            statements = parse(raw_item, read="sqlite")
        except ParseError:
            continue
        for statement in statements:
            if (
                isinstance(statement, exp.Command)
                and str(statement.this).casefold() == "replace"
                and _CONFIG_TABLE_RE.search(str(statement.expression))
            ):
                return True
            if isinstance(statement, exp.Update) and _table_name(statement.this) == AGENT_CONFIG_TABLE:
                for assignment in statement.expressions:
                    if (
                        isinstance(assignment, exp.EQ)
                        and _is_charter_column(assignment.this)
                        and not _charter_expression_preserves_baseline(assignment.expression, baseline)
                    ):
                        return True
            if isinstance(statement, exp.Insert) and _insert_replaces_charter(statement, baseline):
                return True
    return False


def _table_name(expression) -> str:
    table = expression.this if isinstance(expression, exp.Schema) else expression
    return table.name.casefold() if isinstance(table, exp.Table) else ""


def _is_charter_column(expression) -> bool:
    return bool(
        isinstance(expression, exp.Column)
        and expression.name.casefold() == "charter"
        and not expression.table
    )


def _charter_expression_preserves_baseline(expression, baseline: str) -> bool:
    if isinstance(expression, exp.Literal) and expression.is_string:
        return baseline in expression.this
    if _is_charter_column(expression):
        return True
    if isinstance(expression, exp.Replace):
        old = expression.expression
        return bool(
            _is_charter_column(expression.this)
            and (isinstance(old, exp.Literal) or _is_charter_column(old))
            and isinstance(expression.args.get("replacement"), exp.Literal)
        )
    if isinstance(expression, exp.DPipe):
        return _charter_concat_preserves_baseline(expression)
    return False


def _charter_concat_preserves_baseline(expression) -> bool:
    if _is_charter_column(expression):
        return True
    if isinstance(expression, exp.Literal):
        return True
    return bool(
        isinstance(expression, exp.DPipe)
        and _charter_concat_preserves_baseline(expression.this)
        and _charter_concat_preserves_baseline(expression.expression)
        and any(_is_charter_column(node) for node in expression.walk())
    )


def _insert_replaces_charter(statement: exp.Insert, baseline: str) -> bool:
    if _table_name(statement.this) != AGENT_CONFIG_TABLE:
        return False
    if not isinstance(statement.this, exp.Schema):
        return bool(
            str(statement.args.get("alternative") or "").casefold() == "replace"
            or statement.args.get("conflict")
        )
    conflict = statement.args.get("conflict")
    replaces_on_conflict = bool(
        isinstance(conflict, exp.OnConflict)
        and any(
            isinstance(assignment, exp.EQ)
            and _is_charter_column(assignment.this)
            and not _charter_expression_preserves_baseline(assignment.expression, baseline)
            for assignment in conflict.expressions
        )
    )
    columns = [column.name.casefold() for column in statement.this.expressions]
    if "charter" not in columns:
        return replaces_on_conflict
    charter_index = columns.index("charter")
    values = statement.expression
    if not isinstance(values, exp.Values):
        return True
    replaces_inserted_charter = any(
        charter_index >= len(row.expressions)
        or not _charter_expression_preserves_baseline(row.expressions[charter_index], baseline)
        for row in values.expressions
        if isinstance(row, exp.Tuple)
    )
    return replaces_inserted_charter or replaces_on_conflict


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
    *,
    can_update_config: Optional[bool] = None,
) -> AgentConfigApplyResult:
    """Apply any SQLite config updates to the persistent agent record."""
    updated_fields: list[str] = []
    errors: list[str] = []
    current = _read_agent_config_snapshot()

    if baseline is None or current is None:
        _drop_agent_config_table()
        return AgentConfigApplyResult(updated_fields=updated_fields, errors=errors)

    charter = _normalize_charter(current.charter)
    charter_changed = charter != _normalize_charter(baseline.charter)
    schedule_changed = _normalize_schedule(current.schedule) != _normalize_schedule(baseline.schedule)
    if (charter_changed or schedule_changed) and can_update_config is False:
        _drop_agent_config_table()
        return AgentConfigApplyResult(
            updated_fields=(),
            errors=(
                "Configuration update denied: the active requester cannot change this agent's charter or schedule. "
                "Reply concisely without inferring owner status or exposing configuration.",
            ),
        )
    with transaction.atomic():
        if schedule_changed:
            result = execute_update_schedule(agent, {"new_schedule": current.schedule})
            if isinstance(result, dict) and result.get("status") == "ok":
                updated_fields.append("schedule")
            else:
                errors.append(result.get("message", "Schedule update failed.") if isinstance(result, dict) else "Schedule update failed.")

        if charter_changed and not errors:
            result = execute_update_charter(agent, {"new_charter": charter})
            if isinstance(result, dict) and result.get("status") == "ok":
                updated_fields.append("charter")
            else:
                errors.append(result.get("message", "Charter update failed.") if isinstance(result, dict) else "Charter update failed.")

        if errors:
            transaction.set_rollback(True)
            updated_fields.clear()

    if errors:
        agent.refresh_from_db(fields=["charter", "schedule"])

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
