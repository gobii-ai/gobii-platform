"""
SQLite batch tool for persistent agents.

Executes multiple SQL operations efficiently with transactional control,
per-operation results, and resilient error handling.
"""

import json
import logging
import os
import sqlite3
import time
from typing import Any, Dict, List, Optional

from ...models import PersistentAgent

# Reuse the context variable set by agent_sqlite_db
from .sqlite_state import _sqlite_db_path_var  # type: ignore
from .sqlite_helpers import is_write_statement

logger = logging.getLogger(__name__)


def _sanitize_sql(sql: str) -> str:
    """Normalise quote variants and escape obvious literal apostrophes."""

    if not sql:
        return ""

    length = len(sql)
    i = 0
    in_single_literal = False
    out: List[str] = []

    while i < length:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < length else ""

        # Normalise typographic double quotes
        if ch in ("“", "”"):
            out.append('"')
            i += 1
            continue

        # Treat backslash-escaped quotes as doubled quotes (SQLite style)
        if ch == "\\" and nxt in ("'", "’"):
            out.append("''")
            i += 2
            continue

        # Handle curly single quotes/apostrophes
        if ch in ("‘", "’"):
            prev_char = sql[i - 1] if i > 0 else ""
            next_char = sql[i + 1] if i + 1 < length else ""
            if not in_single_literal:
                out.append("'")
                in_single_literal = True
            else:
                if prev_char.isalnum() and next_char.isalnum():
                    # Apostrophe inside literal -> escape by doubling
                    out.append("''")
                else:
                    out.append("'")
                    in_single_literal = False
            i += 1
            continue

        if ch == "'":
            out.append("'")
            if in_single_literal and nxt == "'":
                out.append("'")
                i += 2
                continue
            in_single_literal = not in_single_literal
            i += 1
            continue

        out.append(ch)
        i += 1

    return "".join(out)


def _is_transaction_control(sql: str) -> bool:
    if not sql:
        return False

    stripped = sql.lstrip()
    upper = stripped.upper()
    for keyword in ("BEGIN", "COMMIT", "ROLLBACK"):
        if upper.startswith(keyword):
            next_idx = len(keyword)
            if len(upper) == next_idx or not upper[next_idx].isalpha():
                return True
    return False


def _split_sql_statements(sql: str) -> List[str]:
    """Split SQL on top-level semicolons while respecting comments/literals."""

    if not sql or not sql.strip():
        return []

    statements: List[str] = []
    buf: List[str] = []
    in_single = False
    in_double = False
    in_line_comment = False
    in_block_comment = False
    token_buf: List[str] = []
    expect_trigger = False
    inside_trigger = False
    trigger_block_depth = 0
    length = len(sql)
    i = 0

    def flush_token() -> None:
        nonlocal token_buf, expect_trigger, inside_trigger, trigger_block_depth
        if not token_buf or in_line_comment or in_block_comment or in_single or in_double:
            token_buf = []
            return
        word = "".join(token_buf)
        token_buf = []
        upper = word.upper()
        if expect_trigger:
            if upper in {"OR", "TEMP", "TEMPORARY", "IF", "NOT", "EXISTS"}:
                return
            if upper == "TRIGGER":
                inside_trigger = True
                expect_trigger = False
                return
            expect_trigger = False
        if upper == "CREATE":
            expect_trigger = True
        if inside_trigger:
            if upper == "BEGIN":
                trigger_block_depth += 1
            elif upper == "END":
                if trigger_block_depth > 0:
                    trigger_block_depth -= 1
                if trigger_block_depth == 0:
                    inside_trigger = False

    while i < length:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < length else ""

        if in_line_comment:
            buf.append(ch)
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue

        if in_block_comment:
            buf.append(ch)
            if ch == "*" and nxt == "/":
                buf.append(nxt)
                i += 2
                in_block_comment = False
            else:
                i += 1
            continue

        if not in_single and not in_double:
            if ch == "-" and nxt == "-":
                buf.append(ch)
                buf.append(nxt)
                in_line_comment = True
                i += 2
                continue
            if ch == "/" and nxt == "*":
                buf.append(ch)
                buf.append(nxt)
                in_block_comment = True
                i += 2
                continue

        if ch == "'" and not in_double:
            buf.append(ch)
            if in_single and nxt == "'":
                buf.append(nxt)
                i += 2
                continue
            in_single = not in_single
            i += 1
            continue

        if ch == '"' and not in_single:
            buf.append(ch)
            if in_double and nxt == '"':
                buf.append(nxt)
                i += 2
                continue
            in_double = not in_double
            i += 1
            continue

        if not in_single and not in_double:
            if ch == ";" and trigger_block_depth == 0:
                flush_token()
                statement = "".join(buf).strip()
                if statement:
                    statements.append(statement)
                buf = []
                i += 1
                continue
            if ch.isalpha() or ch == "_":
                token_buf.append(ch)
            else:
                flush_token()

        buf.append(ch)
        i += 1

    flush_token()
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)

    return statements


