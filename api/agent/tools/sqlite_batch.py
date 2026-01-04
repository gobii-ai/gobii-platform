"""
SQLite batch tool for persistent agents.

Simplified multi-query executor aligned with sqlite_query.
"""

import json
import logging
import os
import re
import sqlite3
from typing import Any, Dict, List, Optional

import sqlparse

# Context protection limits
MAX_RESULT_ROWS = 100  # Hard cap on rows returned
MAX_RESULT_BYTES = 8000  # ~2K tokens worth of result data
WARN_RESULT_ROWS = 50  # Warn if exceeding this
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
            corrections.append("== → =")

    # && to AND (outside strings)
    if '&&' in sql:
        new_sql = re.sub(r'\s*&&\s*', ' AND ', sql)
        if new_sql != sql:
            sql = new_sql
            corrections.append("&& → AND")

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
            corrections.append("IF() → IIF()")

    # ILIKE -> LIKE (PostgreSQL case-insensitive like)
    # Note: SQLite LIKE is case-insensitive for ASCII by default
    if re.search(r'\bILIKE\b', sql, re.IGNORECASE):
        sql = re.sub(r'\bILIKE\b', 'LIKE', sql, flags=re.IGNORECASE)
        corrections.append("ILIKE → LIKE")

    # NVL2(x, y, z) -> IIF(x IS NOT NULL, y, z) - Oracle style
    nvl2_match = re.search(r'\bNVL2\s*\(\s*([^,]+)\s*,\s*([^,]+)\s*,\s*([^)]+)\s*\)', sql, re.IGNORECASE)
    if nvl2_match:
        replacement = f"IIF({nvl2_match.group(1)} IS NOT NULL, {nvl2_match.group(2)}, {nvl2_match.group(3)})"
        sql = sql[:nvl2_match.start()] + replacement + sql[nvl2_match.end():]
        corrections.append("NVL2() → IIF()")

    # CONCAT(a, b) -> (a || b) - MySQL/PostgreSQL style
    # Handle simple 2-arg case
    concat_pattern = r'\bCONCAT\s*\(\s*([^,()]+)\s*,\s*([^,()]+)\s*\)'
    while re.search(concat_pattern, sql, re.IGNORECASE):
        sql = re.sub(concat_pattern, r'(\1 || \2)', sql, count=1, flags=re.IGNORECASE)
        if "CONCAT" not in [c.split()[0] for c in corrections]:
            corrections.append("CONCAT() → ||")

    # STRING_AGG(col, sep) -> GROUP_CONCAT(col, sep) - PostgreSQL style
    if re.search(r'\bSTRING_AGG\s*\(', sql, re.IGNORECASE):
        sql = re.sub(r'\bSTRING_AGG\s*\(', 'GROUP_CONCAT(', sql, flags=re.IGNORECASE)
        corrections.append("STRING_AGG() → GROUP_CONCAT()")

    # ARRAY_AGG -> GROUP_CONCAT (PostgreSQL)
    if re.search(r'\bARRAY_AGG\s*\(', sql, re.IGNORECASE):
        sql = re.sub(r'\bARRAY_AGG\s*\(', 'GROUP_CONCAT(', sql, flags=re.IGNORECASE)
        corrections.append("ARRAY_AGG() → GROUP_CONCAT()")

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
        corrections.append(f"TOP {n} → LIMIT {n}")

    # TRUNCATE TABLE x -> DELETE FROM x (SQLite doesn't have TRUNCATE)
    truncate_match = re.search(r'\bTRUNCATE\s+(?:TABLE\s+)?(\w+)', sql, re.IGNORECASE)
    if truncate_match:
        table = truncate_match.group(1)
        sql = re.sub(r'\bTRUNCATE\s+(?:TABLE\s+)?\w+', f'DELETE FROM {table}', sql, flags=re.IGNORECASE)
        corrections.append("TRUNCATE → DELETE FROM")

    # :: type cast -> CAST(x AS type) (PostgreSQL style)
    cast_match = re.search(r'(\w+)::(\w+)', sql)
    if cast_match:
        sql = re.sub(r'(\w+)::(\w+)', r'CAST(\1 AS \2)', sql)
        corrections.append(":: → CAST()")

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
            return sql, f"'{missing}' → '{variant}'"

    return sql, None


