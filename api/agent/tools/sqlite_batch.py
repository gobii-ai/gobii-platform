"""
SQLite batch tool for persistent agents.

Simplified multi-query executor aligned with sqlite_query.
"""

import logging
import os
import sqlite3
from typing import Any, Dict, List, Optional

from ...models import PersistentAgent
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
        if not isinstance(item, str) or not item.strip():
            return None
        queries.append(item)

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
    if will_continue_work_raw is not None and not isinstance(will_continue_work_raw, bool):
        return {"status": "error", "message": "'will_continue_work' must be a boolean when provided."}
    will_continue_work = will_continue_work_raw

    db_path = _sqlite_db_path_var.get(None)
    if not db_path:
        return {"status": "error", "message": "SQLite DB path unavailable"}

    conn: Optional[sqlite3.Connection] = None
    results: List[Dict[str, Any]] = []
    had_error = False
    error_message = ""
    only_write_queries = True

    try:
        conn = sqlite3.connect(db_path)
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

            only_write_queries = only_write_queries and is_write_statement(query)
            try:
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
                    results.append({
                        "message": f"Query {idx} affected {max(0, affected)} rows.",
                    })
                conn.commit()
            except Exception as exc:
                conn.rollback()
                had_error = True
                error_message = f"Query {idx} failed: {exc}"
                break

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
                "Provide 'queries' as a SQL string or an array of SQL strings to run sequentially. "
                "REMEMBER TO PROPERLY ESCAPE STRINGS IN SQL STATEMENTS. "
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "queries": {
                        "type": ["string", "array"],
                        "items": {"type": "string"},
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
