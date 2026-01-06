import logging
import re

import sqlglot
from sqlglot import exp
from sqlglot import errors as sqlglot_errors

logger = logging.getLogger(__name__)

_SQLGLOT_DIALECTS = (
    "postgres",
    "mysql",
    "tsql",
    "bigquery",
    "snowflake",
    "duckdb",
    "presto",
    "trino",
    "spark",
    "oracle",
    "hive",
)

_SQLGLOT_TRIGGER_FRAGMENTS = (
    "syntax error",
    "incomplete input",
    "unrecognized token",
    "no such function",
)

_MISSING_COLUMN_RE = re.compile(r"no such column:\s*([^\s]+)", re.IGNORECASE)


def build_sqlglot_candidates(sql: str, error_msg: str) -> list[tuple[str, list[str]]]:
    if not _should_attempt_sqlglot(error_msg):
        return []

    candidates: list[tuple[str, list[str]]] = []
    seen: set[str] = set()

    def _add_candidate(rewritten: str, fix: str | None) -> None:
        if not fix or _is_same_sql(sql, rewritten):
            return
        normalized = _normalize_sql(rewritten)
        if normalized in seen:
            return
        seen.add(normalized)
        candidates.append((rewritten, [fix]))

    for fixer in (
        _rewrite_with_clause_for_create_as,
        _rewrite_with_clause_for_dml,
        _rewrite_create_table_missing_as,
        _rewrite_delete_star,
        _rewrite_insert_value,
    ):
        rewritten, fix = fixer(sql)
        _add_candidate(rewritten, fix)

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


def build_cte_column_candidates(sql: str, error_msg: str) -> list[tuple[str, list[str]]]:
    corrected, fix = _autocorrect_missing_column_cte_chain(sql, error_msg)
    if fix and not _is_same_sql(sql, corrected):
        return [(corrected, [fix])]
    return []


def _should_attempt_sqlglot(error_msg: str) -> bool:
    if not error_msg:
        return False
    lowered = error_msg.lower()
    return any(fragment in lowered for fragment in _SQLGLOT_TRIGGER_FRAGMENTS)


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


def _autocorrect_missing_column_cte_chain(sql: str, error_msg: str) -> tuple[str, str | None]:
    match = _MISSING_COLUMN_RE.search(error_msg)
    if not match:
        return sql, None

    missing_raw = match.group(1).strip().strip("'\"")
    if not missing_raw:
        return sql, None

    qualifier, missing = _split_qualified_identifier(missing_raw)
    if not missing or qualifier:
        return sql, None

    try:
        expression = sqlglot.parse_one(sql, read="sqlite")
    except sqlglot_errors.ParseError:
        return sql, None

    with_expr = _get_with_expression(expression)
    if with_expr is None or not with_expr.expressions:
        return sql, None

    ctes = {
        cte.alias_or_name.lower(): cte
        for cte in with_expr.expressions
        if cte.alias_or_name
    }
    if not ctes:
        return sql, None

    root_select = _find_root_select(expression)
    if root_select is None:
        return sql, None

    target_name = _select_single_source_name(root_select)
    if not target_name:
        return sql, None

    target_key = target_name.lower()
    if target_key not in ctes:
        return sql, None

    chain: list[tuple[exp.Select, set[str], bool]] = []
    current_key = target_key

    while current_key in ctes:
        cte = ctes[current_key]
        if _cte_has_column_list(cte):
            break
        select_expr = _cte_select_expression(cte)
        if select_expr is None or not _is_simple_select(select_expr):
            break
        columns, has_star = _select_output_columns(select_expr)
        chain.append((select_expr, {col.lower() for col in columns}, has_star))
        source_name = _select_single_source_name(select_expr)
        if not source_name:
            break
        next_key = source_name.lower()
        if next_key == current_key or next_key not in ctes:
            break
        current_key = next_key

    if not chain:
        return sql, None

    missing_lower = missing.lower()
    source_idx = None
    for idx in range(len(chain) - 1, -1, -1):
        _, columns, _ = chain[idx]
        if missing_lower in columns:
            source_idx = idx
            break

    if source_idx is None or source_idx == 0:
        return sql, None

    changed = False
    for idx in range(source_idx - 1, -1, -1):
        select_expr, columns, has_star = chain[idx]
        if has_star or missing_lower in columns:
            continue
        select_expr.append("expressions", exp.column(missing))
        columns.add(missing_lower)
        changed = True

    if not changed:
        return sql, None

    rewritten = expression.sql(dialect="sqlite")
    return rewritten, f"propagated '{missing}' through CTE chain"


