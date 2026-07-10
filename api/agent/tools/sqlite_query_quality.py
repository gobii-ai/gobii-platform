import re
from collections import Counter
from types import SimpleNamespace
from typing import Iterable

import sqlparse


TOOL_RESULTS_SOURCE = r'(?:(?:["`\[]?main["`\]]?)\s*\.\s*)?["`\[]?__tool_results["`\]]?(?!\w)'
TOOL_RESULTS_RE = re.compile(rf'\b(?:from|join)\s+{TOOL_RESULTS_SOURCE}', re.I)
TOOL_RESULTS_SELECT_RE = re.compile(rf'\bselect\b(?P<select>.*?)\bfrom\s+{TOOL_RESULTS_SOURCE}', re.I | re.S)
RESULT_ID_EQ_RE = re.compile(r"\b(?:[a-z_]\w*\.)?result_id\s*=\s*(['\"])(?P<id>[^'\"]+)\1", re.I)
RESULT_ID_IN_RE = re.compile(r"\b(?:[a-z_]\w*\.)?result_id\s+in\s*\((?P<values>[^)]*)\)", re.I | re.S)
CREATE_TABLE_RE = re.compile(r'\bcreate\s+(?:temp(?:orary)?\s+)?table\s+(?:if\s+not\s+exists\s+)?"?(?P<name>[a-z_]\w*)"?', re.I)
INSERT_INTO_RE = re.compile(r'\binsert\s+(?:or\s+\w+\s+)?into\s+"?(?P<name>[a-z_]\w*)"?', re.I)
MANUAL_INSERT_VALUES_RE = re.compile(
    r'\binsert\s+(?:or\s+\w+\s+)?into\s+"?(?P<name>[a-z_]\w*)"?'
    r'\s*(?:\([^;]*?\))?\s*values\s*\(', re.I,
)
MANUAL_CREATE_VALUES_RE = re.compile(
    r'\bcreate\s+(?:temp(?:orary)?\s+)?table\s+(?:if\s+not\s+exists\s+)?'
    r'"?(?P<name>[a-z_]\w*)"?\s+as\s+(?:select\s+\*\s+from\s+)?'
    r'\(\s*values\s*\(', re.I,
)
JSON_FUNCTION_RE = re.compile(r"\bjson_(?:extract|each)\s*\(", re.I)
JSON_CONTAINER_PATH_RE = re.compile(r"^\$\.(?:results?|items?|records?|entries|rows)$", re.I)
RESULT_PAYLOAD_RE = re.compile(r'\b(?:[a-z_]\w*\.)?["`\[]?result_(?:json|text)["`\]]?(?!\w)', re.I)
JSON_ARROW_RE = re.compile(r"->>?\s*(['\"])?\$?(?:[.\[]|[a-z_]\w*)", re.I)
PAYLOAD_ALIAS_RE = re.compile(
    r'\b(?:[a-z_]\w*\.)?["`\[]?result_(?:json|text)["`\]]?\s+as\s+["`\[]?(?P<alias>[a-z_]\w*)["`\]]?', re.I,
)
SHAPING_FUNCTION_NAMES = frozenset({
    "csv_headers", "csv_parse", "grep_context_all", "json_each",
    "json_extract", "parse_date", "parse_number", "split_sections",
})
SQL_STRING_LITERAL_RE = re.compile(r"'((?:''|[^'])*)'")
LIMIT_ONE_OFFSET_RE = re.compile(r"\blimit\s+1\s+offset\s+(?:\d+|\?)", re.I)
MAX_BOUNDED_PAYLOAD_PREVIEW_CHARS = 4000

COUNT_FIELDS = (
    "statement_count", "tool_result_statement_count", "single_result_id_filters",
    "direct_result_text_fetches", "aggregate_tool_result_queries", "smart_tool_result_queries",
    "aggregate_payload_queries", "unshaped_result_payload_queries",
    "unshaped_multi_result_payload_queries",
    "large_unshaped_multi_result_payload_queries",
    "uses_json_functions", "uses_cte", "uses_join", "uses_group_by", "uses_window",
    "uses_order_by", "single_row_offset_queries", "creates_working_table", "reads_working_table",
)
INTERNAL_TABLE_NAMES = {
    "__agent_config", "__agent_skills", "__contacts", "__files",
    "__kanban_cards", "__messages", "__tool_results",
}


def summarize_sqlite_tool_result_calls(tool_calls: Iterable[object]):
    sql_values = []
    sqlite_call_count = 0
    for call in tool_calls:
        params = getattr(call, "tool_params", None) or {}
        if getattr(call, "tool_name", None) == "sqlite_batch" and isinstance(params, dict):
            sqlite_call_count += 1
            sql_values.extend(_sql_values_from_params(params))
    return summarize_sqlite_tool_result_sql(sql_values, sqlite_call_count=sqlite_call_count)


