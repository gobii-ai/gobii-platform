import json
import re
from collections import Counter
from types import SimpleNamespace
from typing import Iterable

import sqlparse
from sqlparse import tokens as sql_tokens
from sqlparse.sql import Function, Parenthesis, Values, Where


TOOL_RESULTS_RE = re.compile(r'\b(?:from|join)\s+"?__tool_results"?\b', re.I)
RESULT_ID_EQ_RE = re.compile(r"\b(?:[a-z_]\w*\.)?result_id\s*=\s*(['\"])(?P<id>[^'\"]+)\1", re.I)
RESULT_ID_IN_RE = re.compile(r"\b(?:[a-z_]\w*\.)?result_id\s+in\s*\((?P<values>[^)]*)\)", re.I | re.S)
RESULT_ID_IN_VALUE_RE = re.compile(r"(?:'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|\?|[:@$][a-z_]\w*)", re.I)
CREATE_TABLE_RE = re.compile(r'\bcreate\s+(?:temp(?:orary)?\s+)?table\s+(?:if\s+not\s+exists\s+)?"?(?P<name>[a-z_]\w*)"?', re.I)
CREATE_TABLE_AS_RE = re.compile(r'\bcreate\s+table\b[^;]*?\bas\s+(?:with|select)\b', re.I | re.S)
CREATE_TEMP_TABLE_RE = re.compile(r'\bcreate\s+temp(?:orary)?\s+table\b', re.I)
CREATE_UNIQUE_INDEX_RE = re.compile(r'\bcreate\s+unique\s+index\b[^;]*?\bon\s+"?(?P<name>[a-z_]\w*)"?', re.I | re.S)
TABLE_IDENTITY_RE = re.compile(r'\bprimary\s+key\b|(?<!["\'`\[])\bunique\b(?!["\'`\]])', re.I)
INSERT_INTO_RE = re.compile(r'\binsert\s+(?:or\s+\w+\s+)?into\s+"?(?P<name>[a-z_]\w*)"?', re.I)
MODEL_MUTATION_RE = re.compile(
    r'\b(?P<operation>insert(?:\s+or\s+\w+)?\s+into|replace\s+into|update|delete\s+from)\s+'
    r'(?:(?:"?[a-z_]\w*"?)\s*\.\s*)?"?(?P<name>[a-z_]\w*)"?',
    re.I,
)
READ_TABLE_RE = re.compile(
    r'\b(?:from|join)\s+(?:(?:"?[a-z_]\w*"?)\s*\.\s*)?"?(?P<name>[a-z_]\w*)"?',
    re.I,
)
CTE_NAME_RE = re.compile(
    r'(?:\bwith|,)\s*(?:recursive\s+)?"?(?P<name>[a-z_]\w*)"?\s*(?:\([^)]*\))?\s+as\s*\(',
    re.I,
)
MANUAL_INSERT_VALUES_RE = re.compile(r'\binsert\s+(?:or\s+\w+\s+)?into\s+"?(?P<name>[a-z_]\w*)"?\s*(?:\(\s*"?[a-z_]\w*"?(?:\s*,\s*"?[a-z_]\w*"?)*\s*\)\s*)?values\b', re.I)
JSON_FUNCTION_RE = re.compile(r"\bjson_(?:extract|each)\s*\(", re.I)
JSON_EACH_RE = re.compile(r"\bjson_each\s*\(", re.I)
TOOL_PAYLOAD_RE = re.compile(r"\b(?:result_json|result_text|analysis_json)\b", re.I)
URL_RE = re.compile(r"https?://[^\s'\",)]+", re.I)
BULK_MANUAL_VALUES_ROW_LIMIT = 4
BULK_COPY_MESSAGE = (
    "Query not executed: a literal-row import copied from tool results is unreliable. Do not copy rows from visible "
    "output; derive them from all relevant __tool_results rows in one INSERT ... SELECT/json_each query."
)
BLOB_LOOP_MESSAGE = (
    "Query not executed: full result blobs were fetched one at a time. Combine prior outputs in one shaped query "
    "using tool_name or result_id IN (...), plus CTEs/json_extract/json_each as needed."
)
ROW_LOOP_MESSAGE = (
    "Query not executed: do not read __tool_results or a staging table derived from it one result_id at a time. "
    "A one-item IN (...) is still one-at-a-time. Do not retry that shape: use one shaped INSERT ... SELECT/json_each or "
    "query covering every relevant sibling via tool_name or a multi-item result_id IN (...)."
)
MANUAL_COPY_MESSAGE = (
    "This builds a VALUES table while multiple tool results exist. If those rows came from tool outputs, do not "
    "continue: rebuild from all relevant __tool_results with one aggregate INSERT ... SELECT/json_each query."
)
SINGLE_IMPORT_MESSAGE = (
    "This imports one result_id while sibling tool results exist. If they share a dataset, replace separate imports "
    "with one shaped query over tool_name or result_id IN (...)."
)
SOURCE_LITERAL_COPY_MESSAGE = (
    "Query not executed: this model write copies source facts into SQL literals. Derive complete rows from "
    "__tool_results in the same INSERT/UPDATE, keyed by stable ID; refresh every mutable/provenance field. Use "
    "tool_name or visible result_id; don't fetch/copy the blob. For http_request JSON, nested arrays are under "
    "$.content; use the actual array key. Then query the model."
)
COUNT_FIELDS = (
    "statement_count", "tool_result_statement_count", "single_result_id_filters", "single_derived_result_filters", "single_tool_result_imports",
    "direct_result_text_fetches", "aggregate_tool_result_queries", "smart_tool_result_queries",
    "uses_json_functions", "uses_bounded_text_projection", "uses_cte", "uses_join", "uses_group_by", "uses_window",
    "uses_order_by", "creates_working_table", "reads_working_table", "tool_result_ctas",
)
USER_TABLE_PREFIXES = ("__", "sqlite_")
NON_MODEL_TABLE_PREFIXES = (*USER_TABLE_PREFIXES, "_csv_", "temp_", "tmp_", "stage_", "staging_")
NON_MODEL_TABLE_SUFFIXES = ("_temp", "_tmp", "_stage", "_staging")


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
    statements = [_structural_sql(stmt.strip()) for sql in sql_list for stmt in sqlparse.split(sql) if stmt.strip()]
    counts: Counter[str] = Counter()
    created_tables: list[str] = []
    working_sources: set[str] = set()
    row_derived_sources: set[str] = set()
    explicit_table_identity: dict[str, bool] = {}
    manual_value_tables: list[str] = []
    manual_value_rows = 0
    direct_fetch_keys: list[str] = []

    for statement in statements:
        lowered = _normalize(statement)
        if not lowered:
            continue
        counts["statement_count"] += 1
        mentions = bool(TOOL_RESULTS_RE.search(statement))
        single_id_filter = _has_single_result_id_filter(statement)
        single_result_filter = mentions and single_id_filter
        single_derived_result_filter = single_id_filter and any(
            _reads_table(statement, table) for table in working_sources
        )
        created_table = _created_table_name(statement)
        inserted_table = _inserted_table_name(statement)
        direct_fetch = single_result_filter and _directly_selects_result_text(statement)
        aggregate = mentions and not direct_fetch and not single_result_filter
        flags = {
            "uses_json_functions": bool(JSON_FUNCTION_RE.search(statement)),
            "uses_bounded_text_projection": _has_bounded_result_text_projection(statement)
            and not _directly_selects_result_text(statement),
            "uses_cte": lowered.startswith("with ") or " with " in lowered,
            "uses_join": " join " in lowered,
            "uses_group_by": " group by " in lowered,
            "uses_window": " over (" in lowered,
            "uses_order_by": " order by " in lowered,
        }
        counts["tool_result_statement_count"] += int(mentions)
        counts["single_result_id_filters"] += int(single_result_filter or single_derived_result_filter)
        counts["single_derived_result_filters"] += int(single_derived_result_filter)
        counts["single_tool_result_imports"] += int(single_result_filter and bool(created_table or inserted_table))
        counts["direct_result_text_fetches"] += int(direct_fetch)
        counts["aggregate_tool_result_queries"] += int(aggregate)
        counts["smart_tool_result_queries"] += int(aggregate and any(flags.values()))
        counts["tool_result_ctas"] += int(mentions and bool(CREATE_TABLE_AS_RE.search(statement)))
        for key, enabled in flags.items():
            counts[key] += int(enabled)
        if direct_fetch:
            direct_fetch_keys.append(_direct_fetch_key(statement))
        if created_table:
            created_tables.append(created_table)
            if not CREATE_TABLE_AS_RE.search(statement) and not CREATE_TEMP_TABLE_RE.search(statement):
                explicit_table_identity[created_table] = bool(TABLE_IDENTITY_RE.search(statement))
            if mentions:
                working_sources.add(created_table)
        if index_match := CREATE_UNIQUE_INDEX_RE.search(statement):
            explicit_table_identity[index_match.group("name").casefold()] = True
        if table := _manual_values_table_name(statement):
            manual_value_tables.append(table)
            manual_value_rows += _manual_values_row_count(statement)
        if mentions and inserted_table:
            working_sources.add(inserted_table)
        if mentions and JSON_EACH_RE.search(statement):
            row_derived_sources.update(table for table in (created_table, inserted_table) if table)

    created_unique = tuple(dict.fromkeys(created_tables))
    manual_values_unique = tuple(dict.fromkeys(manual_value_tables))
    counts["creates_working_table"] = sum(1 for table in created_unique if table in working_sources)
    counts["reads_working_table"] = sum(1 for table in created_unique if table in working_sources and any(_reads_table(stmt, table) for stmt in statements))
    duplicate_fetches = sum(count - 1 for count in Counter(direct_fetch_keys).values() if count > 1)
    return SimpleNamespace(
        sqlite_call_count=sqlite_call_count if sqlite_call_count is not None else len(sql_list),
        duplicate_direct_fetches=duplicate_fetches,
        working_table_names=created_unique,
        derived_working_table_names=tuple(table for table in created_unique if table in working_sources),
        row_derived_working_table_names=tuple(table for table in created_unique if table in row_derived_sources),
        manual_values_table_names=manual_values_unique,
        manual_values_rows=manual_value_rows,
        manual_values_working_tables=sum(1 for table in manual_values_unique if table in created_unique),
        unkeyed_explicit_table_names=tuple(table for table, keyed in explicit_table_identity.items() if not keyed),
        **{field: counts[field] for field in COUNT_FIELDS},
    )