def _rewrite_with_clause_for_create_as(sql: str) -> tuple[str, str | None]:
    if not _starts_with_keyword(sql, "WITH"):
        return sql, None

    with_idx = _find_top_level_keyword(sql, "WITH", 0)
    create_idx = _find_top_level_keyword(sql, "CREATE", 0)
    if with_idx is None or create_idx is None or with_idx > create_idx:
        return sql, None

    with_bounds = _find_with_clause_bounds(sql, with_idx)
    if with_bounds is None:
        return sql, None
    with_start, with_end = with_bounds
    if with_end > create_idx:
        return sql, None

    as_idx = _find_top_level_keyword(sql, "AS", create_idx)
    if as_idx is None:
        return sql, None

    if _find_top_level_keyword(sql, "SELECT", as_idx) is None:
        return sql, None

    if not _is_create_as_target(sql[create_idx:as_idx]):
        return sql, None

    with_clause = sql[with_start:with_end].strip()
    create_prefix = sql[create_idx:as_idx].rstrip()
    select_suffix = sql[as_idx + 2 :].lstrip()

    if not with_clause or not create_prefix or not select_suffix:
        return sql, None

    rewritten = f"{create_prefix} AS {with_clause} {select_suffix}"
    return rewritten, "moved WITH clause after CREATE TABLE/VIEW AS"


def _rewrite_with_clause_for_dml(sql: str) -> tuple[str, str | None]:
    if not _starts_with_any_keyword(sql, ("INSERT", "UPDATE", "DELETE", "REPLACE")):
        return sql, None

    with_idx = _find_top_level_keyword(sql, "WITH", 0)
    if with_idx is None:
        return sql, None

    stmt_idx = _statement_start(sql)
    if stmt_idx is None or with_idx <= stmt_idx:
        return sql, None

    with_bounds = _find_with_clause_bounds(sql, with_idx)
    if with_bounds is None:
        return sql, None
    with_start, with_end = with_bounds

    before_with = sql[:with_start].rstrip()
    with_clause = sql[with_start:with_end].strip()
    after_with = sql[with_end:].lstrip()

    if not before_with or not with_clause or not after_with:
        return sql, None

    rewritten = f"{with_clause} {before_with} {after_with}"
    return rewritten, "moved WITH clause before DML statement"


def _rewrite_create_table_missing_as(sql: str) -> tuple[str, str | None]:
    create_idx = _find_top_level_keyword(sql, "CREATE", 0)
    if create_idx is None:
        return sql, None

    select_idx = _find_top_level_keyword(sql, "SELECT", create_idx)
    if select_idx is None:
        return sql, None

    as_idx = _find_top_level_keyword(sql, "AS", create_idx)
    if as_idx is not None and as_idx < select_idx:
        return sql, None

    if not _is_create_as_target(sql[create_idx:select_idx]):
        return sql, None

    if _has_top_level_paren(sql, create_idx, select_idx):
        return sql, None

    prefix = sql[:select_idx].rstrip()
    suffix = sql[select_idx:].lstrip()
    if not prefix or not suffix:
        return sql, None

    rewritten = f"{prefix} AS {suffix}"
    return rewritten, "added missing AS in CREATE TABLE AS"


def _rewrite_delete_star(sql: str) -> tuple[str, str | None]:
    delete_idx = _find_top_level_keyword(sql, "DELETE", 0)
    if delete_idx is None:
        return sql, None

    from_idx = _find_top_level_keyword(sql, "FROM", delete_idx)
    if from_idx is None:
        return sql, None

    star_idx = _skip_whitespace_and_comments(sql, delete_idx + len("DELETE"))
    if star_idx >= len(sql) or sql[star_idx] != "*":
        return sql, None

    if star_idx > from_idx:
        return sql, None

    rewritten = f"{sql[:delete_idx]}DELETE {sql[from_idx:]}"
    return rewritten, "removed '*' from DELETE"