def _fix_singular_plural_columns(sql: str, error_msg: str) -> tuple[str, str | None]:
    """Fix singular/plural column name mismatches based on error message."""
    match = re.search(r'no such column:\s*(\w+)', error_msg, re.IGNORECASE)
    if not match:
        return sql, None

    missing = match.group(1)
    aliases = _extract_select_aliases(sql)

    # Check for singular/plural variants among aliases
    variants = []
    if missing.endswith('s'):
        variants.append(missing[:-1])
    variants.append(missing + 's')

    for variant in variants:
        if variant.lower() in [a.lower() for a in aliases]:
            pattern = rf'\b{re.escape(missing)}\b'
            sql = re.sub(pattern, variant, sql, flags=re.IGNORECASE)
            return sql, f"'{missing}' → '{variant}'"

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

    # Error-driven fixes (only if we have an error message)
    if error_msg:
        sql, fix = _fix_singular_plural_tables(sql, error_msg)
        if fix:
            corrections.append(fix)

        sql, fix = _fix_singular_plural_columns(sql, error_msg)
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
        return corrected, f"'{column_name}'→'{main_alias}.{column_name}'"
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
                corrections.append(f"'{ref}'→'{cte_name}'")
                break

    return sql, corrections


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
        return " FIX: Check SQL syntax - common issues: missing quotes, commas, or parentheses."
    if "wrong number of arguments" in error_lower:
        return " FIX: Check parentheses in nested function calls - a ')' is likely misplaced."
    if "unique constraint" in error_lower:
        return " FIX: Use INSERT OR REPLACE or INSERT OR IGNORE to handle duplicate keys."
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
        warning = f" ⚠️ TRUNCATED: {total_rows} rows → {MAX_RESULT_ROWS}. Add LIMIT to your query."

    # Check byte size
    try:
        result_bytes = len(json.dumps(rows, default=str).encode('utf-8'))
        if result_bytes > MAX_RESULT_BYTES:
            # Progressively reduce until under limit
            while len(rows) > 10 and len(json.dumps(rows, default=str).encode('utf-8')) > MAX_RESULT_BYTES:
                rows = rows[:len(rows) // 2]
            warning = f" ⚠️ TRUNCATED to {len(rows)} rows (size limit). Use LIMIT and specific columns."
    except Exception:
        pass

    # Warn about missing LIMIT even if not truncated
    if not warning and total_rows > WARN_RESULT_ROWS and not has_limit:
        warning = f" ⚠️ Large result ({total_rows} rows). Consider adding LIMIT for efficiency."

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


def execute_sqlite_batch(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
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

    db_path = _sqlite_db_path_var.get(None)
    if not db_path:
        return {"status": "error", "message": "SQLite DB path unavailable"}

    conn: Optional[sqlite3.Connection] = None
    results: List[Dict[str, Any]] = []
    had_error = False
    error_message = ""
    only_write_queries = True
    all_corrections: List[str] = []

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

            original_query = query  # Keep original for error reporting

            # Apply pre-execution fixes (LLM artifacts, dialect fixes, etc.)
            query, pre_fixes = _apply_all_sql_fixes(query)
            if pre_fixes:
                all_corrections.extend(pre_fixes)

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
                    original_count = len(rows)
                    rows, limit_warning = _enforce_result_limits(rows, query)
                    results.append({
                        "result": rows,
                        "message": f"Query {idx} returned {original_count} rows.{limit_warning}",
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
            except Exception as orig_exc:
                orig_exc_str = str(orig_exc)
                conn.rollback()

                # Attempt error-driven auto-corrections
                corrected_query = query
                query_corrections: list[str] = []

                # 1. CTE typos (e.g., 'comment' -> 'comments')
                corrected_query, cte_fixes = _autocorrect_cte_typos(corrected_query)
                query_corrections.extend(cte_fixes)

                # 2. Ambiguous column names (e.g., 'species' -> 'iris.species')
                ambig_match = re.search(r'ambiguous column name:\s*(\w+)', orig_exc_str, re.IGNORECASE)
                if ambig_match:
                    ambig_col = ambig_match.group(1)
                    corrected_query, ambig_fix = _autocorrect_ambiguous_column(corrected_query, ambig_col)
                    if ambig_fix:
                        query_corrections.append(ambig_fix)

                # 3. Singular/plural table and column mismatches
                corrected_query, table_fix = _fix_singular_plural_tables(corrected_query, orig_exc_str)
                if table_fix:
                    query_corrections.append(table_fix)
                corrected_query, col_fix = _fix_singular_plural_columns(corrected_query, orig_exc_str)
                if col_fix:
                    query_corrections.append(col_fix)

                # If we made corrections, retry
                if query_corrections and corrected_query != query:
                    try:
                        start_query_timer(conn)
                        cur.execute(corrected_query)
                        all_corrections.extend(query_corrections)
                        if cur.description is not None:
                            columns = [col[0] for col in cur.description]
                            rows = [dict(zip(columns, row)) for row in cur.fetchall()]
                            original_count = len(rows)
                            rows, limit_warning = _enforce_result_limits(rows, corrected_query)
                            results.append({
                                "result": rows,
                                "message": f"Query {idx} returned {original_count} rows.{limit_warning}",
                            })
                            only_write_queries = False
                        else:
                            affected = cur.rowcount if cur.rowcount is not None else 0
                            results.append({"message": f"Query {idx} affected {max(0, affected)} rows."})
                        conn.commit()
                        continue  # Success after retry, move to next query
                    except Exception:
                        conn.rollback()
                        # Retry failed - fall through to report ORIGINAL error
                    finally:
                        stop_query_timer(conn)

                # No auto-correction worked, report original error
                had_error = True
                hint = _get_error_hint(orig_exc_str, original_query)
                error_message = f"Query {idx} failed: {orig_exc}{hint}"
                break
            finally:
                stop_query_timer(conn)

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
                msg = f"⚠️ AUTO-CORRECTED: {', '.join(all_corrections)}. Write correct SQL next time. " + msg

        response: Dict[str, Any] = {
            "status": "error" if had_error else "ok",
            "results": results,
            "db_size_mb": round(db_size_mb, 2),
            "message": msg,
        }

        if not had_error and will_continue_work is False and has_user_facing_message:
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
                "Provide `sql` as a single SQL string; separate multiple statements with semicolons. "
                "REMEMBER TO PROPERLY ESCAPE STRINGS IN SQL STATEMENTS. "
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
                        "description": "Set false when no immediate follow-up work is needed; enables auto-sleep.",
                    },
                },
                "required": ["sql"],
            },
        },
    }