def build_tool_result_query_advisories(
    sql_values: Iterable[str], *, available_tool_result_rows: int,
    tool_result_payloads: Iterable[str] = (),
) -> list[SimpleNamespace]:
    if available_tool_result_rows < 1: return []
    sql_values = [str(sql or "") for sql in sql_values]
    tool_result_payloads = tuple(tool_result_payloads)
    advisories = []
    summary = summarize_sqlite_tool_result_sql(sql_values)
    copied_model_tables = _source_literal_copy_tables(sql_values, tool_result_payloads)
    unkeyed_models = set(summary.unkeyed_explicit_table_names).intersection(summary.row_derived_working_table_names)
    if unkeyed_models:
        advisories.append(_advisory("reusable_model_missing_identity", f"Query not executed: reusable table(s) {', '.join(sorted(unkeyed_models))} derive repeating tool-result rows without stable identity. Add PRIMARY KEY/UNIQUE to CREATE TABLE; use TEMP CTAS only for a disposable extract.", blocking=True))
    if summary.tool_result_ctas:
        advisories.append(_advisory("tool_result_ctas", "CTAS has no stable identity. Use it only for a disposable extract; reusable models need explicit CREATE TABLE with PRIMARY KEY/UNIQUE, then aggregate INSERT."))
    if available_tool_result_rows < 2:
        if copied_model_tables:
            advisories.append(_advisory(
                "source_facts_copied_into_model",
                SOURCE_LITERAL_COPY_MESSAGE,
                blocking=True,
            ))
        return advisories
    model_advisories, advisories = advisories, []
    literal_select_rows, copied_provenance_urls = _manual_import_evidence(sql_values, tool_result_payloads)
    if summary.manual_values_rows + literal_select_rows >= BULK_MANUAL_VALUES_ROW_LIMIT and len(copied_provenance_urls) >= 2:
        advisories.append(_advisory("bulk_manual_working_table_from_visible_results", BULK_COPY_MESSAGE, blocking=True))
    elif summary.manual_values_working_tables:
        advisories.append(_advisory("manual_working_table_from_visible_results", MANUAL_COPY_MESSAGE))
    if summary.direct_result_text_fetches >= 2 or summary.duplicate_direct_fetches:
        advisories.append(_advisory("tool_result_blob_fetch_loop", BLOB_LOOP_MESSAGE, blocking=True))
    elif summary.direct_result_text_fetches:
        advisories.append(_advisory("single_tool_result_blob_fetch", "This fetched one full result_text blob while multiple tool results are available. For multi-source synthesis, query the needed rows together; use substr(result_text,1,N) only for previews."))
    if summary.single_result_id_filters >= 2 and summary.direct_result_text_fetches < 2:
        advisories.append(_advisory("tool_result_row_loop", ROW_LOOP_MESSAGE, blocking=True))
    elif summary.single_tool_result_imports:
        advisories.append(_advisory("single_tool_result_import", SINGLE_IMPORT_MESSAGE))
    if copied_model_tables:
        advisories.append(_advisory(
            "source_facts_copied_into_model",
            SOURCE_LITERAL_COPY_MESSAGE,
            blocking=True,
        ))
    return advisories + model_advisories