def _rewrite_insert_value(sql: str) -> tuple[str, str | None]:
    insert_idx = _find_top_level_keyword(sql, "INSERT", 0)
    if insert_idx is None:
        return sql, None

    value_idx = _find_top_level_keyword(sql, "VALUE", insert_idx)
    if value_idx is None:
        return sql, None

    rewritten = f"{sql[:value_idx]}VALUES{sql[value_idx + len('VALUE'):]}"
    return rewritten, "VALUE -> VALUES"


def _split_qualified_identifier(identifier: str) -> tuple[str | None, str]:
    parts = [part for part in identifier.split(".") if part]
    if len(parts) > 1:
        return ".".join(parts[:-1]), parts[-1]
    return None, identifier


def _get_with_expression(expression: exp.Expression) -> exp.With | None:
    if isinstance(expression, exp.With):
        return expression
    with_expr = expression.args.get("with_")
    if isinstance(with_expr, exp.With):
        return with_expr
    return None


def _find_root_select(expression: exp.Expression) -> exp.Select | None:
    current = expression
    if isinstance(current, exp.With):
        current = current.this

    if isinstance(current, exp.Select):
        return current

    candidate = current.args.get("expression") or current.args.get("source")
    if isinstance(candidate, exp.Subquery):
        candidate = candidate.this
    if isinstance(candidate, exp.Select):
        return candidate

    return None


def _select_single_source_name(select_expr: exp.Select) -> str | None:
    if select_expr.args.get("joins"):
        return None
    from_expr = select_expr.args.get("from_")
    if from_expr is None:
        return None
    table_expr = from_expr.this
    if not isinstance(table_expr, exp.Table):
        return None
    return table_expr.name


def _cte_has_column_list(cte: exp.CTE) -> bool:
    alias_expr = cte.args.get("alias")
    if not isinstance(alias_expr, exp.TableAlias):
        return False
    columns = alias_expr.args.get("columns")
    return bool(columns)


def _cte_select_expression(cte: exp.CTE) -> exp.Select | None:
    select_expr = cte.this
    if isinstance(select_expr, exp.Subquery):
        select_expr = select_expr.this
    if isinstance(select_expr, exp.Select):
        return select_expr
    return None


def _is_simple_select(select_expr: exp.Select) -> bool:
    if select_expr.args.get("joins"):
        return False
    if select_expr.args.get("group") is not None:
        return False
    if select_expr.args.get("having") is not None:
        return False
    if select_expr.args.get("distinct") is not None:
        return False
    if select_expr.args.get("qualify") is not None:
        return False
    # Note: FROM clause is NOT required - a CTE can be SELECT 1 AS c1, 2 AS c2
    return True


def _select_output_columns(select_expr: exp.Select) -> tuple[list[str], bool]:
    columns: list[str] = []
    has_star = False
    for projection in select_expr.expressions:
        if isinstance(projection, exp.Star):
            has_star = True
            continue
        if isinstance(projection, exp.Column) and isinstance(projection.this, exp.Star):
            has_star = True
            continue
        alias = projection.alias_or_name
        if alias:
            columns.append(alias)
    return columns, has_star


def _starts_with_keyword(sql: str, keyword: str) -> bool:
    idx = _statement_start(sql)
    if idx is None:
        return False
    return _read_keyword(sql, idx) == keyword.upper()


def _starts_with_any_keyword(sql: str, keywords: tuple[str, ...]) -> bool:
    idx = _statement_start(sql)
    if idx is None:
        return False
    word = _read_keyword(sql, idx)
    return word in {kw.upper() for kw in keywords}


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


def _find_top_level_keyword(sql: str, keyword: str, start: int) -> int | None:
    keyword_upper = keyword.upper()
    length = len(sql)
    idx = start
    depth = 0

    while idx < length:
        idx, skipped = _skip_non_code(sql, idx)
        if skipped:
            continue

        ch = sql[idx]
        if ch == "(":
            depth += 1
        elif ch == ")" and depth > 0:
            depth -= 1

        if depth == 0 and sql[idx : idx + len(keyword_upper)].upper() == keyword_upper:
            before = sql[idx - 1] if idx > 0 else " "
            after_idx = idx + len(keyword_upper)
            after = sql[after_idx] if after_idx < length else " "
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