def summarize_sqlite_tool_result_sql(sql_values: Iterable[str], *, sqlite_call_count: int | None = None):
    sql_list = [str(sql or "") for sql in sql_values]
    statements = [
        statement.strip()
        for sql in sql_list
        for statement in sqlparse.split(sql or "")
        if statement.strip()
    ]
    counts: Counter[str] = Counter()
    created_tables: list[str] = []
    working_sources: set[str] = set()
    manual_value_tables: list[str] = []
    manual_literal_rowsets = 0
    direct_fetch_keys: list[str] = []

    for statement in statements:
        code = sqlparse.format(statement, strip_comments=True)
        lowered = _normalize(code)
        if not lowered:
            continue
        counts["statement_count"] += 1
        mentions = bool(TOOL_RESULTS_RE.search(code))
        eq_count = len(RESULT_ID_EQ_RE.findall(code))
        in_count = _result_id_in_count(code)
        single_result_filter = mentions and eq_count == 1 and in_count == 0
        direct_fetch = single_result_filter and _directly_selects_result_payload(code)
        aggregate = mentions and not direct_fetch and (in_count > 1 or eq_count != 1)
        counts.update(
            uses_json_functions=bool(JSON_FUNCTION_RE.search(code)),
            uses_cte=lowered.startswith("with ") or " with " in lowered,
            uses_join=" join " in lowered,
            uses_group_by=" group by " in lowered,
            uses_window=" over (" in lowered,
            uses_order_by=" order by " in lowered,
        )
        counts["tool_result_statement_count"] += int(mentions)
        counts["single_result_id_filters"] += int(single_result_filter)
        counts["direct_result_text_fetches"] += int(direct_fetch)
        counts["aggregate_tool_result_queries"] += int(aggregate)
        reads_payload = mentions and bool(RESULT_PAYLOAD_RE.search(code))
        shapes_payload = reads_payload and _has_structured_payload_shaping(code)
        projects_raw_payload, raw_preview_chars = _raw_result_payload_projection(code)
        unshaped_payload = (
            projects_raw_payload or _projects_json_container(code, {"result_json", "result_text"})
        ) and not shapes_payload
        counts["smart_tool_result_queries"] += int(aggregate and reads_payload and shapes_payload)
        counts["aggregate_payload_queries"] += int(aggregate and (shapes_payload or projects_raw_payload))
        counts["unshaped_result_payload_queries"] += int(unshaped_payload)
        counts["unshaped_multi_result_payload_queries"] += int(aggregate and unshaped_payload)
        counts["large_unshaped_multi_result_payload_queries"] += int(
            aggregate and unshaped_payload and (
                raw_preview_chars is None or raw_preview_chars > MAX_BOUNDED_PAYLOAD_PREVIEW_CHARS
            )
        )
        counts["single_row_offset_queries"] += int(
            mentions and reads_payload and bool(LIMIT_ONE_OFFSET_RE.search(code))
        )
        if direct_fetch:
            direct_fetch_keys.append(_direct_fetch_key(statement))
        if table := _created_table_name(statement):
            created_tables.append(table)
            if mentions and reads_payload and shapes_payload:
                working_sources.add(table)
        if table := _manual_values_table_name(code):
            manual_value_tables.append(table)
        manual_literal_rowsets += int(bool(_manual_literal_rowset_values(code)))
        if mentions and reads_payload and shapes_payload and (table := _inserted_table_name(statement)):
            working_sources.add(table)

    created_unique = tuple(dict.fromkeys(created_tables))
    manual_values_unique = tuple(dict.fromkeys(manual_value_tables))
    counts["creates_working_table"] = sum(1 for table in created_unique if table in working_sources)
    counts["reads_working_table"] = sum(
        1
        for table in created_unique
        if table in working_sources and any(_reads_table(stmt, table) for stmt in statements)
    )
    duplicate_fetches = sum(count - 1 for count in Counter(direct_fetch_keys).values() if count > 1)
    return SimpleNamespace(
        sqlite_call_count=sqlite_call_count if sqlite_call_count is not None else len(sql_list),
        duplicate_direct_fetches=duplicate_fetches,
        working_table_names=created_unique,
        manual_values_table_names=manual_values_unique,
        manual_values_working_tables=sum(1 for table in manual_values_unique if table in created_unique),
        manual_literal_rowsets=manual_literal_rowsets,
        **{field: counts[field] for field in COUNT_FIELDS},
    )


def manual_literal_rowset_literals(sql_values: Iterable[str]) -> tuple[str, ...]:
    statements = (
        sqlparse.format(statement, strip_comments=True)
        for sql in sql_values for statement in sqlparse.split(str(sql or ""))
    )
    return tuple(dict.fromkeys(value for statement in statements for value in _manual_literal_rowset_values(statement)))