def source_derived_model_mutation_tables(sql_values: Iterable[str]) -> tuple[str, ...]:
    """Return durable DML targets whose rows come from __tool_results in the same statement."""

    tables: list[str] = []
    for sql in sql_values:
        for raw_statement in sqlparse.split(str(sql or "")):
            statement = _structural_sql(raw_statement)
            if not TOOL_RESULTS_RE.search(statement):
                continue
            match = MODEL_MUTATION_RE.search(statement)
            if (
                match
                and _is_named_model_table(match.group("name"))
                and _mutation_derives_payload(statement, match)
            ):
                tables.append(match.group("name").casefold())
    return tuple(dict.fromkeys(tables))


def named_model_read_tables(sql_values: Iterable[str]) -> tuple[str, ...]:
    """Return named model tables read without directly reading raw tool results."""

    tables: list[str] = []
    for sql in sql_values:
        for raw_statement in sqlparse.split(str(sql or "")):
            statement = _structural_sql(raw_statement)
            cte_names = {match.group("name").casefold() for match in CTE_NAME_RE.finditer(statement)}
            for match in READ_TABLE_RE.finditer(statement):
                table = match.group("name").casefold()
                trailing = statement[match.end():].lstrip()
                if not trailing.startswith("(") and table not in cte_names and _is_named_model_table(table):
                    tables.append(table)
    return tuple(dict.fromkeys(tables))