def _has_multiple_statements(sql: str) -> bool:
    statements = _split_sql_statements(sql)
    return len(statements) > 1

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

def _normalize_operations(value: Any) -> Optional[List[str]]:
    """Best-effort coercion of operations into a list of SQL strings."""
    if isinstance(value, list) and value:
        if all(isinstance(item, str) for item in value):
            return value  # already in correct form
        # Support list of dicts with "sql" keys, common in structured tool outputs
        sqlified = []
        for item in value:
            if isinstance(item, dict) and "sql" in item and isinstance(item["sql"], str):
                sqlified.append(item["sql"])
            else:
                return None
        return sqlified if sqlified else None

    if isinstance(value, str) and value.strip():
        # Attempt to parse JSON arrays encoded as strings
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            # Treat as single statement string
            return [value]

        return _normalize_operations(parsed)

    return None

DEFAULT_SELECT_ROW_LIMIT = 200
MAX_SELECT_ROW_LIMIT = 1000


def execute_sqlite_batch(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a batch of SQL operations against the agent's SQLite DB.

    Expected params:
        - operations: list[str] (SQL statements executed in order)
        - mode: 'atomic' | 'per_statement' (default 'per_statement')
    """

    ops_raw = params.get("operations")
    ops: Optional[List[str]] = None

    ops = _normalize_operations(ops_raw)

    if not ops or not all(isinstance(s, str) and s.strip() for s in ops):
        logger.warning(
            "sqlite_batch received invalid operations payload: %s",
            json.dumps(ops_raw)[:400] if isinstance(ops_raw, (str, list, dict)) else str(type(ops_raw)),
        )
        return {"status": "error", "message": "'operations' must be a non-empty array of SQL strings."}

    mode = params.get("mode", "per_statement")
    if mode not in ("atomic", "per_statement"):
        return {"status": "error", "message": f"Invalid mode '{mode}'. Must be 'atomic' or 'per_statement'."}

    # Fixed defaults to keep API simple
    provided_row_limit = params.get("row_limit")
    if provided_row_limit is None:
        row_limit = DEFAULT_SELECT_ROW_LIMIT
    else:
        try:
            row_limit = int(provided_row_limit)
            if not (1 <= row_limit <= MAX_SELECT_ROW_LIMIT):
                raise ValueError
        except (TypeError, ValueError):
            return {
                "status": "error",
                "message": f"'row_limit' must be an integer between 1 and {MAX_SELECT_ROW_LIMIT}.",
            }

    busy_timeout_ms = 2000

    db_path = _sqlite_db_path_var.get(None)
    if not db_path:
        return {"status": "error", "message": "SQLite DB path unavailable"}

    # Expand operations so each entry contains at most one SQL statement
    expanded_ops: List[str] = []
    expanded_display_ops: List[str] = []
    expanded_original_indexes: List[int] = []
    split_warnings: List[str] = []
    for original_index, raw_sql in enumerate(ops):
        sanitized = _sanitize_sql(raw_sql)
        statements = _split_sql_statements(sanitized)
        if not statements:
            # Force downstream validation to flag empty statements explicitly
            statements = [""]
        if len(statements) > 1:
            split_warnings.append(
                f"Operation {original_index} contained multiple statements. Auto-split into {len(statements)} entries; send separate operations to save credits."
            )
        for stmt in statements:
            expanded_ops.append(stmt)
            if len(statements) == 1:
                expanded_display_ops.append(raw_sql)
            else:
                expanded_display_ops.append(stmt)
            expanded_original_indexes.append(original_index)

    ops = expanded_ops
    op_display_strings = expanded_display_ops
    op_original_indexes = expanded_original_indexes

    results: List[Dict[str, Any]] = []
    total_changes = 0
    any_truncated = False
    succeeded = 0
    failed = 0
    warnings: List[str] = []
    only_write_ops = True

    # Log a preview of the batch for observability (truncate SQL text)
    try:
        preview_ops = [
            (sql.strip()[:160] + ("..." if len(sql.strip()) > 160 else ""))
            for sql in op_display_strings[:10]
        ]
        logger.info(
            "Agent %s executing sqlite_batch: %s ops, mode=%s, preview=%s",
            agent.id, len(ops), mode, json.dumps(preview_ops)
        )
    except Exception:
        logger.info("Agent %s executing sqlite_batch: %s ops, mode=%s", agent.id, len(ops), mode)

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

        # Helper to handle preflight validation errors consistently
        def _handle_preflight_error(code: str, message: str, at_sql: str, at_index: int, original_index: int) -> bool:
            nonlocal failed, error_occurred
            results.append({
                "ok": False,
                "error": {
                    "code": code,
                    "message": message,
                    "at_sql": at_sql,
                    "at_index": at_index,
                    "at_original_index": original_index,
                },
            })
            failed += 1
            if mode == "atomic":
                rollback_all_if_needed()
                error_occurred = True
                # Mark remaining ops as skipped due to rollback
                for j in range(at_index + 1, len(ops)):
                    results.append({
                        "ok": False,
                        "error": {
                            "code": code,
                            "message": "Batch rolled back due to prior error",
                            "at_index": j,
                            "at_original_index": op_original_indexes[j],
                        },
                    })
                    failed += 1
                return True  # signal to break the main loop
            return False  # signal to continue to next operation

        for idx, sql in enumerate(ops):
            display_sql = op_display_strings[idx]
            original_index = op_original_indexes[idx]
            if not isinstance(sql, str) or not sql.strip():
                results.append({
                    "ok": False,
                    "error": {
                        "code": "invalid_input",
                        "message": "Operation must be a non-empty SQL string",
                        "at_sql": display_sql,
                        "at_index": idx,
                        "at_original_index": original_index,
                    },
                })
                failed += 1
                only_write_ops = False
                if mode == "atomic":
                    error_occurred = True
                    break
                else:
                    continue

            t0 = time.monotonic()
            try:
                sql_sanitized = sql
                only_write_ops = only_write_ops and is_write_statement(sql_sanitized)
                if _is_transaction_control(sql_sanitized):
                    only_write_ops = False
                    should_break = _handle_preflight_error(
                        "transaction_control_disallowed",
                        "Remove explicit BEGIN/COMMIT/ROLLBACK. The tool manages transactions automatically in atomic mode.",
                        display_sql,
                        idx,
                        original_index,
                    )
                    if should_break:
                        break
                    else:
                        continue

                if _has_multiple_statements(sql_sanitized):
                    only_write_ops = False
                    should_break = _handle_preflight_error(
                        "multiple_statements",
                        "Provide exactly one SQL statement per operation. Split statements into separate items in the operations array.",
                        display_sql,
                        idx,
                        original_index,
                    )
                    if should_break:
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
                        "truncated_rows": truncated,
                    }
                    results.append(res)
                    succeeded += 1
                    only_write_ops = False
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
                    "error": {
                        "code": code,
                        "message": f"{e}",
                        "at_sql": display_sql,
                        "at_index": idx,
                        "at_original_index": original_index,
                    },
                    "time_ms": elapsed,
                })
                failed += 1
                only_write_ops = False

                # Rollback strategy
                if mode == "atomic":
                    rollback_all_if_needed()
                    error_occurred = True
                    # Mark remaining ops as skipped due to rollback
                    for j in range(idx + 1, len(ops)):
                        results.append({
                            "ok": False,
                            "error": {
                                "code": code,
                                "message": "Batch rolled back due to prior error",
                                "at_index": j,
                                "at_original_index": op_original_indexes[j],
                            },
                        })
                        failed += 1
                    break
                else:
                    # Per-statement rollback and continue/stop
                    try:
                        conn.rollback()
                    except Exception as exc:
                        logger.warning("Failed to rollback transaction in per_statement mode: %s", exc)
                    # Continue to next op in per_statement mode

        if not error_occurred and mode == "atomic":
            commit_if_needed_for_mode()

        db_size_mb = _get_db_size_mb(db_path)
        if db_size_mb > 50:
            warnings.append("WARNING: DB SIZE EXCEEDS 50MB. CONSIDER CLEANUP/VACUUM TO AVOID WIPE AT 100MB.")
        warnings.extend(split_warnings)

        status = "ok" if failed == 0 else "error"
        response = {
            "status": status,
            "results": results,
            "db_size_mb": round(db_size_mb, 2),
            "warnings": warnings,
            "truncated_rows": any_truncated,
            "row_limit": row_limit,
        }
        if status == "ok" and succeeded > 0 and only_write_ops:
            response["auto_sleep_ok"] = True
        return response
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
                "Durable SQLite memory for structured data. \n"
                "Rules:\n"
                "- Provide exactly one SQL statement per entry in 'operations'. We can auto-split semicolon chains, but it costs more tokens.\n"
                "- DO NOT INCLUDE BEGIN/COMMIT/ROLLBACK. Default mode is 'per_statement' so later statements can run; switch to 'atomic' when ops must succeed together.\n"
                "- Escape single quotes by doubling them (e.g., 'What''s new'). Keep tables tight and prune data so the DB stays under 50 MB.\n"
                f"- Each SELECT returns at most {DEFAULT_SELECT_ROW_LIMIT} rows unless you raise 'row_limit' (max {MAX_SELECT_ROW_LIMIT}). Page through large results yourself.\n"
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
                    "mode": {"type": "string", "enum": ["atomic", "per_statement"], "default": "per_statement"},
                    "row_limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_SELECT_ROW_LIMIT,
                        "description": (
                            f"Maximum rows to return per SELECT (default {DEFAULT_SELECT_ROW_LIMIT}). "
                            "Use sparingly and page results yourself."
                        ),
                    },
                },
                "required": ["operations"],
            },
        },
    }