def build_tool_result_query_advisories(
    sql_values: Iterable[str],
    *,
    available_tool_result_rows: int,
) -> list[SimpleNamespace]:
    if available_tool_result_rows < 2:
        return []
    advisories = []
    summary = summarize_sqlite_tool_result_sql(sql_values)
    if summary.manual_values_working_tables or summary.manual_literal_rowsets:
        advisories.append(
            _advisory(
                "manual_working_table_from_visible_results",
                "If these rows duplicate visible tool output, derive them from __tool_results with json_extract/json_each or CREATE TABLE ... AS SELECT instead of transcribing them.",
                severity="info",
            )
        )
    if (
        summary.large_unshaped_multi_result_payload_queries
        or summary.unshaped_multi_result_payload_queries >= 2
    ):
        advisories.append(
            _advisory(
                "unshaped_multi_result_payload",
                "This batch projects too much raw payload text across multiple tool results. Shape payloads set-wise with json_extract/json_each, grep_context_all, joins, or aggregates. One bounded head preview (up to 4000 characters per row) is reasonable; avoid unbounded, oversized, or repeated result_json/result_text previews.",
            )
        )
    elif summary.unshaped_multi_result_payload_queries:
        advisories.append(
            _advisory(
                "bounded_multi_result_preview",
                "A bounded preview is useful for inspection. Before synthesizing, comparing, or deduplicating, run one shaped set-wise query with json_extract/json_each, grep_context_all, joins, grouping, or aggregates; do not merge preview blobs mentally.",
                severity="info",
            )
        )
    if summary.direct_result_text_fetches >= 2 or summary.duplicate_direct_fetches:
        advisories.append(
            _advisory("tool_result_blob_fetch_loop", "You are fetching full result_text blobs one result at a time. Combine prior tool outputs in one shaped query using WHERE result_id IN (...), CTEs, json_extract/json_each, joins, aggregation, or CREATE TABLE ... AS SELECT for a working table.")
        )
    elif summary.direct_result_text_fetches:
        advisories.append(
            _advisory(
                "single_tool_result_blob_fetch",
                "This fetched one full result payload while multiple tool results are available. For multi-source synthesis, query needed rows together; use a bounded projection for previews.",
                severity="info",
            )
        )
    return advisories


def _advisory(code: str, message: str, *, severity: str = "warning") -> SimpleNamespace:
    return SimpleNamespace(code=code, message=message, severity=severity)


def _sql_values_from_params(params: dict) -> list[str]:
    value = params.get("sql") or params.get("query") or params.get("queries")
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item or "").strip()]
    if value:
        return [str(value)]
    return []


def _normalize(statement: str) -> str:
    return re.sub(r"\s+", " ", statement or "").strip().lower()


def _result_id_in_count(statement: str) -> int:
    counts = [len([v for v in match.group("values").split(",") if v.strip()]) for match in RESULT_ID_IN_RE.finditer(statement or "")]
    return max(counts or [0])


def _has_structured_payload_shaping(statement: str) -> bool:
    payload_names = {"result_json", "result_text"}
    payload_names.update(match.group("alias").lower() for match in PAYLOAD_ALIAS_RE.finditer(statement))

    for function_name, args in _sql_function_calls(statement):
        if not args or not _expression_reads_payload(args[0], payload_names):
            continue
        if function_name == "json_extract":
            path = args[1].strip().strip("'\"") if len(args) >= 2 else "$"
            if path == "$" or JSON_CONTAINER_PATH_RE.fullmatch(path):
                continue
        return True

    return any(
        re.search(
            rf'(?<!\w)["`\[]?{re.escape(payload_name)}["`\]]?(?!\w)\s*{JSON_ARROW_RE.pattern}',
            statement,
            re.I,
        )
        for payload_name in payload_names
    )


def _projects_json_container(statement: str, payload_names: set[str]) -> bool:
    return any(
        function_name == "json_extract"
        and len(args) >= 2
        and _expression_reads_payload(args[0], payload_names)
        and JSON_CONTAINER_PATH_RE.fullmatch(args[1].strip().strip("'\""))
        for function_name, args in _sql_function_calls(statement)
    )


def _expression_reads_payload(expression: str, payload_names: set[str]) -> bool:
    return any(
        re.search(rf'(?<!\w)["`\[]?{re.escape(name)}["`\]]?(?!\w)', expression, re.I)
        for name in payload_names
    )