def source_derived_model_reconciled_tables(sql_values: Iterable[str]) -> tuple[str, ...]:
    """Return source-derived model targets read by a later statement."""

    pending: set[str] = set()
    reconciled: list[str] = []
    for sql in sql_values:
        for statement in sqlparse.split(str(sql or "")):
            for table in named_model_read_tables((statement,)):
                if table in pending:
                    reconciled.append(table)
                    pending.remove(table)
            for table in source_derived_model_mutation_tables((statement,)):
                pending.add(table)
                reconciled = [reconciled_table for reconciled_table in reconciled if reconciled_table != table]
    return tuple(dict.fromkeys(reconciled))


def _is_named_model_table(table_name: str) -> bool:
    normalized = str(table_name or "").casefold()
    return bool(normalized) and not normalized.startswith(NON_MODEL_TABLE_PREFIXES) and not normalized.endswith(
        NON_MODEL_TABLE_SUFFIXES
    )


def _mutation_derives_payload(statement: str, match: re.Match) -> bool:
    if not TOOL_PAYLOAD_RE.search(statement):
        return False
    operation = match.group("operation").casefold()
    if operation.startswith(("insert", "replace")):
        return bool(re.search(r"\bselect\b", statement[match.end():], re.I))
    if operation.startswith("delete"):
        return True

    remainder = statement[match.end():]
    set_match = re.search(r"\bset\b", remainder, re.I)
    if not set_match:
        return False
    value_clause = remainder[set_match.end():]
    if where_match := re.search(r"\bwhere\b", value_clause, re.I):
        value_clause = value_clause[:where_match.start()]
    if TOOL_RESULTS_RE.search(value_clause):
        return True
    return any(
        re.search(rf"\b{re.escape(cte_name)}\b", value_clause, re.I)
        for cte_name in _tool_result_cte_names(statement)
    )


