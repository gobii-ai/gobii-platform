"""
SQLite batch tool for persistent agents.

Simplified multi-query executor aligned with sqlite_query.
"""

import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from collections import deque
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import sqlparse

# Context protection limits
MAX_RESULT_ROWS = 100  # Hard cap on rows returned
MAX_RESULT_BYTES = 8000  # ~2K tokens worth of result data
WARN_RESULT_ROWS = 50  # Warn if exceeding this
MAX_AUTO_CORRECTION_ATTEMPTS = 8
MAX_AUTO_CORRECTION_CANDIDATES = 20
from sqlparse import tokens as sql_tokens
from sqlparse.sql import Statement

if TYPE_CHECKING:
    from ...models import PersistentAgent
from .sqlite_guardrails import (
    clear_guarded_connection,
    get_blocked_statement_reason,
    open_guarded_sqlite_connection,
    start_query_timer,
    stop_query_timer,
)
from .sqlite_autocorrect import build_cte_column_candidates, build_sqlglot_candidates
from .sqlite_helpers import is_write_statement
from .sqlite_state import _sqlite_db_path_var  # type: ignore

logger = logging.getLogger(__name__)

try:
    import resource
except ImportError:  # pragma: no cover - not available on all platforms
    resource = None

DEFAULT_SQLITE_BATCH_WALL_TIMEOUT_SECONDS = 30.0
DEFAULT_SQLITE_BATCH_CPU_SECONDS = 30
DEFAULT_SQLITE_BATCH_MEMORY_MB = 256
DEFAULT_SQLITE_BATCH_TERMINATE_GRACE_SECONDS = 1.0
DEFAULT_SQLITE_BATCH_KILL_GRACE_SECONDS = 1.0


@dataclass(frozen=True)
class _SqliteBatchLimits:
    wall_timeout_seconds: float
    cpu_seconds: int
    memory_mb: int
    query_timeout_seconds: float


def _get_setting_value(name: str) -> Any:
    try:
        from django.conf import settings
    except Exception:
        settings = None

    if settings is not None and hasattr(settings, name):
        value = getattr(settings, name)
        if value is not None and value != "":
            return value

    env_value = os.getenv(name)
    if env_value is not None and env_value != "":
        return env_value

    return None


