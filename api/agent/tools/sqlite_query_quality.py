import re
from collections import Counter
from types import SimpleNamespace
from typing import Iterable

import sqlparse


TOOL_RESULTS_RE = re.compile(r'\b(?:from|join)\s+"?__tool_results"?\b', re.I)
RESULT_ID_EQ_RE = re.compile(r"\b(?:[a-z_]\w*\.)?result_id\s*=\s*(['\"])(?P<id>[^'\"]+)\1", re.I)
RESULT_ID_IN_RE = re.compile(r"\b(?:[a-z_]\w*\.)?result_id\s+in\s*\((?P<values>[^)]*)\)", re.I | re.S)
CREATE_TABLE_RE = re.compile(r'\bcreate\s+(?:temp(?:orary)?\s+)?table\s+(?:if\s+not\s+exists\s+)?"?(?P<name>[a-z_]\w*)"?', re.I)
INSERT_INTO_RE = re.compile(r'\binsert\s+(?:or\s+\w+\s+)?into\s+"?(?P<name>[a-z_]\w*)"?', re.I)
MANUAL_INSERT_VALUES_RE = re.compile(r'\binsert\s+(?:or\s+\w+\s+)?into\s+"?(?P<name>[a-z_]\w*)"?[\s\S]*?\bvalues\b', re.I)
JSON_FUNCTION_RE = re.compile(r"\bjson_(?:extract|each)\s*\(", re.I)

COUNT_FIELDS = (
    "statement_count", "tool_result_statement_count", "single_result_id_filters",
    "direct_result_text_fetches", "aggregate_tool_result_queries", "smart_tool_result_queries",
    "uses_json_functions", "uses_cte", "uses_join", "uses_group_by", "uses_window",
    "uses_order_by", "creates_working_table", "reads_working_table",
)
USER_TABLE_PREFIXES = ("__", "sqlite_")


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
    direct_fetch_keys: list[str] = []

    for statement in statements:
        lowered = _normalize(statement)
        if not lowered:
            continue
        counts["statement_count"] += 1
        mentions = bool(TOOL_RESULTS_RE.search(statement))
        eq_count = len(RESULT_ID_EQ_RE.findall(statement))
        in_count = _result_id_in_count(statement)
        single_result_filter = mentions and eq_count == 1 and in_count == 0
        direct_fetch = single_result_filter and _directly_selects_result_text(statement)
        aggregate = mentions and not direct_fetch and (in_count > 1 or eq_count != 1)
        flags = {
            "uses_json_functions": bool(JSON_FUNCTION_RE.search(statement)),
            "uses_cte": lowered.startswith("with ") or " with " in lowered,
            "uses_join": " join " in lowered,
            "uses_group_by": " group by " in lowered,
            "uses_window": " over (" in lowered,
            "uses_order_by": " order by " in lowered,
        }
        counts["tool_result_statement_count"] += int(mentions)
        counts["single_result_id_filters"] += int(single_result_filter)
        counts["direct_result_text_fetches"] += int(direct_fetch)
        counts["aggregate_tool_result_queries"] += int(aggregate)
        counts["smart_tool_result_queries"] += int(aggregate and any(flags.values()))
        for key, enabled in flags.items():
            counts[key] += int(enabled)
        if direct_fetch:
            direct_fetch_keys.append(_direct_fetch_key(statement))
        if table := _created_table_name(statement):
            created_tables.append(table)
            if mentions:
                working_sources.add(table)
        if table := _manual_values_table_name(statement):
            manual_value_tables.append(table)
        if mentions and (table := _inserted_table_name(statement)):
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
        **{field: counts[field] for field in COUNT_FIELDS},
    )


def build_tool_result_query_advisories(
    sql_values: Iterable[str],
    *,
    available_tool_result_rows: int,
) -> list[SimpleNamespace]:
    if available_tool_result_rows < 2:
        return []
    advisories = []
    summary = summarize_sqlite_tool_result_sql(sql_values)
    if summary.manual_values_working_tables:
        advisories.append(
            _advisory("manual_working_table_from_visible_results", "This builds a working table by hand-entering VALUES while multiple tool results exist. Derive rows from __tool_results in SQL with json_extract/json_each or CREATE TABLE ... AS SELECT.")
        )
    if summary.direct_result_text_fetches >= 2 or summary.duplicate_direct_fetches:
        advisories.append(
            _advisory("tool_result_blob_fetch_loop", "You are fetching full result_text blobs one result at a time. Combine prior tool outputs in one shaped query using WHERE result_id IN (...), CTEs, json_extract/json_each, joins, aggregation, or CREATE TABLE ... AS SELECT for a working table.")
        )
    elif summary.direct_result_text_fetches:
        advisories.append(
            _advisory("single_tool_result_blob_fetch", "This fetched one full result_text blob while multiple tool results are available. For multi-source synthesis, query the needed rows together; use substr(result_text,1,N) only for previews.")
        )
    return advisories


def _advisory(code: str, message: str) -> SimpleNamespace:
    return SimpleNamespace(code=code, message=message)


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


def _directly_selects_result_text(statement: str) -> bool:
    match = re.search(
        r'\bselect\b(?P<select>.*?)\bfrom\s+"?__tool_results"?\b',
        statement or "",
        re.I | re.S,
    )
    if not match:
        return False
    for field in match.group("select").split(","):
        cleaned = re.sub(r"\s+as\s+\"?[a-z_]\w*\"?$", "", field.strip(), flags=re.I).strip('"')
        if (
            cleaned == "*"
            or cleaned.endswith(".*")
            or re.fullmatch(r'(?:[a-z_]\w*\.)?"?result_text"?', cleaned, flags=re.I)
        ):
            return True
    return False


def _created_table_name(statement: str) -> str | None: return _matched_user_table(CREATE_TABLE_RE, statement)


def _inserted_table_name(statement: str) -> str | None: return _matched_user_table(INSERT_INTO_RE, statement)


def _manual_values_table_name(statement: str) -> str | None: return _matched_user_table(MANUAL_INSERT_VALUES_RE, statement)


def _matched_user_table(regex: re.Pattern, statement: str) -> str | None:
    match = regex.search(statement or "")
    if not match:
        return None
    name = match.group("name").lower()
    return None if name.startswith(USER_TABLE_PREFIXES) else name


def _reads_table(statement: str, table_name: str) -> bool:
    return bool(re.search(rf'\b(?:from|join)\s+"?{re.escape(table_name)}"?\b', statement or "", re.I))


def _direct_fetch_key(statement: str) -> str:
    ids = [match.group("id") for match in RESULT_ID_EQ_RE.finditer(statement or "")]
    return f"result_id={ids[0]}" if ids else _normalize(statement)