def _tool_result_cte_names(statement: str) -> tuple[str, ...]:
    names = []
    for match in CTE_NAME_RE.finditer(statement):
        depth = 1
        cursor = match.end()
        while cursor < len(statement) and depth:
            depth += (statement[cursor] == "(") - (statement[cursor] == ")")
            cursor += 1
        if depth == 0 and TOOL_RESULTS_RE.search(statement[match.end():cursor - 1]):
            names.append(match.group("name").casefold())
    return tuple(names)


def _advisory(code: str, message: str, *, blocking: bool = False) -> SimpleNamespace:
    return SimpleNamespace(code=code, message=message, blocking=blocking)


def _sql_values_from_params(params: dict) -> list[str]:
    value = params.get("sql") or params.get("query") or params.get("queries")
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item or "").strip()]
    return [str(value)] if value else []


def _normalize(statement: str) -> str:
    return re.sub(r"\s+", " ", statement or "").strip().lower()


def _structural_sql(statement: str) -> str:
    """Remove comments and mask string contents before structural regex checks."""
    parsed = sqlparse.parse(statement or "")
    if not parsed:
        return ""
    parts = []
    for token in parsed[0].flatten():
        if token.ttype in sql_tokens.Comment:
            continue
        if token.ttype in sql_tokens.Literal.String.Single:
            parts.append("'value'")
        elif token.ttype in sql_tokens.Literal.String.Symbol and not re.fullmatch(r'"[a-z_]\w*"', token.value, re.I):
            parts.append('"value"')
        else:
            parts.append(token.value)
    return "".join(parts)


def _manual_import_evidence(sql_values: Iterable[str], tool_result_payloads: Iterable[str]) -> tuple[int, set[str]]:
    literal_select_rows, query_urls = 0, set()
    for sql in sql_values:
        for statement in sqlparse.parse(str(sql or "")):
            structural = _structural_sql(str(statement))
            target = _inserted_table_name(structural) or (CREATE_TABLE_AS_RE.search(structural) and _created_table_name(structural))
            literal_rows = 0 if not target or re.search(r"\bfrom\b", structural, re.I) else sum(
                token.match(sql_tokens.Keyword.DML, "SELECT") for token in statement.tokens
            )
            literal_rows += _literal_json_row_count(statement) if target else 0
            literal_select_rows += literal_rows
            groups = [group for group in statement.tokens if isinstance(group, Values)]
            if literal_rows:
                groups.append(statement)
            for group in groups:
                for token in group.flatten():
                    if token.ttype not in sql_tokens.Comment and (isinstance(group, Values) or token.ttype in sql_tokens.Literal.String):
                        query_urls.update(URL_RE.findall(token.value))
    payload_text = "\n".join(str(payload or "") for payload in tool_result_payloads)
    return literal_select_rows, {url for url in query_urls if url in payload_text}


def _source_literal_copy_tables(
    sql_values: Iterable[str], tool_result_payloads: Iterable[str],
) -> tuple[str, ...]:
    payload_text = "\n".join(str(payload or "") for payload in tool_result_payloads)
    if not payload_text:
        return ()

    copied_tables = []
    for sql in sql_values:
        for statement in sqlparse.parse(str(sql or "")):
            raw_statement = str(statement)
            structural = _structural_sql(raw_statement)
            match = MODEL_MUTATION_RE.search(structural)
            if not match or not _is_named_model_table(match.group("name")):
                continue
            matched_literals = set()
            copied_url = False
            previous_token = None
            for token in _mutation_value_tokens(statement, match.group("operation")):
                if token.is_whitespace or token.ttype in sql_tokens.Comment:
                    continue
                is_json_selector = previous_token is not None and previous_token.value in {"->", "->>"}
                previous_token = token
                if token.ttype != sql_tokens.Literal.String.Single or is_json_selector:
                    continue
                value = token.value[1:-1].replace("''", "'")
                if len(value) < 6 or value not in payload_text:
                    continue
                matched_literals.add(value)
                copied_url = copied_url or bool(URL_RE.fullmatch(value))
            if copied_url or len(matched_literals) >= 2:
                copied_tables.append(match.group("name").casefold())
    return tuple(dict.fromkeys(copied_tables))


