"""Shared helpers for SQLite agent tools."""

from __future__ import annotations

import re


WRITE_LEADING_KEYWORDS = {
    "INSERT",
    "UPDATE",
    "DELETE",
    "REPLACE",
    "CREATE",
    "ALTER",
    "DROP",
}

# precompiled regex to grab the leading alphabetic token
_LEADING_WORD_RE = re.compile(r"([A-Z]+)")


def _strip_comments_and_literals(sql: str) -> str:
    """Return *sql* with comments removed and string/identifier literals neutralised.

    We keep positional spacing by replacing skipped regions with spaces, which keeps
    subsequent parsing simple while avoiding false positives inside quotes.
    """

    result: list[str] = []
    i = 0
    length = len(sql)

    while i < length:
        ch = sql[i]

        # Handle line comments
        if ch == "-" and i + 1 < length and sql[i + 1] == "-":
            i += 2
            while i < length and sql[i] != "\n":
                i += 1
            continue

        # Handle block comments
        if ch == "/" and i + 1 < length and sql[i + 1] == "*":
            i += 2
            while i + 1 < length and not (sql[i] == "*" and sql[i + 1] == "/"):
                i += 1
            # Skip the closing */ if present
            i = i + 2 if i + 1 < length else length
            continue

        # Handle quoted strings (single and double quotes)
        if ch in {'"', "'"}:
            quote = ch
            result.append(" ")
            i += 1
            while i < length:
                curr = sql[i]
                if curr == quote:
                    # Handle escaped quotes represented by doubled characters
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


def _lstrip_spaces(text: str) -> str:
    """Left-strip ASCII whitespace (faster than regex for this hot path)."""

    idx = 0
    length = len(text)
    while idx < length and text[idx].isspace():
        idx += 1
    return text[idx:]


def _skip_leading_with_clause(sql_upper: str) -> str:
    """Best-effort removal of a leading WITH clause.

    The input *must* already be upper-cased and stripped of comments/literals.
    We intentionally keep this lightweight; if parsing fails we return the
    original string so callers fall back to conservative behaviour.
    """

    if not sql_upper.startswith("WITH"):
        return sql_upper

    idx = 4  # len("WITH")
    length = len(sql_upper)

    # Skip optional RECURSIVE keyword
    while idx < length and sql_upper[idx].isspace():
        idx += 1
    if sql_upper.startswith("RECURSIVE", idx):
        idx += len("RECURSIVE")
        while idx < length and sql_upper[idx].isspace():
            idx += 1

    depth = 0
    cte_finished = False
    saw_paren = False

    while idx < length:
        ch = sql_upper[idx]

        if depth == 0 and cte_finished:
            if ch == ',':
                cte_finished = False
                idx += 1
                continue
            if ch.isspace():
                idx += 1
                continue
            return sql_upper[idx:]

        if ch == '(':
            depth += 1
            saw_paren = True
        elif ch == ')':
            if depth > 0:
                depth -= 1
            if depth == 0 and saw_paren:
                cte_finished = True
        idx += 1

    # Parsing failed – fall back to original to stay conservative
    return sql_upper


def is_write_statement(sql: str) -> bool:
    """Return True if *sql* clearly performs mutations without returning rows.

    The heuristic intentionally errs on the side of False (i.e. not auto-sleep)
    when we cannot confidently classify the statement.
    """

    if not sql:
        return False

    stripped = _strip_comments_and_literals(sql)
    stripped = _lstrip_spaces(stripped)
    if not stripped:
        return False

    upper = stripped.upper()

    # Drop leading WITH clause if we can find it
    if upper.startswith("WITH"):
        remainder = _skip_leading_with_clause(upper)
        if remainder != upper:
            upper = _lstrip_spaces(remainder)
        else:
            upper = _lstrip_spaces(upper)

    upper = _lstrip_spaces(upper)
    if not upper:
        return False

    match = _LEADING_WORD_RE.match(upper)
    if not match:
        return False

    keyword = match.group(1)
    if keyword not in WRITE_LEADING_KEYWORDS:
        return False

    # Statements with RETURNING clauses produce result sets and require inspection
    if "RETURNING" in upper:
        return False

    return True
