"""
SQLite batch tool for persistent agents.

Simplified multi-query executor aligned with sqlite_query.
"""

import logging
import os
import sqlite3
from typing import Any, Dict, List, Optional

import sqlparse
from sqlparse import tokens as sql_tokens
from sqlparse.sql import Statement

from ...models import PersistentAgent
from .sqlite_guardrails import (
    clear_guarded_connection,
    get_blocked_statement_reason,
    open_guarded_sqlite_connection,
    start_query_timer,
    stop_query_timer,
)
from .sqlite_helpers import is_write_statement
from .sqlite_state import _sqlite_db_path_var  # type: ignore

logger = logging.getLogger(__name__)


def _get_db_size_mb(db_path: str) -> float:
    try:
        if os.path.exists(db_path):
            return os.path.getsize(db_path) / (1024 * 1024)
    except Exception:
        pass
    return 0.0


def _get_error_hint(error_msg: str) -> str:
    """Return a helpful hint for common SQLite errors."""
    error_lower = error_msg.lower()
    if "union" in error_lower and "column" in error_lower:
        return " FIX: All SELECTs in UNION/UNION ALL must have the same number of columns."
    if "no column named" in error_lower or "no such column" in error_lower:
        return " FIX: Check column name spelling matches your table schema."
    if "no such table" in error_lower:
        return " FIX: Create the table first with CREATE TABLE before querying it."
    if "syntax error" in error_lower:
        return " FIX: Check SQL syntax - common issues: missing quotes, commas, or parentheses."
    if "unique constraint" in error_lower:
        return " FIX: Use INSERT OR REPLACE or INSERT OR IGNORE to handle duplicate keys."
    return ""


def _clean_statement(statement: str) -> Optional[str]:
    trimmed = statement.strip()
    if not trimmed:
        return None
    while trimmed.endswith(";"):
        trimmed = trimmed[:-1].rstrip()
    return trimmed or None


def _statement_has_sql(statement: Statement) -> bool:
    for token in statement.flatten():
        if token.is_whitespace:
            continue
        if token.ttype in sql_tokens.Comment:
            continue
        if token.ttype in sql_tokens.Punctuation and token.value == ";":
            continue
        return True
    return False


def _split_sqlite_statements(sql: str) -> List[str]:
    """Split SQL into statements using sqlparse."""
    statements: List[str] = []
    for statement in sqlparse.parse(sql):
        if not _statement_has_sql(statement):
            continue
        cleaned = _clean_statement(str(statement))
        if cleaned:
            statements.append(cleaned)

    return statements


def _normalize_queries(params: Dict[str, Any]) -> Optional[List[str]]:
    """Return a list of SQL strings from the single 'queries' parameter."""
    if "queries" not in params:
        return None

    raw = params.get("queries")
    if isinstance(raw, str):
        items = [raw]
    elif isinstance(raw, list):
        items = raw
    else:
        return None

    queries: List[str] = []
    for item in items:
        if not isinstance(item, str):
            return None
        split_items = _split_sqlite_statements(item)
        if split_items:
            queries.extend(split_items)

    return queries if queries else None