def _statement_start(sql: str) -> int | None:
    idx = _skip_whitespace_and_comments(sql, 0)
    if idx >= len(sql):
        return None
    return idx


def _skip_whitespace_and_comments(sql: str, idx: int) -> int:
    length = len(sql)
    while idx < length:
        ch = sql[idx]
        if ch.isspace():
            idx += 1
            continue
        if ch == "-" and idx + 1 < length and sql[idx + 1] == "-":
            idx = _skip_line_comment(sql, idx)
            continue
        if ch == "/" and idx + 1 < length and sql[idx + 1] == "*":
            idx = _skip_block_comment(sql, idx)
            continue
        break
    return idx


def _skip_non_code(sql: str, idx: int) -> tuple[int, bool]:
    length = len(sql)
    if idx >= length:
        return idx, False
    ch = sql[idx]

    if ch == "-" and idx + 1 < length and sql[idx + 1] == "-":
        return _skip_line_comment(sql, idx), True
    if ch == "/" and idx + 1 < length and sql[idx + 1] == "*":
        return _skip_block_comment(sql, idx), True
    if ch in ("'", '"', "`"):
        return _skip_quoted_literal(sql, idx, ch), True
    if ch == "[":
        return _skip_bracket_identifier(sql, idx), True
    return idx, False


def _skip_line_comment(sql: str, idx: int) -> int:
    length = len(sql)
    idx += 2
    while idx < length and sql[idx] != "\n":
        idx += 1
    return idx


def _skip_block_comment(sql: str, idx: int) -> int:
    length = len(sql)
    idx += 2
    while idx + 1 < length and not (sql[idx] == "*" and sql[idx + 1] == "/"):
        idx += 1
    return idx + 2 if idx + 1 < length else length


def _skip_quoted_literal(sql: str, idx: int, quote: str) -> int:
    length = len(sql)
    idx += 1
    while idx < length:
        if sql[idx] == quote:
            if idx + 1 < length and sql[idx + 1] == quote:
                idx += 2
                continue
            idx += 1
            break
        idx += 1
    return idx


def _skip_bracket_identifier(sql: str, idx: int) -> int:
    length = len(sql)
    idx += 1
    while idx < length and sql[idx] != "]":
        idx += 1
    return idx + 1 if idx < length else length


def _read_keyword(sql: str, idx: int) -> str | None:
    length = len(sql)
    if idx >= length or not sql[idx].isalpha():
        return None
    end = idx + 1
    while end < length and sql[end].isalpha():
        end += 1
    return sql[idx:end].upper()


def _find_with_clause_bounds(sql: str, with_idx: int) -> tuple[int, int] | None:
    idx = with_idx + len("WITH")
    length = len(sql)
    idx = _skip_whitespace_and_comments(sql, idx)
    if idx + len("RECURSIVE") <= length and sql[idx : idx + len("RECURSIVE")].upper() == "RECURSIVE":
        idx += len("RECURSIVE")
        idx = _skip_whitespace_and_comments(sql, idx)

    depth = 0
    saw_paren = False
    cte_finished = False

    while idx < length:
        idx, skipped = _skip_non_code(sql, idx)
        if skipped:
            continue

        if depth == 0 and cte_finished:
            next_idx = _skip_whitespace_and_comments(sql, idx)
            if next_idx >= length:
                return with_idx, next_idx
            if sql[next_idx] == ",":
                idx = next_idx + 1
                cte_finished = False
                continue
            return with_idx, next_idx

        ch = sql[idx]
        if ch == "(":
            depth += 1
            saw_paren = True
        elif ch == ")" and depth > 0:
            depth -= 1
            if depth == 0 and saw_paren:
                cte_finished = True
        idx += 1

    return None


def _has_top_level_paren(sql: str, start: int, end: int) -> bool:
    idx = start
    depth = 0
    while idx < end:
        idx, skipped = _skip_non_code(sql, idx)
        if skipped:
            continue
        ch = sql[idx]
        if ch == "(":
            depth += 1
            if depth == 1:
                return True
        elif ch == ")" and depth > 0:
            depth -= 1
        idx += 1
    return False
