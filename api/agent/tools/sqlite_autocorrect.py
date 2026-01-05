import logging
import re

import sqlglot
from sqlglot import errors as sqlglot_errors

from .sqlite_helpers import _strip_comments_and_literals

logger = logging.getLogger(__name__)

_SQLGLOT_DIALECTS = (
    "sqlite",
    "postgres",
    "mysql",
    "tsql",
    "bigquery",
    "snowflake",
)

_SYNTAX_ERROR_FRAGMENTS = (
    "syntax error",
    "incomplete input",
    "unrecognized token",
)


def build_sqlglot_candidates(sql: str, error_msg: str) -> list[tuple[str, list[str]]]:
    if not _should_attempt_sqlglot(error_msg):
        return []

    candidates: list[tuple[str, list[str]]] = []
    seen: set[str] = set()

    rewritten, fix = _rewrite_with_clause_for_create_as(sql)
    if fix and not _is_same_sql(sql, rewritten):
        normalized = _normalize_sql(rewritten)
        if normalized not in seen:
            seen.add(normalized)
            candidates.append((rewritten, [fix]))

    for dialect in _SQLGLOT_DIALECTS:
        rewritten = _transpile_sqlglot(sql, dialect)
        if rewritten is None or _is_same_sql(sql, rewritten):
            continue
        normalized = _normalize_sql(rewritten)
        if normalized in seen:
            continue
        seen.add(normalized)
        candidates.append((rewritten, [f"sqlglot:{dialect}->sqlite"]))

    return candidates


def _should_attempt_sqlglot(error_msg: str) -> bool:
    if not error_msg:
        return False
    lowered = error_msg.lower()
    return any(fragment in lowered for fragment in _SYNTAX_ERROR_FRAGMENTS)


def _transpile_sqlglot(sql: str, read_dialect: str) -> str | None:
    try:
        transpiled = sqlglot.transpile(sql, read=read_dialect, write="sqlite")
    except sqlglot_errors.ParseError:
        return None
    except Exception:
        logger.debug("sqlglot transpile failed for dialect=%s", read_dialect, exc_info=True)
        return None

    if not transpiled:
        return None
    return transpiled[0]


def _rewrite_with_clause_for_create_as(sql: str) -> tuple[str, str | None]:
    cleaned = _strip_comments_and_literals(sql)
    if not _starts_with_keyword(cleaned, "WITH"):
        return sql, None

    create_idx = _find_top_level_keyword(cleaned, "CREATE", 0)
    if create_idx is None:
        return sql, None

    as_idx = _find_top_level_keyword(cleaned, "AS", create_idx)
    if as_idx is None:
        return sql, None

    if _find_top_level_keyword(cleaned, "SELECT", as_idx) is None:
        return sql, None

    if not _is_create_as_target(cleaned[create_idx:as_idx]):
        return sql, None

    with_clause = sql[:create_idx].strip()
    create_prefix = sql[create_idx:as_idx].rstrip()
    select_suffix = sql[as_idx + 2 :].lstrip()

    if not with_clause or not create_prefix or not select_suffix:
        return sql, None

    rewritten = f"{create_prefix} AS {with_clause} {select_suffix}"
    return rewritten, "moved WITH clause after CREATE TABLE/VIEW AS"


def _starts_with_keyword(cleaned: str, keyword: str) -> bool:
    idx = 0
    length = len(cleaned)
    while idx < length and cleaned[idx].isspace():
        idx += 1
    if idx >= length:
        return False
    return cleaned[idx : idx + len(keyword)].upper() == keyword.upper()


def _is_create_as_target(segment: str) -> bool:
    upper = segment.upper()
    patterns = (
        r"\bCREATE\s+TABLE\b",
        r"\bCREATE\s+TEMP\s+TABLE\b",
        r"\bCREATE\s+TEMPORARY\s+TABLE\b",
        r"\bCREATE\s+VIEW\b",
        r"\bCREATE\s+TEMP\s+VIEW\b",
        r"\bCREATE\s+TEMPORARY\s+VIEW\b",
    )
    return any(re.search(pattern, upper) for pattern in patterns)


def _find_top_level_keyword(cleaned: str, keyword: str, start: int) -> int | None:
    keyword_upper = keyword.upper()
    length = len(cleaned)
    idx = start
    depth = 0

    while idx < length:
        ch = cleaned[idx]
        if ch == "(":
            depth += 1
        elif ch == ")" and depth > 0:
            depth -= 1

        if depth == 0 and cleaned[idx : idx + len(keyword_upper)].upper() == keyword_upper:
            before = cleaned[idx - 1] if idx > 0 else " "
            after_idx = idx + len(keyword_upper)
            after = cleaned[after_idx] if after_idx < length else " "
            if not _is_identifier_char(before) and not _is_identifier_char(after):
                return idx

        idx += 1

    return None


def _is_identifier_char(char: str) -> bool:
    return char.isalnum() or char == "_"


def _normalize_sql(sql: str) -> str:
    collapsed = re.sub(r"\s+", " ", sql.strip())
    return collapsed.rstrip(";")


def _is_same_sql(left: str, right: str) -> bool:
    return _normalize_sql(left) == _normalize_sql(right)