def execute_sqlite_batch(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    """Execute one or more SQL queries against the agent's SQLite DB."""
    queries = _normalize_queries(params)
    if not queries:
        return {
            "status": "error",
            "message": "Provide 'queries' as a SQL string or an array of SQL strings.",
        }

    will_continue_work_raw = params.get("will_continue_work", None)
    if will_continue_work_raw is None:
        will_continue_work = None
    elif isinstance(will_continue_work_raw, bool):
        will_continue_work = will_continue_work_raw
    elif isinstance(will_continue_work_raw, str):
        will_continue_work = will_continue_work_raw.lower() == "true"
    else:
        will_continue_work = None

    db_path = _sqlite_db_path_var.get(None)
    if not db_path:
        return {"status": "error", "message": "SQLite DB path unavailable"}

    conn: Optional[sqlite3.Connection] = None
    results: List[Dict[str, Any]] = []
    had_error = False
    error_message = ""
    only_write_queries = True

    try:
        conn = open_guarded_sqlite_connection(db_path)
        cur = conn.cursor()
        try:
            cur.execute("PRAGMA busy_timeout = 2000;")
        except Exception:
            pass

        preview = [q.strip()[:160] for q in queries[:5]]
        logger.info("Agent %s executing sqlite_batch: %s queries (preview=%s)", agent.id, len(queries), preview)

        for idx, query in enumerate(queries):
            if not isinstance(query, str) or not query.strip():
                had_error = True
                error_message = f"Query {idx} is empty or invalid."
                break
            block_reason = get_blocked_statement_reason(query)
            if block_reason:
                had_error = True
                error_message = f"Query {idx} blocked: {block_reason}"
                break

            only_write_queries = only_write_queries and is_write_statement(query)
            try:
                start_query_timer(conn)
                cur.execute(query)
                if cur.description is not None:
                    columns = [col[0] for col in cur.description]
                    rows = [dict(zip(columns, row)) for row in cur.fetchall()]
                    results.append({
                        "result": rows,
                        "message": f"Query {idx} returned {len(rows)} rows.",
                    })
                    only_write_queries = False
                else:
                    affected = cur.rowcount if cur.rowcount is not None else 0
                    msg = f"Query {idx} affected {max(0, affected)} rows."
                    # CTE-based INSERTs often report 0 rows affected even when data is inserted
                    query_upper = query.upper()
                    if affected <= 0 and "WITH" in query_upper and "INSERT" in query_upper:
                        msg += " (Normal for CTE INSERT - check sqlite_schema for actual row count)"
                    results.append({
                        "message": msg,
                    })
                conn.commit()
            except Exception as exc:
                conn.rollback()
                had_error = True
                hint = _get_error_hint(str(exc))
                error_message = f"Query {idx} failed: {exc}{hint}"
                break
            finally:
                stop_query_timer(conn)

        db_size_mb = _get_db_size_mb(db_path)
        size_warning = ""
        if db_size_mb > 50:
            size_warning = " WARNING: DB SIZE EXCEEDS 50MB. YOU MUST EXECUTE MORE QUERIES TO SHRINK THE SIZE, OR THE WHOLE DB WILL BE WIPED!!!"

        response: Dict[str, Any] = {
            "status": "error" if had_error else "ok",
            "results": results,
            "db_size_mb": round(db_size_mb, 2),
            "message": error_message if had_error else f"Executed {len(results)} queries. Database size: {db_size_mb:.2f} MB.{size_warning}",
        }

        if not had_error and will_continue_work is False:
            response["auto_sleep_ok"] = True

        return response
    except Exception as outer:
        return {"status": "error", "message": f"SQLite batch failed: {outer}"}
    finally:
        if conn is not None:
            try:
                clear_guarded_connection(conn)
                conn.close()
            except Exception:
                pass


def get_sqlite_batch_tool() -> Dict[str, Any]:
    """Return the sqlite_batch tool definition for the LLM."""
    return {
        "type": "function",
        "function": {
            "name": "sqlite_batch",
            "description": (
                "Durable SQLite memory for structured data. "
                "Provide 'queries' as a SQL string or an array of SQL strings to run sequentially; "
                "multiple statements in one string are split using sqlparse. "
                "REMEMBER TO PROPERLY ESCAPE STRINGS IN SQL STATEMENTS. "
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "queries": {
                        "anyOf": [
                            {"type": "string"},
                            {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        ],
                        "description": "SQL to execute (string for one statement, or array for multiple). You are responsible for managing schema and selective retrieval.",
                    },
                    "will_continue_work": {
                        "type": "boolean",
                        "description": "Set false when no immediate follow-up work is needed; enables auto-sleep.",
                    },
                },
                "required": ["queries"],
            },
        },
    }
