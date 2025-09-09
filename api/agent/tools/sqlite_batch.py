"""
SQLite batch tool for persistent agents.

Executes multiple SQL operations efficiently with transactional control,
per-operation results, and resilient error handling.
"""

import json
import logging
import os
import re
import sqlite3
import time
from typing import Any, Dict, List, Optional

from ...models import PersistentAgent

# Reuse the context variable set by agent_sqlite_db
from .sqlite_query import _sqlite_db_path_var  # type: ignore

logger = logging.getLogger(__name__)


def _classify_sqlite_error(exc: Exception) -> str:
    msg = str(exc).lower()
    if isinstance(exc, sqlite3.IntegrityError):
        return "constraint_violation"
    if isinstance(exc, sqlite3.OperationalError):
        if "syntax" in msg:
            return "syntax_error"
        if "locked" in msg or "busy" in msg:
            return "busy_timeout"
    return "unknown"


def _get_db_size_mb(db_path: str) -> float:
    try:
        if os.path.exists(db_path):
            return os.path.getsize(db_path) / (1024 * 1024)
    except Exception:
        pass
    return 0.0


def execute_sqlite_batch(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a batch of SQL operations against the agent's SQLite DB.

    Expected params:
        - operations: list[str] (SQL statements executed in order)
        - mode: 'atomic' | 'per_statement' (default 'atomic')
    """

    ops = params.get("operations")
    if not isinstance(ops, list) or not ops or not all(isinstance(s, str) for s in ops):
        return {"status": "error", "message": "'operations' must be a non-empty array of SQL strings."}

    mode = params.get("mode", "atomic")
    if mode not in ("atomic", "per_statement"):
        return {"status": "error", "message": f"Invalid mode '{mode}'. Must be 'atomic' or 'per_statement'."}

    # Fixed defaults to keep API simple
    row_limit = 1000
    busy_timeout_ms = 2000

    db_path = _sqlite_db_path_var.get(None)
    if not db_path:
        return {"status": "error", "message": "SQLite DB path unavailable"}

    results: List[Dict[str, Any]] = []
    total_changes = 0
    any_truncated = False
    succeeded = 0
    failed = 0
    warnings: List[str] = []

    # Log a preview of the batch for observability (truncate SQL text)
    try:
        preview_ops = [
            (sql.strip()[:160] + ("..." if len(sql.strip()) > 160 else ""))
            for sql in ops[:10]
        ]
        logger.info(
            "Agent %s executing sqlite_batch: %s ops, mode=%s, preview=%s",
            agent.id, len(ops), mode, json.dumps(preview_ops)
        )
    except Exception:
        logger.info("Agent %s executing sqlite_batch: %s ops, mode=%s", agent.id, len(ops), mode)

    # Helper: best-effort SQL sanitation to mitigate common errors without attempting a full SQL parse
    def _sanitize_sql(sql: str) -> str:
        s = sql
        # Normalise typographic quotes
        s = s.replace("“", '"').replace("”", '"')
        # Replace fancy apostrophes with doubled single quotes for SQL string literals
        s = s.replace("’", "''")
        # Convert backslash-escaped single quotes to standard doubled quotes for SQLite
        s = s.replace("\\'", "''")

        return s

    def _is_transaction_control(sql: str) -> bool:
        return bool(re.match(r"^\s*(BEGIN|COMMIT|ROLLBACK)\b", sql, re.IGNORECASE))

    def _has_multiple_statements(sql: str) -> bool:
        """Heuristic: flag multiple statements if there is a semicolon outside quotes."""
        in_single = False
        in_double = False
        for ch in sql:
            if ch == "'" and not in_double:
                in_single = not in_single
            elif ch == '"' and not in_single:
                in_double = not in_double
            elif ch == ";" and not in_single and not in_double:
                return True
        return False

    # connect with busy timeout (seconds)
    conn: Optional[sqlite3.Connection] = None
    try:
        conn = sqlite3.connect(db_path, timeout=max(0.001, busy_timeout_ms / 1000.0))
        cur = conn.cursor()
        try:
            cur.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)};")
        except Exception:
            pass

        error_occurred = False

        def begin_if_needed_for_mode():
            if mode == "atomic":
                cur.execute("BEGIN IMMEDIATE;")

        def commit_if_needed_for_mode():
            if mode == "atomic":
                conn.commit()

        def rollback_all_if_needed():
            if mode == "atomic":
                try:
                    conn.rollback()
                except Exception:
                    pass

        begin_if_needed_for_mode()

        for idx, sql in enumerate(ops):
            if not isinstance(sql, str) or not sql.strip():
                results.append({
                    "ok": False,
                    "error": {"code": "invalid_input", "message": "Operation must be a non-empty SQL string", "at_index": idx},
                })
                failed += 1
                if mode == "atomic":
                    error_occurred = True
                    break
                else:
                    continue

            t0 = time.monotonic()
            try:
                # Preflight checks and best-effort sanitisation
                sql_sanitized = _sanitize_sql(sql)
                if _is_transaction_control(sql_sanitized):
                    results.append({
                        "ok": False,
                        "error": {
                            "code": "transaction_control_disallowed",
                            "message": "Remove explicit BEGIN/COMMIT/ROLLBACK. The tool manages transactions automatically in atomic mode.",
                            "at_sql": sql,
                            "at_index": idx,
                        },
                    })
                    failed += 1
                    if mode == "atomic":
                        rollback_all_if_needed()
                        error_occurred = True
                        # Mark remaining ops as skipped due to rollback
                        for j in range(idx + 1, len(ops)):
                            results.append({
                                "ok": False,
                                "error": {"code": "transaction_control_disallowed", "message": "Batch rolled back due to prior error", "at_index": j},
                            })
                            failed += 1
                        break
                    else:
                        continue

                if _has_multiple_statements(sql_sanitized):
                    results.append({
                        "ok": False,
                        "error": {
                            "code": "multiple_statements",
                            "message": "Provide exactly one SQL statement per operation. Split statements into separate items in the operations array.",
                            "at_sql": sql,
                            "at_index": idx,
                        },
                    })
                    failed += 1
                    if mode == "atomic":
                        rollback_all_if_needed()
                        error_occurred = True
                        for j in range(idx + 1, len(ops)):
                            results.append({
                                "ok": False,
                                "error": {"code": "multiple_statements", "message": "Batch rolled back due to prior error", "at_index": j},
                            })
                            failed += 1
                        break
                    else:
                        continue

                cur.execute(sql_sanitized)
                # SELECT-like: cursor.description present
                if cur.description is not None:
                    columns = [c[0] for c in cur.description]
                    fetched = cur.fetchmany(row_limit)
                    rows = [dict(zip(columns, r)) for r in fetched]
                    # Detect truncation heuristically: try to fetch one more row
                    extra = cur.fetchmany(1)
                    truncated = len(extra) > 0
                    any_truncated = any_truncated or truncated
                    res = {
                        "ok": True,
                        "rows": rows,
                        "schema": columns,
                        "changes": 0,
                        "last_insert_rowid": None,
                    }
                    results.append(res)
                    succeeded += 1
                else:
                    # for inserts/updates/deletes
                    affected = cur.rowcount if cur.rowcount is not None else 0
                    # Best-effort last insert id for INSERT statements
                    last_id = None
                    try:
                        if sql.lstrip().upper().startswith("INSERT"):
                            last_id = cur.lastrowid or cur.execute("SELECT last_insert_rowid();").fetchone()[0]
                    except Exception:
                        last_id = cur.lastrowid
                    total_changes += max(0, affected)
                    res = {
                        "ok": True,
                        "rows": [],
                        "schema": [],
                        "changes": max(0, affected),
                        "last_insert_rowid": last_id,
                    }
                    results.append(res)
                    succeeded += 1

                # Per-statement commit behavior
                if mode == "per_statement":
                    conn.commit()
            except Exception as e:
                code = _classify_sqlite_error(e)
                elapsed = int((time.monotonic() - t0) * 1000)
                results.append({
                    "ok": False,
                    "error": {"code": code, "message": f"{e}", "at_sql": sql, "at_index": idx},
                    "time_ms": elapsed,
                })
                failed += 1

                # Rollback strategy
                if mode == "atomic":
                    rollback_all_if_needed()
                    error_occurred = True
                    # Mark remaining ops as skipped due to rollback
                    for j in range(idx + 1, len(ops)):
                        results.append({
                            "ok": False,
                            "error": {"code": code, "message": "Batch rolled back due to prior error", "at_index": j},
                        })
                        failed += 1
                    break
                else:
                    # Per-statement rollback and continue/stop
                    try:
                        conn.rollback()
                    except Exception as e:
                        logger.warning("Failed to rollback transaction in atomic mode: %s", e)
                    # Continue to next op in per_statement mode

        if not error_occurred and mode == "atomic":
            commit_if_needed_for_mode()

        db_size_mb = _get_db_size_mb(db_path)
        if db_size_mb > 50:
            warnings.append("WARNING: DB SIZE EXCEEDS 50MB. CONSIDER CLEANUP/VACUUM TO AVOID WIPE AT 100MB.")

        status = "ok" if failed == 0 else "error"
        return {
            "status": status,
            "results": results,
            "db_size_mb": round(db_size_mb, 2),
            "warnings": warnings,
        }
    except Exception as outer:
        return {"status": "error", "message": f"SQLite batch failed: {outer}"}
    finally:
        try:
            if conn is not None:
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
                "Execute multiple SQLite operations in one call. Use this whenever you have two or more SQL statements. "
                "Rules: (1) Provide exactly ONE SQL statement per entry in 'operations' (no semicolon-separated bundles). "
                "(2) Do NOT include BEGIN/COMMIT/ROLLBACK; the tool manages transactions for mode=atomic. "
                "(3) Escape single quotes inside values by doubling them (e.g., 'What''s new'). Avoid backslash escaping. "
                "(4) Prefer 'INSERT OR IGNORE' or 'INSERT ... ON CONFLICT(col) DO UPDATE ...' to avoid UNIQUE violations. "
                "Choose mode=atomic for dependent ops (all-or-nothing) or per_statement to continue past individual errors."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "operations": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "description": "List of SQL statements to execute in order.",
                    },
                    "mode": {"type": "string", "enum": ["atomic", "per_statement"], "default": "atomic"},
                },
                "required": ["operations"],
            },
        },
    }
