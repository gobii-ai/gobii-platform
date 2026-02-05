"""
Shared helpers for executing SQLite SELECT queries within agent tools.

Centralizes guarded connection handling so multiple tools (e.g., charts,
CSV exports) can reuse the same safe execution path without duplicating
timer/cleanup logic.
"""

import logging
from typing import Any, List, Optional, Tuple

from .sqlite_state import _sqlite_db_path_var  # type: ignore
from .sqlite_guardrails import (
    clear_guarded_connection,
    open_guarded_sqlite_connection,
    start_query_timer,
    stop_query_timer,
)

logger = logging.getLogger(__name__)


def run_sqlite_select(
    query: str,
) -> Tuple[List[dict[str, Any]], Optional[list[str]], Optional[str]]:
    """Execute a SELECT query and return (rows, columns, error_message).

    Errors are returned as strings instead of raising to keep callers simple.
    """

    if not isinstance(query, str) or not query.strip():
        return [], None, "Query must be a SELECT statement that returns rows"
    upper = query.strip().upper()
    if not (upper.startswith("SELECT") or upper.startswith("WITH")):
        return [], None, "Query must be a SELECT statement that returns rows"

    db_path = _sqlite_db_path_var.get(None)
    if not db_path:
        return [], None, "SQLite database not available - ensure an agent session is active"

    conn = None
    try:
        conn = open_guarded_sqlite_connection(db_path)
        cursor = conn.cursor()
        start_query_timer(conn)
        cursor.execute(query)

        if cursor.description is None:
            return [], None, "Query must be a SELECT statement that returns rows"

        columns = [col[0] for col in cursor.description]
        rows = cursor.fetchall()
        conn.commit()

        data = [dict(zip(columns, row)) for row in rows]
        return data, columns, None
    except Exception as exc:  # noqa: BLE001 - propagate as string
        return [], None, f"Query failed: {exc}"
    finally:
        if conn is not None:
            stop_query_timer(conn)
            try:
                clear_guarded_connection(conn)
                conn.close()
            except Exception:  # noqa: BLE001
                logger.debug("Failed to close SQLite connection", exc_info=True)
