"""SQLite guardrails for agent-managed databases."""

import logging
import math
import re
import sqlite3
import time
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Safe custom functions for text analysis (no I/O, pure computation)
# ---------------------------------------------------------------------------

def _regexp(pattern: str, string: Optional[str]) -> bool:
    """REGEXP function for pattern matching in queries."""
    if string is None or pattern is None:
        return False
    try:
        return bool(re.search(pattern, string))
    except re.error:
        return False


def _regexp_extract(string: Optional[str], pattern: str, group: int = 0) -> Optional[str]:
    """Extract first regex match from string.

    Usage: regexp_extract(column, 'pattern') or regexp_extract(column, '(group)', 1)
    """
    if string is None or pattern is None:
        return None
    try:
        match = re.search(pattern, string)
        return match.group(group) if match else None
    except (re.error, IndexError):
        return None


def _word_count(string: Optional[str]) -> int:
    """Count words in a string."""
    if not string:
        return 0
    return len(string.split())


def _char_count(string: Optional[str]) -> int:
    """Count characters in a string."""
    return len(string) if string else 0

_BLOCKED_ACTIONS = {
    sqlite3.SQLITE_ATTACH,
    sqlite3.SQLITE_DETACH,
}

_BLOCKED_FUNCTIONS = {
    "load_extension",
    "readfile",
    "writefile",
    "edit",
    "fts3_tokenizer",
}

_BLOCKED_PRAGMAS = {
    "database_list",
    "key",
    "rekey",
    "temp_store",
    "temp_store_directory",
}

_VACUUM_PATTERN = re.compile(
    r"^\s*(?:EXPLAIN\s+(?:QUERY\s+PLAN\s+)?)?VACUUM\b",
    re.IGNORECASE,
)


def _deny_action(action_code: int, param1: Optional[str], param2: Optional[str]) -> int:
    action_name = str(action_code)
    logger.warning(
        "Blocked SQLite action=%s param1=%s param2=%s",
        action_name,
        param1,
        param2,
    )
    return sqlite3.SQLITE_DENY


def _sqlite_authorizer(
    action_code: int,
    param1: Optional[str],
    param2: Optional[str],
    _db_name: Optional[str],
    _trigger_name: Optional[str],
) -> int:
    if action_code in _BLOCKED_ACTIONS:
        return _deny_action(action_code, param1, param2)

    if action_code == sqlite3.SQLITE_FUNCTION:
        func = (param2 or param1 or "").lower()
        if func in _BLOCKED_FUNCTIONS:
            return _deny_action(action_code, param1, param2)

    if action_code == sqlite3.SQLITE_PRAGMA:
        pragma = (param1 or "").lower()
        if pragma in _BLOCKED_PRAGMAS:
            return _deny_action(action_code, param1, param2)

    return sqlite3.SQLITE_OK


def _strip_comments_and_literals(sql: str) -> str:
    """Remove comments and quoted literals for safer keyword checks."""
    result: list[str] = []
    i = 0
    length = len(sql)

    while i < length:
        ch = sql[i]

        if ch == "-" and i + 1 < length and sql[i + 1] == "-":
            i += 2
            while i < length and sql[i] != "\n":
                i += 1
            continue

        if ch == "/" and i + 1 < length and sql[i + 1] == "*":
            i += 2
            while i + 1 < length and not (sql[i] == "*" and sql[i + 1] == "/"):
                i += 1
            i = i + 2 if i + 1 < length else length
            continue

        if ch in {"'", '"'}:
            quote = ch
            result.append(" ")
            i += 1
            while i < length:
                curr = sql[i]
                if curr == quote:
                    if i + 1 < length and sql[i + 1] == quote:
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue

        result.append(ch)
        i += 1

    return "".join(result)


def get_blocked_statement_reason(sql: str) -> Optional[str]:
    """Return a message if the statement should be blocked."""
    stripped = _strip_comments_and_literals(sql or "")
    if _VACUUM_PATTERN.match(stripped):
        return "VACUUM statements are disabled for safety."
    return None


_QUERY_STARTS: dict[int, float] = {}
_QUERY_TIMEOUTS: dict[int, float] = {}


def start_query_timer(conn: sqlite3.Connection) -> None:
    _QUERY_STARTS[id(conn)] = time.monotonic()


def stop_query_timer(conn: sqlite3.Connection) -> None:
    _QUERY_STARTS.pop(id(conn), None)


def clear_guarded_connection(conn: sqlite3.Connection) -> None:
    conn_id = id(conn)
    _QUERY_STARTS.pop(conn_id, None)
    _QUERY_TIMEOUTS.pop(conn_id, None)


def _make_progress_handler(conn_id: int):

    def handler() -> int:
        start = _QUERY_STARTS.get(conn_id)
        timeout = _QUERY_TIMEOUTS.get(conn_id)
        if start is None or timeout is None:
            return 0
        if time.monotonic() - start > timeout:
            return 1
        return 0

    return handler


def _register_safe_functions(conn: sqlite3.Connection) -> None:
    """Register safe custom functions for text analysis."""
    conn.create_function("REGEXP", 2, _regexp)
    conn.create_function("regexp_extract", 2, _regexp_extract)
    conn.create_function("regexp_extract", 3, _regexp_extract)  # With group arg
    conn.create_function("word_count", 1, _word_count)
    conn.create_function("char_count", 1, _char_count)


def open_guarded_sqlite_connection(
    db_path: str,
    *,
    timeout_seconds: float = 30.0,
) -> sqlite3.Connection:
    """Open a SQLite connection with guardrails against host file access."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA temp_store = MEMORY;")
    except Exception:
        logger.debug("Failed to set SQLite temp_store=MEMORY", exc_info=True)
    try:
        conn.enable_load_extension(False)
    except Exception:
        logger.debug("Failed to disable SQLite load_extension", exc_info=True)
    # Register safe analysis functions
    _register_safe_functions(conn)
    if hasattr(conn, "setlimit") and hasattr(sqlite3, "SQLITE_LIMIT_ATTACHED"):
        try:
            conn.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
        except Exception:
            logger.debug("Failed to set SQLite attached DB limit", exc_info=True)
    try:
        conn.set_authorizer(_sqlite_authorizer)
    except Exception as exc:
        conn.close()
        raise RuntimeError("Failed to enable SQLite guardrails") from exc
    conn_id = id(conn)
    _QUERY_TIMEOUTS[conn_id] = timeout_seconds
    conn.set_progress_handler(_make_progress_handler(conn_id), 10000)
    return conn