def _sql_function_calls(statement: str) -> Iterable[tuple[str, list[str]]]:
    names = "|".join(sorted(SHAPING_FUNCTION_NAMES, key=len, reverse=True))
    for match in re.finditer(rf"\b(?P<name>{names})\s*\(", statement, re.I):
        args = _parse_sql_call_arguments(statement, match.end() - 1)
        if args is not None:
            yield match.group("name").lower(), args


def _parse_sql_call_arguments(statement: str, open_paren: int) -> list[str] | None:
    depth = 1
    quote = ""
    start = open_paren + 1
    args: list[str] = []
    index = start
    while index < len(statement):
        char = statement[index]
        if quote:
            if char == quote:
                if index + 1 < len(statement) and statement[index + 1] == quote:
                    index += 1
                else:
                    quote = ""
        elif char in "'\"":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                args.append(statement[start:index].strip())
                return args
        elif char == "," and depth == 1:
            args.append(statement[start:index].strip())
            start = index + 1
        index += 1
    return None


def _directly_selects_result_payload(statement: str) -> bool:
    match = TOOL_RESULTS_SELECT_RE.search(statement or "")
    if not match:
        return False
    for field in match.group("select").split(","):
        cleaned = re.sub(r"\s+as\s+\"?[a-z_]\w*\"?$", "", field.strip(), flags=re.I).strip('"')
        if (
            cleaned == "*"
            or cleaned.endswith(".*")
            or re.fullmatch(r'(?:[a-z_]\w*\.)?["`\[]?result_(?:text|json)["`\]]?', cleaned, flags=re.I)
            or re.fullmatch(
                r'cast\s*\(\s*(?:[a-z_]\w*\.)?["`\[]?result_(?:text|json)["`\]]?\s+as\s+text\s*\)',
                cleaned,
                flags=re.I,
            )
        ):
            return True
    return False


def _raw_result_payload_projection(statement: str) -> tuple[bool, int | None]:
    """Describe raw payload output as ``(projects_payload, preview_chars)``.

    Metadata expressions such as COUNT(result_json) are intentionally excluded.
    The caller separately checks whether the payload is shaped with a structured
    helper. ``None`` means the projection is unbounded or its bound is unknown.
    """
    match = TOOL_RESULTS_SELECT_RE.search(statement or "")
    if not match:
        return False, None

    projection = match.group("select").strip()
    projection = re.sub(r"^(?:distinct|all)\s+", "", projection, flags=re.I)
    if _directly_selects_result_payload(f"SELECT {projection} FROM __tool_results"):
        return True, None

    preview_lengths = []
    for function_match in re.finditer(r"\bsubstr(?:ing)?\s*\(", projection, re.I):
        args = _parse_sql_call_arguments(projection, function_match.end() - 1)
        if not args or not _expression_reads_payload(args[0], {"result_json", "result_text"}):
            continue
        if len(args) < 3 or not re.fullmatch(r"\+?\d+", args[2].strip()):
            return True, None
        preview_lengths.append(int(args[2].strip()))

    if preview_lengths:
        return True, sum(preview_lengths)
    return False, None


def _created_table_name(statement: str) -> str | None: return _matched_user_table(CREATE_TABLE_RE, statement)


def _inserted_table_name(statement: str) -> str | None: return _matched_user_table(INSERT_INTO_RE, statement)


def _manual_values_table_name(statement: str) -> str | None:
    return _matched_user_table(MANUAL_INSERT_VALUES_RE, statement) or _matched_user_table(MANUAL_CREATE_VALUES_RE, statement)


def _manual_literal_rowset_values(statement: str) -> list[str]:
    if not re.search(
        r"\bunion\s+all\s+select\b|\bas\s*\(\s*values\s*\(|"
        r"\binsert\s+(?:or\s+\w+\s+)?into\b[^;]*?\bvalues\s*\([^;]*?\)\s*,\s*\(",
        statement or "",
        re.I,
    ):
        return []
    literals = list(dict.fromkeys(value for match in SQL_STRING_LITERAL_RE.finditer(statement or "")
                                  if len((value := match.group(1).replace("''", "'")).strip()) >= 20))
    return literals if len(literals) >= 3 and sum(map(len, literals)) >= 100 else []


def _matched_user_table(regex: re.Pattern, statement: str) -> str | None:
    match = regex.search(statement or "")
    if not match:
        return None
    name = match.group("name").lower()
    return None if name.startswith("sqlite_") or name in INTERNAL_TABLE_NAMES else name


def _reads_table(statement: str, table_name: str) -> bool:
    return bool(re.search(rf'\b(?:from|join)\s+"?{re.escape(table_name)}"?\b', statement or "", re.I))


def _direct_fetch_key(statement: str) -> str:
    ids = [match.group("id") for match in RESULT_ID_EQ_RE.finditer(statement or "")]
    return f"result_id={ids[0]}" if ids else _normalize(statement)