def _coerce_float(value: Any, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_int(value: Any, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_str(value: Any, default: str) -> str:
    if value is None:
        return default
    try:
        return str(value)
    except Exception:
        return default


def _resolve_sqlite_batch_limits() -> _SqliteBatchLimits:
    wall_timeout = _coerce_float(
        _get_setting_value("SQLITE_BATCH_WALL_TIMEOUT_SECONDS"),
        DEFAULT_SQLITE_BATCH_WALL_TIMEOUT_SECONDS,
    )
    wall_timeout = max(0.0, wall_timeout)
    query_timeout = _coerce_float(
        _get_setting_value("SQLITE_BATCH_QUERY_TIMEOUT_SECONDS"),
        wall_timeout,
    )
    query_timeout = max(0.0, query_timeout)
    cpu_seconds = _coerce_int(
        _get_setting_value("SQLITE_BATCH_CPU_SECONDS"),
        DEFAULT_SQLITE_BATCH_CPU_SECONDS,
    )
    cpu_seconds = max(0, cpu_seconds)
    memory_mb = _coerce_int(
        _get_setting_value("SQLITE_BATCH_MEMORY_MB"),
        DEFAULT_SQLITE_BATCH_MEMORY_MB,
    )
    memory_mb = max(0, memory_mb)

    return _SqliteBatchLimits(
        wall_timeout_seconds=wall_timeout,
        cpu_seconds=cpu_seconds,
        memory_mb=memory_mb,
        query_timeout_seconds=min(query_timeout, wall_timeout) if wall_timeout > 0 else query_timeout,
    )


def _apply_resource_limits(limits: _SqliteBatchLimits) -> None:
    if resource is None:
        return

    if limits.cpu_seconds > 0:
        try:
            resource.setrlimit(resource.RLIMIT_CPU, (limits.cpu_seconds, limits.cpu_seconds))
        except Exception:
            logger.debug("Failed to set sqlite_batch CPU limit", exc_info=True)

    if limits.memory_mb > 0:
        memory_bytes = int(limits.memory_mb * 1024 * 1024)
        for limit_name in ("RLIMIT_AS", "RLIMIT_DATA"):
            if hasattr(resource, limit_name):
                try:
                    resource.setrlimit(getattr(resource, limit_name), (memory_bytes, memory_bytes))
                except Exception:
                    logger.debug(
                        "Failed to set sqlite_batch memory limit for %s",
                        limit_name,
                        exc_info=True,
                    )


def _get_db_size_mb(db_path: str) -> float:
    try:
        if os.path.exists(db_path):
            return os.path.getsize(db_path) / (1024 * 1024)
    except Exception:
        pass
    return 0.0


def _extract_cte_names(sql: str) -> list[str]:
    """Extract CTE names from WITH clauses."""
    # Match: WITH name AS, WITH RECURSIVE name AS, or , name AS (for multiple CTEs)
    pattern = r'(?:WITH(?:\s+RECURSIVE)?|,)\s+(\w+)\s+AS\s*\('
    return re.findall(pattern, sql, re.IGNORECASE)


# -----------------------------------------------------------------------------
# LLM artifact cleanup - fix common formatting mistakes before execution
# -----------------------------------------------------------------------------

def _strip_trailing_tool_params(sql: str) -> tuple[str, str | None]:
    """Strip trailing tool call parameters that LLMs mistakenly include in SQL.

    Example: '...ORDER BY price", will_continue_work=true' -> '...ORDER BY price'
    """
    # Pattern: trailing ", param=value or ", "param": value} patterns
    patterns = [
        # Trailing ", will_continue_work=true/false (with optional closing brace/quote)
        r'"\s*,\s*will_continue_work\s*=\s*(true|false)\s*[}"\']?\s*$',
        # Trailing ", "will_continue_work": true/false}
        r'"\s*,\s*"will_continue_work"\s*:\s*(true|false)\s*}\s*$',
        # Trailing "} or '}
        r'"\s*}\s*$',
        # Trailing ", followed by any param=value pattern
        r'"\s*,\s*\w+\s*=\s*\w+\s*$',
    ]
    for pattern in patterns:
        match = re.search(pattern, sql, re.IGNORECASE)
        if match:
            cleaned = sql[:match.start()].rstrip()
            return cleaned, f"stripped trailing '{match.group()}'"
    return sql, None


def _strip_markdown_fences(sql: str) -> tuple[str, str | None]:
    """Strip markdown code fences from SQL.

    Example: '```sql\nSELECT * FROM t\n```' -> 'SELECT * FROM t'
    """
    original = sql
    # Strip leading ```sql or ```
    sql = re.sub(r'^```(?:sql)?\s*\n?', '', sql, flags=re.IGNORECASE)
    # Strip trailing ```
    sql = re.sub(r'\n?```\s*$', '', sql)
    if sql != original:
        return sql.strip(), "stripped markdown fences"
    return sql, None


def _fix_escaped_quotes(sql: str) -> tuple[str, str | None]:
    r"""Fix escaped quotes that LLMs sometimes produce.

    Example: 'WHERE name = \"John\"' -> "WHERE name = 'John'"
    """
    original = sql
    # Replace \" with ' (JSON-style escaping used for SQL strings)
    sql = sql.replace('\\"', "'")
    # Replace \' with '' (SQL-style escape)
    sql = sql.replace("\\'", "''")
    if sql != original:
        return sql, "fixed escaped quotes"
    return sql, None


def _fix_unescaped_single_quote_runs(sql: str) -> tuple[str, str | None]:
    """Balance odd-length runs of single quotes.

    Example: REPLACE(text, '&#x27;', ''') -> REPLACE(text, '&#x27;', '''')
    """
    original = sql

    def _balance(match: re.Match[str]) -> str:
        run = match.group(0)
        if len(run) > 1 and len(run) % 2 == 1:
            return run + "'"
        return run

    sql = re.sub(r"'+", _balance, sql)
    if sql != original:
        return sql, "balanced single-quote run"
    return sql, None


def _fix_python_operators(sql: str) -> tuple[str, str | None]:
    """Fix Python/C operators used instead of SQL operators.

    Examples: == -> =, && -> AND, != stays (valid in SQLite)
    """
    corrections = []

    # == to = (but not inside strings)
    # Use a simple heuristic: replace == that's not inside quotes
    if '==' in sql:
        # Only fix if it looks like a comparison, not inside a string
        new_sql = re.sub(r'(?<![\'"])\s*==\s*(?![\'"])', ' = ', sql)
        if new_sql != sql:
            sql = new_sql
            corrections.append("'==' -> '='")

    # && to AND (outside strings)
    if '&&' in sql:
        new_sql = re.sub(r'\s*&&\s*', ' AND ', sql)
        if new_sql != sql:
            sql = new_sql
            corrections.append("'&&' -> 'AND'")

    # || for logical OR is tricky - in SQLite || is string concat
    # Only fix if it looks like logical OR context (between conditions)
    # This is risky so we'll skip it

    if corrections:
        return sql, ", ".join(corrections)
    return sql, None


def _fix_dialect_functions(sql: str) -> tuple[str, str | None]:
    """Fix functions from other SQL dialects.

    Examples: IF() -> IIF(), ILIKE -> LIKE, CONCAT() -> ||
    """
    corrections = []

    # IF(cond, then, else) -> IIF(cond, then, else) - MySQL style
    if re.search(r'\bIF\s*\(', sql, re.IGNORECASE):
        # Make sure it's not already IIF
        new_sql = re.sub(r'\bIF\s*\(', 'IIF(', sql, flags=re.IGNORECASE)
        # But don't change IIF to IIIF
        new_sql = re.sub(r'\bIIIF\(', 'IIF(', new_sql, flags=re.IGNORECASE)
        if new_sql != sql:
            sql = new_sql
            corrections.append("IF() -> IIF()")

    # ILIKE -> LIKE (PostgreSQL case-insensitive like)
    # Note: SQLite LIKE is case-insensitive for ASCII by default
    if re.search(r'\bILIKE\b', sql, re.IGNORECASE):
        sql = re.sub(r'\bILIKE\b', 'LIKE', sql, flags=re.IGNORECASE)
        corrections.append("ILIKE -> LIKE")

    # NVL2(x, y, z) -> IIF(x IS NOT NULL, y, z) - Oracle style
    nvl2_match = re.search(r'\bNVL2\s*\(\s*([^,]+)\s*,\s*([^,]+)\s*,\s*([^)]+)\s*\)', sql, re.IGNORECASE)
    if nvl2_match:
        replacement = f"IIF({nvl2_match.group(1)} IS NOT NULL, {nvl2_match.group(2)}, {nvl2_match.group(3)})"
        sql = sql[:nvl2_match.start()] + replacement + sql[nvl2_match.end():]
        corrections.append("NVL2() -> IIF()")

    # CONCAT(a, b) -> (a || b) - MySQL/PostgreSQL style
    # Handle simple 2-arg case
    concat_pattern = r'\bCONCAT\s*\(\s*([^,()]+)\s*,\s*([^,()]+)\s*\)'
    while re.search(concat_pattern, sql, re.IGNORECASE):
        sql = re.sub(concat_pattern, r'(\1 || \2)', sql, count=1, flags=re.IGNORECASE)
        if "CONCAT" not in [c.split()[0] for c in corrections]:
            corrections.append("CONCAT() -> ||")

    # STRING_AGG(col, sep) -> GROUP_CONCAT(col, sep) - PostgreSQL style
    if re.search(r'\bSTRING_AGG\s*\(', sql, re.IGNORECASE):
        sql = re.sub(r'\bSTRING_AGG\s*\(', 'GROUP_CONCAT(', sql, flags=re.IGNORECASE)
        corrections.append("STRING_AGG() -> GROUP_CONCAT()")

    # ARRAY_AGG -> GROUP_CONCAT (PostgreSQL)
    if re.search(r'\bARRAY_AGG\s*\(', sql, re.IGNORECASE):
        sql = re.sub(r'\bARRAY_AGG\s*\(', 'GROUP_CONCAT(', sql, flags=re.IGNORECASE)
        corrections.append("ARRAY_AGG() -> GROUP_CONCAT()")

    if corrections:
        return sql, ", ".join(corrections)
    return sql, None


def _fix_dialect_syntax(sql: str) -> tuple[str, str | None]:
    """Fix SQL syntax from other dialects.

    Examples: TOP N -> LIMIT N, TRUNCATE -> DELETE FROM
    """
    corrections = []

    # SELECT TOP N ... -> SELECT ... LIMIT N (SQL Server style)
    top_match = re.search(r'\bSELECT\s+TOP\s+(\d+)\s+', sql, re.IGNORECASE)
    if top_match:
        n = top_match.group(1)
        # Remove TOP N and add LIMIT N at the end
        sql = re.sub(r'\bSELECT\s+TOP\s+\d+\s+', 'SELECT ', sql, flags=re.IGNORECASE)
        # Add LIMIT if not already present
        if not re.search(r'\bLIMIT\s+\d+', sql, re.IGNORECASE):
            sql = sql.rstrip().rstrip(';') + f' LIMIT {n}'
        corrections.append(f"TOP {n} -> LIMIT {n}")

    # TRUNCATE TABLE x -> DELETE FROM x (SQLite doesn't have TRUNCATE)
    truncate_match = re.search(r'\bTRUNCATE\s+(?:TABLE\s+)?(\w+)', sql, re.IGNORECASE)
    if truncate_match:
        table = truncate_match.group(1)
        sql = re.sub(r'\bTRUNCATE\s+(?:TABLE\s+)?\w+', f'DELETE FROM {table}', sql, flags=re.IGNORECASE)
        corrections.append("TRUNCATE -> DELETE FROM")

    # :: type cast -> CAST(x AS type) (PostgreSQL style)
    cast_match = re.search(r'(\w+)::(\w+)', sql)
    if cast_match:
        sql = re.sub(r'(\w+)::(\w+)', r'CAST(\1 AS \2)', sql)
        corrections.append(":: -> CAST()")

    if corrections:
        return sql, ", ".join(corrections)
    return sql, None


def _fix_unbalanced_parens(sql: str) -> tuple[str, str | None]:
    """Attempt to fix unbalanced parentheses.

    Only fixes simple cases: 1 extra open or 1 extra close paren.
    """
    # Count parens outside of strings
    open_count = 0
    close_count = 0
    in_string = False
    string_char = None

    for i, char in enumerate(sql):
        if in_string:
            if char == string_char:
                # Check for escaped quote
                if i + 1 < len(sql) and sql[i + 1] == string_char:
                    continue
                in_string = False
        else:
            if char in ("'", '"'):
                in_string = True
                string_char = char
            elif char == '(':
                open_count += 1
            elif char == ')':
                close_count += 1

    diff = open_count - close_count

    if diff == 1:
        # One extra open paren - add close paren at end
        sql = sql.rstrip().rstrip(';') + ')'
        return sql, "added missing ')'"
    elif diff == -1:
        # One extra close paren - try to remove trailing )
        if sql.rstrip().endswith(')'):
            sql = sql.rstrip()[:-1]
            return sql, "removed extra ')'"
    elif diff == 0:
        return sql, None

    # More complex imbalance - don't try to fix
    return sql, None


def _fix_trailing_commas(sql: str) -> tuple[str, str | None]:
    """Fix trailing commas before closing parentheses.

    Example: VALUES (1, 2, 3,) -> VALUES (1, 2, 3)
    This is a common LLM mistake in multi-row INSERT statements.
    """
    # Pattern: comma followed by optional whitespace then closing paren
    # But only outside of string literals
    original = sql
    result = []
    i = 0
    in_string = False
    string_char = None

    while i < len(sql):
        char = sql[i]

        if in_string:
            result.append(char)
            if char == string_char:
                # Check for escaped quote (doubled)
                if i + 1 < len(sql) and sql[i + 1] == string_char:
                    i += 1
                    result.append(sql[i])
                else:
                    in_string = False
        else:
            if char in ("'", '"'):
                in_string = True
                string_char = char
                result.append(char)
            elif char == ',':
                # Look ahead for optional whitespace then ')'
                j = i + 1
                while j < len(sql) and sql[j] in ' \t\n\r':
                    j += 1
                if j < len(sql) and sql[j] == ')':
                    # Skip this comma (don't append it)
                    pass
                else:
                    result.append(char)
            else:
                result.append(char)
        i += 1

    fixed = ''.join(result)
    if fixed != original:
        return fixed, "removed trailing comma before ')'"
    return sql, None


def _fix_singular_plural_tables(sql: str, error_msg: str) -> tuple[str, str | None]:
    """Fix singular/plural table name mismatches based on error message.

    If error says 'no such table: user', check if 'users' exists in CTEs or
    would make sense.
    """
    # Extract the missing table name from error
    match = re.search(r'no such table:\s*(\w+)', error_msg, re.IGNORECASE)
    if not match:
        return sql, None

    missing = match.group(1)
    cte_names = _extract_cte_names(sql)

    # Check for singular/plural variants
    variants = []
    if missing.endswith('s'):
        variants.append(missing[:-1])  # users -> user
    if missing.endswith('es'):
        variants.append(missing[:-2])  # boxes -> box
    if missing.endswith('ies'):
        variants.append(missing[:-3] + 'y')  # categories -> category
    # Add plural forms
    variants.append(missing + 's')  # user -> users
    if missing.endswith('y'):
        variants.append(missing[:-1] + 'ies')  # category -> categories
    if missing.endswith(('s', 'x', 'z', 'ch', 'sh')):
        variants.append(missing + 'es')  # box -> boxes

    # Check if any variant is a CTE
    for variant in variants:
        if variant.lower() in [c.lower() for c in cte_names]:
            # Replace missing with variant
            pattern = rf'\b{re.escape(missing)}\b'
            sql = re.sub(pattern, variant, sql, flags=re.IGNORECASE)
            return sql, f"'{missing}' -> '{variant}'"

    return sql, None


def _fix_singular_plural_columns(sql: str, error_msg: str) -> tuple[str, str | None]:
    """Fix singular/plural column name mismatches based on error message."""
    match = re.search(r'no such column:\s*(\w+)', error_msg, re.IGNORECASE)
    if not match:
        return sql, None

    missing = match.group(1)
    aliases = _extract_select_aliases(sql)
    if not aliases:
        return sql, None

    best_alias = _best_identifier_match(missing, aliases)
    if best_alias:
        pattern = rf'\b{re.escape(missing)}\b'
        sql, count = _replace_outside_literals(sql, pattern, best_alias, flags=re.IGNORECASE)
        if count:
            return sql, f"'{missing}' -> '{best_alias}'"

    return sql, None


def _fix_json_key_vs_alias(sql: str, error_msg: str) -> tuple[str, str | None]:
    """Fix when LLM uses JSON key name instead of SQL alias in ORDER BY/WHERE.

    Example: SELECT json_extract(x, '$.objectID') as comment_id ... ORDER BY objectID
    Should be: ORDER BY comment_id
    """
    match = re.search(r'no such column:\s*(\w+)', error_msg, re.IGNORECASE)
    if not match:
        return sql, None

    missing = match.group(1)
    missing_lower = missing.lower()

    # Look for patterns like: json_extract(..., '$.{missing}') as {alias}
    # or: json_extract(..., '$.{missing}') AS {alias}
    # The missing column might be the JSON key, and we should use the alias instead
    patterns = [
        # json_extract with the missing key, followed by AS alias
        rf"json_extract\s*\([^)]*['\"]\.{re.escape(missing)}['\"][^)]*\)\s+[Aa][Ss]\s+(\w+)",
        # Also check for $.key.missing or $[key].missing patterns
        rf"json_extract\s*\([^)]*['\"][^'\"]*{re.escape(missing)}['\"][^)]*\)\s+[Aa][Ss]\s+(\w+)",
    ]

    for pattern in patterns:
        alias_match = re.search(pattern, sql, re.IGNORECASE)
        if alias_match:
            alias = alias_match.group(1)
            # Only fix if the missing column appears after SELECT (in ORDER BY, WHERE, etc.)
            # Find where SELECT ends (roughly after FROM)
            from_match = re.search(r'\bFROM\b', sql, re.IGNORECASE)
            if from_match:
                after_select = sql[from_match.end():]
                # Check if missing appears in ORDER BY, GROUP BY, HAVING, or WHERE after FROM
                usage_pattern = rf'\b{re.escape(missing)}\b'
                if re.search(usage_pattern, after_select, re.IGNORECASE):
                    # Replace the missing column with the alias in ORDER BY/GROUP BY/HAVING
                    # Be careful to only replace after FROM clause
                    before = sql[:from_match.end()]
                    after_fixed = re.sub(usage_pattern, alias, after_select, flags=re.IGNORECASE)
                    if after_fixed != after_select:
                        return before + after_fixed, f"'{missing}' -> '{alias}' (use alias, not JSON key)"

    return sql, None


def _apply_all_sql_fixes(sql: str, error_msg: str = "") -> tuple[str, list[str]]:
    """Apply all SQL fixes and return (fixed_sql, list_of_corrections)."""
    corrections = []

    # Pre-execution cleanups (always apply)
    sql, fix = _strip_trailing_tool_params(sql)
    if fix:
        corrections.append(fix)

    sql, fix = _strip_markdown_fences(sql)
    if fix:
        corrections.append(fix)

    sql, fix = _fix_escaped_quotes(sql)
    if fix:
        corrections.append(fix)
    sql, fix = _fix_unescaped_single_quote_runs(sql)
    if fix:
        corrections.append(fix)

    sql, fix = _fix_python_operators(sql)
    if fix:
        corrections.append(fix)

    sql, fix = _fix_dialect_functions(sql)
    if fix:
        corrections.append(fix)

    sql, fix = _fix_dialect_syntax(sql)
    if fix:
        corrections.append(fix)

    sql, fix = _fix_unbalanced_parens(sql)
    if fix:
        corrections.append(fix)

    sql, fix = _fix_trailing_commas(sql)
    if fix:
        corrections.append(fix)

    # Error-driven fixes (only if we have an error message)
    if error_msg:
        sql, fix = _fix_singular_plural_tables(sql, error_msg)
        if fix:
            corrections.append(fix)

        sql, fix = _fix_singular_plural_columns(sql, error_msg)
        if fix:
            corrections.append(fix)

        sql, fix = _fix_json_key_vs_alias(sql, error_msg)
        if fix:
            corrections.append(fix)

    return sql, corrections


def _extract_select_aliases(sql: str) -> list[str]:
    """Extract column aliases from SELECT clauses (e.g., 'as points')."""
    pattern = r'\bAS\s+(\w+)'
    return re.findall(pattern, sql, re.IGNORECASE)


def _extract_table_refs(sql: str) -> list[tuple[str, str]]:
    """Extract table references from FROM/JOIN clauses.

    Returns list of (table_name, alias) tuples.
    If no alias, alias equals table_name.
    """
    refs = []

    # Find ALL FROM clauses in the SQL (there may be multiple due to CTEs/subqueries)
    # Take the last one as the "main" query's FROM clause
    from_matches = re.findall(
        r'\bFROM\s+(.+?)(?:\s+WHERE\b|\s+GROUP\b|\s+ORDER\b|\s+LIMIT\b|\s+UNION\b|;|$)',
        sql, re.IGNORECASE | re.DOTALL
    )

    if not from_matches:
        return refs

    # Use the last FROM clause (main query, not CTE subqueries)
    from_clause = from_matches[-1]

    # Handle comma-separated tables: "table1, table2 t2, table3 AS t3"
    # Also handle JOINs inline
    parts = re.split(r'\s+(?:LEFT\s+|RIGHT\s+|INNER\s+|OUTER\s+|CROSS\s+)?JOIN\s+', from_clause, flags=re.IGNORECASE)
    for part in parts:
        # Each part could be "table alias" or "table, table2 alias2"
        tables = [t.strip() for t in part.split(',')]
        for table_str in tables:
            table_str = table_str.strip()
            if not table_str:
                continue
            # Parse "table [AS] alias" or just "table"
            match = re.match(r'^(\w+)(?:\s+AS\s+(\w+)|\s+(\w+))?', table_str, re.IGNORECASE)
            if match:
                table_name = match.group(1)
                alias = match.group(2) or match.group(3) or table_name
                refs.append((table_name, alias))
    return refs


def _split_sql_outside_literals(sql: str) -> list[tuple[str, bool]]:
    """Split SQL into (segment, is_code) pieces, ignoring string literals/comments."""
    segments: list[tuple[str, bool]] = []
    i = 0
    start = 0
    length = len(sql)

    while i < length:
        ch = sql[i]

        if ch == "-" and i + 1 < length and sql[i + 1] == "-":
            if start < i:
                segments.append((sql[start:i], True))
            j = i + 2
            while j < length and sql[j] != "\n":
                j += 1
            segments.append((sql[i:j], False))
            i = j
            start = i
            continue

        if ch == "/" and i + 1 < length and sql[i + 1] == "*":
            if start < i:
                segments.append((sql[start:i], True))
            j = i + 2
            while j + 1 < length and not (sql[j] == "*" and sql[j + 1] == "/"):
                j += 1
            j = j + 2 if j + 1 < length else length
            segments.append((sql[i:j], False))
            i = j
            start = i
            continue

        if ch == "'":
            if start < i:
                segments.append((sql[start:i], True))
            j = i + 1
            while j < length:
                if sql[j] == "'":
                    if j + 1 < length and sql[j + 1] == "'":
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            segments.append((sql[i:j], False))
            i = j
            start = i
            continue

        i += 1

    if start < length:
        segments.append((sql[start:], True))

    return segments


def _replace_outside_literals(
    sql: str,
    pattern: str,
    replacement: str,
    *,
    flags: int = 0,
) -> tuple[str, int]:
    """Replace pattern outside string literals/comments. Returns (sql, count)."""
    segments = _split_sql_outside_literals(sql)
    parts: list[str] = []
    total = 0
    for segment, is_code in segments:
        if is_code:
            updated, count = re.subn(pattern, replacement, segment, flags=flags)
            parts.append(updated)
            total += count
        else:
            parts.append(segment)
    return "".join(parts), total


def _placeholder_sql_literals(sql: str) -> tuple[str, list[str]]:
    segments = _split_sql_outside_literals(sql)
    literals: list[str] = []
    parts: list[str] = []
    for segment, is_code in segments:
        if is_code:
            parts.append(segment)
        else:
            placeholder = f"__lit_{len(literals)}__"
            literals.append(segment)
            parts.append(placeholder)
    return "".join(parts), literals


def _restore_sql_literals(sql: str, literals: list[str]) -> str:
    restored = sql
    for idx, literal in enumerate(literals):
        restored = restored.replace(f"__lit_{idx}__", literal)
    return restored


def _normalize_identifier(identifier: str) -> str:
    cleaned = identifier.strip().strip('`"[]')
    return cleaned.lower()


def _levenshtein_distance(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    if len(a) < len(b):
        a, b = b, a

    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            insert = current[j - 1] + 1
            delete = previous[j] + 1
            replace = previous[j - 1] + (0 if ca == cb else 1)
            current.append(min(insert, delete, replace))
        previous = current
    return previous[-1]


def _identifier_similarity(a: str, b: str) -> tuple[float, int]:
    a_norm = _normalize_identifier(a)
    b_norm = _normalize_identifier(b)
    if not a_norm or not b_norm:
        return 0.0, max(len(a_norm), len(b_norm))

    distance = _levenshtein_distance(a_norm, b_norm)
    ratio = 1.0 - (distance / max(len(a_norm), len(b_norm)))

    a_compact = a_norm.replace("_", "")
    b_compact = b_norm.replace("_", "")
    if a_compact != a_norm or b_compact != b_norm:
        compact_distance = _levenshtein_distance(a_compact, b_compact)
        compact_ratio = 1.0 - (compact_distance / max(len(a_compact), len(b_compact)))
        if compact_ratio > ratio:
            ratio = compact_ratio
            distance = min(distance, compact_distance)

    return ratio, distance


def _best_identifier_match(target: str, candidates: list[str]) -> str | None:
    target_norm = _normalize_identifier(target)
    if not target_norm:
        return None

    scored: list[tuple[float, int, str]] = []
    seen: set[str] = set()
    for candidate in candidates:
        cand_norm = _normalize_identifier(candidate)
        if not cand_norm or cand_norm == target_norm or cand_norm in seen:
            continue
        seen.add(cand_norm)
        ratio, distance = _identifier_similarity(target_norm, cand_norm)
        if ratio <= 0:
            continue
        scored.append((ratio, distance, candidate))

    if not scored:
        return None

    scored.sort(key=lambda item: (-item[0], item[1], item[2].lower()))
    best_ratio, best_distance, best_candidate = scored[0]
    length = len(target_norm)
    min_ratio = 0.92 if length <= 4 else 0.88 if length <= 8 else 0.84
    max_distance = 1 if length <= 6 else 2

    if best_ratio < min_ratio and not (best_distance == 1 and length >= 3):
        return None
    if best_distance > max_distance and best_ratio < 0.95:
        return None
    if len(scored) > 1:
        second_ratio, second_distance, _ = scored[1]
        margin = 0.1 if length <= 4 else 0.06
        if best_ratio - second_ratio < margin and best_distance >= second_distance:
            return None

    return best_candidate


def _split_qualified_identifier(identifier: str) -> tuple[str | None, str]:
    parts = [part for part in identifier.split(".") if part]
    if len(parts) > 1:
        return ".".join(parts[:-1]), parts[-1]
    return None, identifier


def _extract_result_json_paths(sql: str) -> list[str]:
    pattern = re.compile(
        r"\bjson_(?:extract|each)\s*\(\s*([^,]+?)\s*,\s*(['\"])([^'\"]+)\2",
        re.IGNORECASE,
    )
    paths: list[str] = []
    for match in pattern.finditer(sql):
        source = match.group(1)
        if "result_json" not in source.lower():
            continue
        path = match.group(3).strip()
        if path:
            paths.append(path)
    return paths


def _derive_result_json_bases(paths: list[str], missing: str) -> list[str]:
    base_counts: dict[str, int] = {}
    for path in paths:
        path = path.strip()
        if not path.startswith("$"):
            continue
        if "." not in path:
            continue
        segments = path.split(".")
        base = None
        if len(segments) >= 3:
            base = ".".join(segments[:-1]) + "."
        elif len(segments) == 2:
            last_segment = segments[1]
            if last_segment and last_segment.lower() != missing.lower():
                base = path + "."
        if not base or base in ("$.", "$"):
            continue
        base_counts[base] = base_counts.get(base, 0) + 1

    if not base_counts:
        return []

    return sorted(base_counts.keys(), key=lambda b: (-base_counts[b], -len(b), b))


def _autocorrect_missing_column_with_json_paths(
    sql: str,
    error_msg: str,
) -> list[tuple[str, str]]:
    match = re.search(r'no such column:\s*([^\s]+)', error_msg, re.IGNORECASE)
    if not match:
        return []

    missing_raw = match.group(1).strip().strip("'\"")
    if not missing_raw:
        return []

    qualifier, missing = _split_qualified_identifier(missing_raw)
    if not missing:
        return []

    table_refs = _extract_table_refs(sql)
    tool_alias = None
    for table_name, alias in table_refs:
        if table_name.lower() == "__tool_results":
            tool_alias = alias
            break
    if not tool_alias:
        return []
    if qualifier and qualifier.lower() != tool_alias.lower():
        return []

    paths = _extract_result_json_paths(sql)
    bases = _derive_result_json_bases(paths, missing)
    if not bases:
        return []

    result_json_ref = f"{tool_alias}.result_json"
    candidates: list[tuple[str, str]] = []
    for base in bases:
        replacement_expr = f"json_extract({result_json_ref}, '{base}{missing}')"
        if qualifier:
            pattern = rf'(\b{re.escape(qualifier)}\s*\.\s*){re.escape(missing)}\b'
        else:
            pattern = rf'(?<!\.)(?<!\w)\b{re.escape(missing)}\b(?!\.)'
        updated_sql, count = _replace_outside_literals(sql, pattern, replacement_expr, flags=re.IGNORECASE)
        if count:
            candidates.append((updated_sql, f"'{missing_raw}' -> {replacement_expr}"))

    return candidates


def _get_schema_tables(conn: sqlite3.Connection) -> list[str]:
    tables: list[str] = []
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view') "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name;"
        )
        tables.extend([row[0] for row in cur.fetchall()])
    except Exception:
        pass
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_temp_master WHERE type IN ('table','view') "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name;"
        )
        tables.extend([row[0] for row in cur.fetchall()])
    except Exception:
        pass

    seen: set[str] = set()
    deduped: list[str] = []
    for name in tables:
        if name in seen:
            continue
        seen.add(name)
        deduped.append(name)
    return deduped


def _table_has_column(conn: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    try:
        cur = conn.cursor()
        cur.execute(f'PRAGMA table_info("{table_name}");')
        target = column_name.lower()
        return any((row[1] or "").lower() == target for row in cur.fetchall())
    except Exception:
        return False


def _rewrite_result_id_comparisons(
    sql: str,
    *,
    column_ref: str,
    compat_ref: str,
) -> str:
    if column_ref.lower() not in sql.lower():
        return sql

    if "." in column_ref:
        col_pattern = rf"\b{re.escape(column_ref)}\b"
    else:
        col_pattern = rf"(?<!\.)\b{re.escape(column_ref)}\b"

    placeholder_sql, literals = _placeholder_sql_literals(sql)
    patterns = [
        (
            rf"{col_pattern}\s*=\s*([^\s),;]+)",
            rf"({column_ref} = \1 OR {compat_ref} = \1)",
        ),
        (
            rf"([^\s(,;]+)\s*=\s*{col_pattern}",
            rf"(\1 = {column_ref} OR \1 = {compat_ref})",
        ),
        (
            rf"{col_pattern}\s+IN\s*(\([^)]*\))",
            rf"({column_ref} IN \1 OR {compat_ref} IN \1)",
        ),
    ]

    for pattern, replacement in patterns:
        placeholder_sql = re.sub(pattern, replacement, placeholder_sql, flags=re.IGNORECASE)
    return _restore_sql_literals(placeholder_sql, literals)


def _apply_tool_results_result_id_compat(sql: str, conn: sqlite3.Connection) -> str:
    if "__tool_results" not in sql.lower():
        return sql
    if not _table_has_column(conn, "__tool_results", "legacy_result_id"):
        return sql

    aliases = {
        alias
        for table_name, alias in _extract_table_refs(sql)
        if table_name.lower() == "__tool_results"
    }
    if not aliases:
        aliases = {"__tool_results"}

    for alias in aliases:
        if alias:
            sql = _rewrite_result_id_comparisons(
                sql,
                column_ref=f"{alias}.result_id",
                compat_ref=f"{alias}.legacy_result_id",
            )
    sql = _rewrite_result_id_comparisons(
        sql,
        column_ref="result_id",
        compat_ref="legacy_result_id",
    )
    return sql


def _get_schema_columns(conn: sqlite3.Connection, table_name: str) -> list[str]:
    try:
        cur = conn.cursor()
        cur.execute(f'PRAGMA table_info("{table_name}");')
        return [row[1] for row in cur.fetchall() if row[1]]
    except Exception:
        return []


def _autocorrect_missing_table_with_schema(
    sql: str,
    error_msg: str,
    conn: sqlite3.Connection,
) -> tuple[str, str | None]:
    match = re.search(r'no such table:\s*([^\s]+)', error_msg, re.IGNORECASE)
    if not match:
        return sql, None

    missing_raw = match.group(1).strip().strip("'\"")
    if not missing_raw:
        return sql, None

    prefix, missing = _split_qualified_identifier(missing_raw)
    if missing.lower() in ("sqlite_master", "sqlite_schema", "sqlite_temp_master", "sqlite_temp_schema"):
        return sql, None

    candidates = _get_schema_tables(conn)
    if not candidates:
        return sql, None

    best = _best_identifier_match(missing, candidates)
    if not best:
        return sql, None

    if prefix:
        pattern = rf'(\b{re.escape(prefix)}\s*\.\s*){re.escape(missing)}\b'
        replacement = rf'\1{best}'
    else:
        pattern = rf'\b{re.escape(missing)}\b'
        replacement = best

    sql, count = _replace_outside_literals(sql, pattern, replacement, flags=re.IGNORECASE)
    if not count:
        return sql, None

    replacement_name = f"{prefix}.{best}" if prefix else best
    return sql, f"'{missing_raw}' -> '{replacement_name}'"


def _autocorrect_missing_column_with_schema(
    sql: str,
    error_msg: str,
    conn: sqlite3.Connection,
) -> tuple[str, str | None]:
    match = re.search(r'no such column:\s*([^\s]+)', error_msg, re.IGNORECASE)
    if not match:
        return sql, None

    missing_raw = match.group(1).strip().strip("'\"")
    if not missing_raw:
        return sql, None

    qualifier, missing = _split_qualified_identifier(missing_raw)
    tables = _get_schema_tables(conn)
    if not tables:
        return sql, None

    table_lookup = {name.lower(): name for name in tables}
    alias_map: dict[str, str] = {}
    for table_name, alias in _extract_table_refs(sql):
        table_key = table_name.lower()
        if table_key in table_lookup:
            alias_map[alias.lower()] = table_lookup[table_key]

    if qualifier:
        table_name = alias_map.get(qualifier.lower()) or table_lookup.get(qualifier.lower())
        if not table_name:
            return sql, None
        columns = _get_schema_columns(conn, table_name)
        if not columns:
            return sql, None
        best = _best_identifier_match(missing, columns)
        if not best:
            return sql, None
        pattern = rf'(\b{re.escape(qualifier)}\s*\.\s*){re.escape(missing)}\b'
        replacement = rf'\1{best}'
        sql, count = _replace_outside_literals(sql, pattern, replacement, flags=re.IGNORECASE)
        if count:
            return sql, f"'{missing_raw}' -> '{qualifier}.{best}'"
        return sql, None

    if not alias_map:
        return sql, None

    table_candidates = list(dict.fromkeys(alias_map.values()))
    col_to_tables: dict[str, set[str]] = {}
    col_display: dict[str, str] = {}
    for table_name in table_candidates:
        for col in _get_schema_columns(conn, table_name):
            col_norm = _normalize_identifier(col)
            if not col_norm:
                continue
            col_display.setdefault(col_norm, col)
            col_to_tables.setdefault(col_norm, set()).add(table_name)

    if not col_display:
        return sql, None

    best = _best_identifier_match(missing, list(col_display.values()))
    if not best:
        return sql, None

    best_norm = _normalize_identifier(best)
    if len(col_to_tables.get(best_norm, set())) > 1 and len(table_candidates) > 1:
        return sql, None

    pattern = rf'(?<!\.)(?<!\w)\b{re.escape(missing)}\b(?!\.)'
    sql, count = _replace_outside_literals(sql, pattern, best, flags=re.IGNORECASE)
    if count:
        return sql, f"'{missing_raw}' -> '{best}'"
    return sql, None


def _autocorrect_ambiguous_column(sql: str, column_name: str) -> tuple[str, str | None]:
    """Attempt to fix ambiguous column by qualifying with first table.

    Returns (corrected_sql, correction_description) or (original_sql, None) if no fix.
    """
    cte_names = set(name.lower() for name in _extract_cte_names(sql))
    table_refs = _extract_table_refs(sql)

    if len(table_refs) < 2:
        # Not a multi-table query, can't auto-fix
        return sql, None

    # Find the "main" table (first non-CTE table, or first table if all CTEs)
    main_alias = None
    for table_name, alias in table_refs:
        if table_name.lower() not in cte_names:
            main_alias = alias
            break
    if not main_alias:
        main_alias = table_refs[0][1]  # Fall back to first table

    # Use negative lookbehind/lookahead to match only unqualified columns
    # Negative lookbehind: not preceded by a dot (already qualified like "table.column")
    # Negative lookahead: not followed by a dot (is a table alias like "column.field")
    pattern = rf'(?<!\.)(?<!\w)\b({re.escape(column_name)})\b(?!\.)'

    def replace_unqualified(match: re.Match) -> str:
        col = match.group(1)
        return f"{main_alias}.{col}"

    corrected = re.sub(pattern, replace_unqualified, sql, flags=re.IGNORECASE)

    if corrected != sql:
        return corrected, f"'{column_name}'->'{main_alias}.{column_name}'"
    return sql, None


def _is_typo(s1: str, s2: str) -> bool:
    """Check if s1 is likely a typo of s2 (off by 1 char)."""
    s1, s2 = s1.lower(), s2.lower()
    if s1 == s2:
        return False
    # Same length, 1 char different
    if len(s1) == len(s2):
        return sum(a != b for a, b in zip(s1, s2)) == 1
    # Off by 1 char (missing or extra)
    if abs(len(s1) - len(s2)) == 1:
        longer, shorter = (s1, s2) if len(s1) > len(s2) else (s2, s1)
        for i in range(len(longer)):
            if longer[:i] + longer[i+1:] == shorter:
                return True
    return False


def _autocorrect_cte_typos(sql: str) -> tuple[str, list[str]]:
    """Auto-correct obvious CTE name typos (e.g., 'comment' -> 'comments').

    Returns (corrected_sql, list_of_corrections).
    Only corrects when there's exactly one CTE that's 1 char different.
    """
    cte_names = _extract_cte_names(sql)
    if not cte_names:
        return sql, []

    cte_lower = {name.lower(): name for name in cte_names}
    corrections = []

    # Find table references in FROM/JOIN clauses (not after AS which defines aliases)
    # Pattern: FROM/JOIN followed by identifier (not a subquery)
    table_refs = re.findall(r'\b(?:FROM|JOIN)\s+(\w+)(?!\s*\()', sql, re.IGNORECASE)

    for ref in table_refs:
        ref_lower = ref.lower()
        # Skip if it's already a valid CTE name
        if ref_lower in cte_lower:
            continue
        # Skip common table names that shouldn't be auto-corrected
        if ref_lower in ('__tool_results', 'sqlite_master', 'sqlite_schema'):
            continue
        # Check if it's a typo of any CTE
        for cte_name in cte_names:
            if _is_typo(ref, cte_name):
                # Replace this specific reference (case-insensitive, word boundary)
                pattern = rf'\b{re.escape(ref)}\b'
                sql = re.sub(pattern, cte_name, sql, flags=re.IGNORECASE)
                corrections.append(f"'{ref}'->'{cte_name}'")
                break

    return sql, corrections


def _build_autocorrection_candidates(
    sql: str,
    error_msg: str,
    conn: sqlite3.Connection,
) -> list[tuple[str, list[str]]]:
    candidates: list[tuple[str, list[str]]] = []

    corrected, fixes = _autocorrect_cte_typos(sql)
    if fixes and corrected != sql:
        candidates.append((corrected, fixes))

    ambig_match = re.search(r'ambiguous column name:\s*(\w+)', error_msg, re.IGNORECASE)
    if ambig_match:
        corrected, fix = _autocorrect_ambiguous_column(sql, ambig_match.group(1))
        if fix and corrected != sql:
            candidates.append((corrected, [fix]))

    corrected, fix = _fix_singular_plural_tables(sql, error_msg)
    if fix and corrected != sql:
        candidates.append((corrected, [fix]))

    corrected, fix = _autocorrect_missing_table_with_schema(sql, error_msg, conn)
    if fix and corrected != sql:
        candidates.append((corrected, [fix]))

    corrected, fix = _fix_singular_plural_columns(sql, error_msg)
    if fix and corrected != sql:
        candidates.append((corrected, [fix]))

    corrected, fix = _fix_json_key_vs_alias(sql, error_msg)
    if fix and corrected != sql:
        candidates.append((corrected, [fix]))

    corrected, fix = _autocorrect_missing_column_with_schema(sql, error_msg, conn)
    if fix and corrected != sql:
        candidates.append((corrected, [fix]))

    for updated_sql, fix in _autocorrect_missing_column_with_json_paths(sql, error_msg):
        if updated_sql != sql:
            candidates.append((updated_sql, [fix]))

    for updated_sql, fixes in build_cte_column_candidates(sql, error_msg):
        if updated_sql != sql:
            candidates.append((updated_sql, fixes))

    for updated_sql, fixes in build_sqlglot_candidates(sql, error_msg):
        if updated_sql != sql:
            candidates.append((updated_sql, fixes))

    return candidates


def _execute_with_autocorrections(
    conn: sqlite3.Connection,
    cur: sqlite3.Cursor,
    query: str,
    idx: int,
    base_corrections: list[str],
) -> tuple[Dict[str, Any] | None, str, list[str], str | None]:
    seen: set[str] = {query}
    queue_items: deque[tuple[str, list[str]]] = deque([(query, base_corrections)])
    attempts = 0
    last_error_message = None
    last_error_query = query

    while queue_items and attempts < MAX_AUTO_CORRECTION_ATTEMPTS:
        current_query, corrections = queue_items.popleft()
        attempts += 1
        try:
            start_query_timer(conn)
            cur.execute(current_query)
            if cur.description is not None:
                columns = [col[0] for col in cur.description]
                rows = [dict(zip(columns, row)) for row in cur.fetchall()]
                original_count = len(rows)
                rows, limit_warning = _enforce_result_limits(rows, current_query)
                result_entry: Dict[str, Any] = {
                    "result": rows,
                    "message": f"Query {idx} returned {original_count} rows.{limit_warning}",
                }
            else:
                affected = cur.rowcount if cur.rowcount is not None else 0
                msg = f"Query {idx} affected {max(0, affected)} rows."
                query_upper = current_query.upper()
                if affected <= 0 and "WITH" in query_upper and "INSERT" in query_upper:
                    msg += " (Normal for CTE INSERT - check sqlite_schema for actual row count)"
                elif affected == 0 and ("UPDATE" in query_upper or "DELETE" in query_upper):
                    msg += (
                        " (No match—verify WHERE values against ground truth: "
                        "schema, kanban snapshot, tool results, or prior query output.)"
                    )
                result_entry = {"message": msg}
            return result_entry, current_query, corrections, None
        except Exception as orig_exc:
            last_error_message = str(orig_exc)
            last_error_query = current_query
            conn.rollback()
            for updated_sql, fixes in _build_autocorrection_candidates(current_query, last_error_message, conn):
                if updated_sql in seen:
                    continue
                if len(seen) >= MAX_AUTO_CORRECTION_CANDIDATES:
                    break
                seen.add(updated_sql)
                queue_items.append((updated_sql, corrections + fixes))
        finally:
            stop_query_timer(conn)

    if last_error_message is None:
        last_error_message = "Query failed without error details."
    return None, last_error_query, base_corrections, last_error_message


def _get_error_hint(error_msg: str, sql: str = "") -> str:
    """Return a helpful hint for common SQLite errors."""
    error_lower = error_msg.lower()
    if "union" in error_lower and "column" in error_lower:
        return " FIX: All SELECTs in UNION/UNION ALL must have the same number of columns."
    if "no column named" in error_lower or "no such column" in error_lower:
        # Extract the missing column name
        match = re.search(r'no such column:\s*(\w+)', error_msg, re.IGNORECASE)
        if not match:
            match = re.search(r'no column named\s+(\w+)', error_msg, re.IGNORECASE)
        if match and sql:
            missing = match.group(1)
            aliases = _extract_select_aliases(sql)
            for alias in aliases:
                if _is_typo(missing, alias):
                    return f" FIX: Typo? You defined alias '{alias}' but referenced '{missing}'."
        return " FIX: Check column name spelling matches your SELECT aliases or table schema."
    if "no such table" in error_lower:
        # Extract the missing table name
        match = re.search(r'no such table:\s*(\w+)', error_msg, re.IGNORECASE)
        if match and sql:
            missing = match.group(1)
            cte_names = _extract_cte_names(sql)
            for cte in cte_names:
                if _is_typo(missing, cte):
                    return f" FIX: Typo? You defined CTE '{cte}' but referenced '{missing}'."
        return " FIX: Create the table first with CREATE TABLE before querying it."
    if "syntax error" in error_lower:
        # Check if it might be a quote escaping issue
        if "'" in sql and ("regexp" in sql.lower() or "pattern" in sql.lower()):
            return " FIX: Quote escaping issue. Use extract_urls()/extract_emails() for URLs/emails, or escape ' as '' in regex patterns."
        return " FIX: Check SQL syntax - common issues: missing quotes, commas, or parentheses."
    if "wrong number of arguments" in error_lower:
        return " FIX: Check parentheses in nested function calls - a ')' is likely misplaced."
    if "unique constraint" in error_lower:
        return " FIX: Use INSERT OR REPLACE or INSERT OR IGNORE to handle duplicate keys."
    if "malformed json" in error_lower:
        # Check if they're misusing grep_context_all or similar functions
        if "grep_context" in sql.lower():
            return " FIX: grep_context_all returns array of STRINGS, not objects. Use: SELECT ctx.value FROM json_each(grep_context_all(...)) ctx"
        if "split_sections" in sql.lower():
            return " FIX: split_sections returns array of STRINGS. Use: SELECT s.value FROM json_each(split_sections(...)) s"
        return " FIX: json_extract requires valid JSON. Check that the column/expression contains JSON, not plain text."
    return ""


def _enforce_result_limits(rows: List[Dict[str, Any]], query: str) -> tuple[List[Dict[str, Any]], str]:
    """Enforce context protection limits on query results.

    Returns (limited_rows, warning_message).
    """
    warning = ""
    total_rows = len(rows)

    # Check if query already has LIMIT
    query_upper = query.upper()
    has_limit = bool(re.search(r'\bLIMIT\s+\d+', query_upper))

    # Hard cap on rows
    if total_rows > MAX_RESULT_ROWS:
        rows = rows[:MAX_RESULT_ROWS]
        warning = f" [!] TRUNCATED: {total_rows} rows -> {MAX_RESULT_ROWS}. Add LIMIT to your query."

    # Check byte size
    try:
        result_bytes = len(json.dumps(rows, default=str).encode('utf-8'))
        if result_bytes > MAX_RESULT_BYTES:
            # Progressively reduce until under limit
            while len(rows) > 10 and len(json.dumps(rows, default=str).encode('utf-8')) > MAX_RESULT_BYTES:
                rows = rows[:len(rows) // 2]
            warning = f" [!] TRUNCATED to {len(rows)} rows (size limit). Use LIMIT and specific columns."
    except Exception:
        pass

    # Warn about missing LIMIT even if not truncated
    if not warning and total_rows > WARN_RESULT_ROWS and not has_limit:
        warning = f" [!] Large result ({total_rows} rows). Consider adding LIMIT for efficiency."

    return rows, warning


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


def _extract_sql_param(params: Dict[str, Any]) -> Any:
    for key in ("sql", "query", "queries"):
        if key in params:
            return params.get(key)
    return None


def _unwrap_wrapped_sql(statement: str) -> str:
    trimmed = statement.strip()
    if len(trimmed) >= 2 and trimmed[0] == trimmed[-1] and trimmed[0] in ("'", '"'):
        inner = trimmed[1:-1].strip()
        if inner:
            return inner
    return trimmed


def _normalize_queries(params: Dict[str, Any]) -> Optional[List[str]]:
    """Return a list of SQL strings from sql/query/queries inputs."""
    raw = _extract_sql_param(params)
    if raw is None:
        return None

    if isinstance(raw, dict):
        raw = _extract_sql_param(raw)
        if raw is None:
            return None

    if isinstance(raw, str):
        items: List[str] = [_unwrap_wrapped_sql(raw)]
    elif isinstance(raw, list):
        items = raw
    else:
        return None

    queries: List[str] = []
    for item in items:
        if not isinstance(item, str):
            return None
        normalized = _unwrap_wrapped_sql(item)
        if not normalized:
            continue
        trimmed = normalized.strip()
        if trimmed.startswith("[") and trimmed.endswith("]"):
            try:
                parsed = json.loads(trimmed)
            except json.JSONDecodeError:
                return None
            if not isinstance(parsed, list) or not all(isinstance(entry, str) for entry in parsed):
                return None
            for entry in parsed:
                split_items = _split_sqlite_statements(_unwrap_wrapped_sql(entry))
                if split_items:
                    queries.extend(split_items)
            continue
        split_items = _split_sqlite_statements(normalized)
        if split_items:
            queries.extend(split_items)

    return queries if queries else None


def _run_sqlite_batch_in_subprocess(
    *,
    agent_id: str,
    params: Dict[str, Any],
    db_path: str,
    limits: _SqliteBatchLimits,
) -> Dict[str, Any]:
    """Run SQLite batch in a subprocess using subprocess.Popen.

    This approach avoids the "daemonic processes are not allowed to have children"
    error that occurs with multiprocessing when running under gunicorn/uvicorn workers.
    """
    payload = {
        "agent_id": agent_id,
        "params": params,
        "db_path": db_path,
        "limits": {
            "wall_timeout_seconds": limits.wall_timeout_seconds,
            "cpu_seconds": limits.cpu_seconds,
            "memory_mb": limits.memory_mb,
            "query_timeout_seconds": limits.query_timeout_seconds,
        },
    }

    # Write payload to temp file (safer than piping large payloads)
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(payload, f)
            payload_file = f.name
    except Exception as exc:
        logger.exception("Failed to create sqlite_batch payload file")
        return {"status": "error", "message": f"SQLite batch failed to start: {exc}"}

    try:
        # Run the worker as a subprocess using this module's __main__ block
        process = subprocess.Popen(
            [sys.executable, "-m", "api.agent.tools.sqlite_batch", payload_file],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        try:
            stdout, stderr = process.communicate(timeout=limits.wall_timeout_seconds)
        except subprocess.TimeoutExpired:
            timeout_label = f"{limits.wall_timeout_seconds:g}"
            logger.warning("SQLite batch timed out for agent %s after %s seconds", agent_id, timeout_label)
            process.terminate()
            try:
                process.wait(timeout=DEFAULT_SQLITE_BATCH_TERMINATE_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=DEFAULT_SQLITE_BATCH_KILL_GRACE_SECONDS)
            return {"status": "error", "message": f"SQLite batch timed out after {timeout_label} seconds."}

        if process.returncode != 0:
            error_detail = stderr.strip() if stderr else f"exit code {process.returncode}"
            if process.returncode < 0:
                return {"status": "error", "message": f"SQLite batch terminated by signal {-process.returncode}."}
            return {"status": "error", "message": f"SQLite batch failed: {error_detail}"}

        if not stdout.strip():
            return {"status": "error", "message": "SQLite batch failed: worker did not return a result."}

        try:
            return json.loads(stdout)
        except json.JSONDecodeError as exc:
            logger.error("Failed to parse sqlite_batch result: %s", exc)
            return {"status": "error", "message": f"SQLite batch failed: invalid result format"}

    except Exception as exc:
        logger.exception("Failed to run sqlite_batch worker")
        return {"status": "error", "message": f"SQLite batch failed to start: {exc}"}
    finally:
        # Clean up temp file
        try:
            os.unlink(payload_file)
        except Exception:
            pass


def _execute_sqlite_batch_inner(
    *,
    agent_id: str,
    params: Dict[str, Any],
    db_path: str,
    query_timeout_seconds: float,
) -> Dict[str, Any]:
    """Execute one or more SQL queries against the agent's SQLite DB."""
    queries = _normalize_queries(params)
    if not queries:
        return {
            "status": "error",
            "message": "Provide `sql` as a SQL string (semicolon-separated for multiple statements).",
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
    user_message_raw = params.get("_has_user_facing_message", None)
    if user_message_raw is None:
        has_user_facing_message = False
    elif isinstance(user_message_raw, bool):
        has_user_facing_message = user_message_raw
    elif isinstance(user_message_raw, str):
        has_user_facing_message = user_message_raw.lower() == "true"
    else:
        has_user_facing_message = bool(user_message_raw)

    conn: Optional[sqlite3.Connection] = None
    results: List[Dict[str, Any]] = []
    had_error = False
    error_message = ""
    only_write_queries = True
    all_corrections: List[str] = []

    try:
        conn = open_guarded_sqlite_connection(db_path, timeout_seconds=query_timeout_seconds)
        cur = conn.cursor()
        try:
            cur.execute("PRAGMA busy_timeout = 2000;")
        except Exception:
            pass

        preview = [q.strip()[:160] for q in queries[:5]]
        logger.info("Agent %s executing sqlite_batch: %s queries (preview=%s)", agent_id, len(queries), preview)

        for idx, query in enumerate(queries):
            if not isinstance(query, str) or not query.strip():
                had_error = True
                error_message = f"Query {idx} is empty or invalid."
                break

            original_query = query  # Keep original for error reporting
            query_corrections: list[str] = []

            # Apply pre-execution fixes (LLM artifacts, dialect fixes, etc.)
            query, pre_fixes = _apply_all_sql_fixes(query)
            if pre_fixes:
                query_corrections.extend(pre_fixes)

            block_reason = get_blocked_statement_reason(query)
            if block_reason:
                had_error = True
                error_message = f"Query {idx} blocked: {block_reason}"
                break

            query = _apply_tool_results_result_id_compat(query, conn)
            only_write_queries = only_write_queries and is_write_statement(query)

            result_entry, final_query, applied_corrections, failure_message = _execute_with_autocorrections(
                conn,
                cur,
                query,
                idx,
                query_corrections,
            )
            if failure_message:
                had_error = True
                hint = _get_error_hint(failure_message, final_query)
                error_message = f"Query {idx} failed: {failure_message}{hint}"
                break

            if result_entry is None:
                had_error = True
                error_message = f"Query {idx} failed: no result produced."
                break

            if result_entry.get("result") is not None:
                only_write_queries = False

            if applied_corrections and original_query != final_query:
                result_entry["auto_correction"] = {
                    "before": original_query,
                    "after": final_query,
                    "fixes": applied_corrections,
                }
                all_corrections.extend(applied_corrections)
            results.append(result_entry)
            conn.commit()

        db_size_mb = _get_db_size_mb(db_path)
        size_warning = ""
        if db_size_mb > 50:
            size_warning = " WARNING: DB SIZE EXCEEDS 50MB. YOU MUST EXECUTE MORE QUERIES TO SHRINK THE SIZE, OR THE WHOLE DB WILL BE WIPED!!!"

        # Build success message with any auto-corrections noted
        if had_error:
            msg = error_message
        else:
            msg = f"Executed {len(results)} queries. Database size: {db_size_mb:.2f} MB.{size_warning}"
            if all_corrections:
                msg = (
                    f"[!] AUTO-CORRECTED: {', '.join(all_corrections)}. "
                    "STOP making this mistake. Use extract_urls()/extract_emails() instead of regexp for URLs/emails. "
                    "For other patterns: escape single quotes as '' in SQL strings. "
                    + msg
                )

        response: Dict[str, Any] = {
            "status": "error" if had_error else "ok",
            "results": results,
            "db_size_mb": round(db_size_mb, 2),
            "message": msg,
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


def execute_sqlite_batch(agent: "PersistentAgent", params: Dict[str, Any]) -> Dict[str, Any]:
    """Execute one or more SQL queries against the agent's SQLite DB."""
    db_path = _sqlite_db_path_var.get(None)
    if not db_path:
        return {"status": "error", "message": "SQLite DB path unavailable"}

    limits = _resolve_sqlite_batch_limits()
    return _run_sqlite_batch_in_subprocess(
        agent_id=str(agent.id),
        params=params,
        db_path=db_path,
        limits=limits,
    )


def get_sqlite_batch_tool() -> Dict[str, Any]:
    """Return the sqlite_batch tool definition for the LLM."""
    return {
        "type": "function",
        "function": {
            "name": "sqlite_batch",
            "description": (
                "Durable SQLite memory for structured data. "
                "Provide `sql` as a single SQL string; separate multiple statements with semicolons. "
                "ESCAPE single quotes by DOUBLING them: 'O''Brien' (NOT backslash). "
                "grep_context_all/split_sections return STRING arrays: use json_each(...) then ctx.value directly, NOT json_extract. "
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "SQL to execute as a single string. Use semicolons to separate statements.",
                    },
                    "will_continue_work": {
                        "type": "boolean",
                        "description": "Set false to STOP when: all kanban cards are done AND you've sent your final report. Omitting defaults to continue. Always set false on your final tool call.",
                    },
                },
                "required": ["sql"],
            },
        },
    }


# ---------------------------------------------------------------------------
# Subprocess worker entry point
# ---------------------------------------------------------------------------

def _subprocess_worker_main() -> None:
    """Entry point when this module is run as a subprocess worker."""
    if len(sys.argv) < 2:
        result = {"status": "error", "message": "SQLite batch worker: missing payload file argument"}
        print(json.dumps(result))
        sys.exit(1)

    payload_file = sys.argv[1]
    try:
        with open(payload_file, 'r') as f:
            payload = json.load(f)
    except Exception as exc:
        result = {"status": "error", "message": f"SQLite batch worker: failed to load payload: {exc}"}
        print(json.dumps(result))
        sys.exit(1)

    # Reconstruct limits dataclass from dict
    limits_dict = payload.get("limits", {})
    limits = _SqliteBatchLimits(
        wall_timeout_seconds=limits_dict.get("wall_timeout_seconds", DEFAULT_SQLITE_BATCH_WALL_TIMEOUT_SECONDS),
        cpu_seconds=limits_dict.get("cpu_seconds", DEFAULT_SQLITE_BATCH_CPU_SECONDS),
        memory_mb=limits_dict.get("memory_mb", DEFAULT_SQLITE_BATCH_MEMORY_MB),
        query_timeout_seconds=limits_dict.get("query_timeout_seconds", DEFAULT_SQLITE_BATCH_WALL_TIMEOUT_SECONDS),
    )

    # Apply resource limits (CPU time, memory)
    _apply_resource_limits(limits)

    try:
        result = _execute_sqlite_batch_inner(
            agent_id=payload.get("agent_id", "unknown"),
            params=payload.get("params", {}),
            db_path=payload.get("db_path", ""),
            query_timeout_seconds=limits.query_timeout_seconds,
        )
    except Exception as exc:
        result = {"status": "error", "message": f"SQLite batch failed: {exc}"}

    # Output result as JSON to stdout
    print(json.dumps(result))


if __name__ == "__main__":
    _subprocess_worker_main()