def _mutation_value_tokens(statement, operation: str):
    """Yield tokens that supply new row values, excluding identity predicates."""

    operation = operation.casefold()
    if operation.startswith("delete"):
        return ()
    if operation.startswith(("insert", "replace")):
        values = [token for token in statement.tokens if isinstance(token, Values)]
        if values:
            return tuple(child for value_group in values for child in value_group.flatten())

        projection = []
        after_insert = False
        in_select = False
        for token in statement.tokens:
            if token.ttype in sql_tokens.Comment or token.is_whitespace:
                continue
            if token.ttype in sql_tokens.Keyword.DML and token.normalized in {"INSERT", "REPLACE"}:
                after_insert = True
                continue
            if after_insert and token.ttype in sql_tokens.Keyword.DML and token.normalized == "SELECT":
                in_select = True
                continue
            if in_select and token.match(sql_tokens.Keyword, "FROM"):
                break
            if in_select:
                projection.extend(token.flatten())
        return tuple(projection)

    values = []
    in_set = False
    for token in statement.tokens:
        if token.ttype in sql_tokens.Comment or token.is_whitespace:
            continue
        if token.match(sql_tokens.Keyword, "SET"):
            in_set = True
            continue
        if in_set and (
            isinstance(token, Where)
            or token.match(sql_tokens.Keyword, ("FROM", "RETURNING", "ORDER BY", "LIMIT"))
        ):
            break
        if in_set:
            values.extend(token.flatten())
    return tuple(values)


def _literal_json_row_count(statement) -> int:
    tokens = [token for token in statement.flatten() if not token.is_whitespace and token.ttype not in sql_tokens.Comment]
    rows = 0
    for name, opening, literal in zip(tokens, tokens[1:], tokens[2:]):
        if name.value.casefold() != "json_each" or opening.value != "(" or literal.ttype != sql_tokens.Literal.String.Single:
            continue
        try:
            value = json.loads(literal.value[1:-1].replace("''", "'"))
        except json.JSONDecodeError:
            continue
        if isinstance(value, (list, dict)):
            rows += len(value)
    return rows


def _result_id_in_count(statement: str) -> int:
    counts = []
    for match in RESULT_ID_IN_RE.finditer(statement or ""):
        values = [value.strip() for value in match.group("values").split(",") if value.strip()]
        valid_literals = values and all(RESULT_ID_IN_VALUE_RE.fullmatch(value) for value in values)
        counts.append(len(values) if valid_literals else 2)
    return max(counts or [0])


def _has_single_result_id_filter(statement: str) -> bool:
    eq_count = len(RESULT_ID_EQ_RE.findall(statement or ""))
    in_count = _result_id_in_count(statement)
    return (eq_count == 1 and in_count == 0) or (eq_count == 0 and in_count == 1)


def _manual_values_row_count(statement: str) -> int:
    parsed = sqlparse.parse(statement or "")
    return 0 if not parsed else sum(
        isinstance(child, Parenthesis)
        for values_group in parsed[0].tokens
        if isinstance(values_group, Values)
        for child in values_group.tokens
    )


def _directly_selects_result_text(statement: str) -> bool:
    match = re.search(r'\bselect\b(?P<select>.*?)\bfrom\s+"?__tool_results"?\b', statement or "", re.I | re.S)
    if not match:
        return False
    for field in match.group("select").split(","):
        cleaned = re.sub(r"\s+as\s+\"?[a-z_]\w*\"?$", "", field.strip(), flags=re.I).strip('"')
        if cleaned == "*" or cleaned.endswith(".*") or re.fullmatch(
            r'(?:[a-z_]\w*\.)?"?result_text"?', cleaned, flags=re.I
        ):
            return True
    return False


def _has_bounded_result_text_projection(statement: str) -> bool:
    parsed = sqlparse.parse(statement or "")
    if not parsed:
        return False
    for function in _nested_functions(parsed[0]):
        params = list(function.get_parameters())
        if (
            str(function.get_name() or "").casefold() in {"substr", "substring"}
            and params
            and re.fullmatch(r'(?:[a-z_]\w*\.)?"?result_text"?', str(params[0]).strip(), re.I)
            and (len(params) == 3 or (len(params) == 2 and re.fullmatch(r"-\s*\d+", str(params[1]).strip())))
        ):
            return True
    return False


def _nested_functions(token):
    for child in getattr(token, "tokens", ()):
        if isinstance(child, Function):
            yield child
        yield from _nested_functions(child)


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
