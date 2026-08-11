import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from ..tools.context_hints import URL_FIELD_PRIORITY, extract_context_hint, hint_from_unstructured_text
from ..tools.sqlite_guardrails import clear_guarded_connection, open_guarded_sqlite_connection
from ..tools.sqlite_state import TOOL_RESULTS_TABLE, get_sqlite_db_path
from ..tools.tool_manager import PYTHON_EXEC_TOOL_NAME, RUN_COMMAND_TOOL_NAME, SQLITE_TOOL_NAME
from .link_references import is_source_bearing_tool
from .result_analysis import ResultAnalysis, analyze_result, analysis_to_dict, _get_json_type, _safe_json_path

logger = logging.getLogger(__name__)

PREVIEW_TIERS_EXTERNAL = [
    512,    # Position 0: 512B - Structure hint only
    512,    # Position 1: enough to preserve a compact source's leading facts
    512,    # Position 2: enough to preserve a compact source's leading facts
    512,    # Position 3: keep a four-source parallel batch visible
]

LARGE_RESULT_THRESHOLD = 15_000   # 15KB - start capping here
LARGE_RESULT_PREVIEW_CAP = 1500   # 1.5KB - enough for first array item structure

HUGE_RESULT_THRESHOLD = 50_000    # 50KB - truly large results
HUGE_RESULT_PREVIEW_CAP = 800     # 800 bytes - still shows keys and sample values

FRESH_RESULT_INLINE_THRESHOLD = 40_000  # 40KB - inline fresh results under this

SMALL_RESULT_ALWAYS_INLINE = 2048  # 2KB - always show these in full

PREVIEW_TIERS_SQLITE = [
    16384,  # Position 0: 16KB - Show full query result
    8192,   # Position 1: 8KB - Recent query results
    4096,   # Position 2: 4KB - Older query results
    2048,   # Position 3: 2KB
    1024,   # Position 4: 1KB
]

PREVIEW_TIER_COUNT = max(len(PREVIEW_TIERS_EXTERNAL), len(PREVIEW_TIERS_SQLITE))
MAX_ACTIVE_PROSE_PREVIEWS = 12

MAX_TOOL_RESULT_BYTES = 5_000_000
MAX_TOP_KEYS = 20

EXCLUDED_TOOL_NAMES = {SQLITE_TOOL_NAME, "sqlite_query"}
COMPUTE_SOURCE_TOOL_NAMES = frozenset({PYTHON_EXEC_TOOL_NAME, RUN_COMMAND_TOOL_NAME})
SPAWN_WEB_TASK_RESULT_TOOL_NAME = "spawn_web_task_result"

SCHEMA_ELIGIBLE_TOOL_PREFIXES = ("http_request", "mcp_")

_BASE64_RE = re.compile(r"base64,", re.IGNORECASE)
_IMAGE_RE = re.compile(r"data:image/|image_base64|image_url", re.IGNORECASE)
BARBELL_TEXT_FORMATS = frozenset({"html", "markdown", "plain", "log"})
_UUID_RESULT_ID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)
_LINK_FIELD_RE = re.compile(
    r"\b([A-Za-z_][\w-]*(?:url|link|href))\s*([:=])\s*"
    r"(?:https?://\S+\s+\[link_ref:\s*)?\$\[link:",
    re.IGNORECASE,
)
_URL_PART_FIELD_RE = re.compile(
    r"(\b[\w-]*(?:host|path|route)\s*[:=]\s*)([^|\n]*?\S)(?=[^\S\r\n]*(?:\||\\n|\r?\n|$))",
    re.IGNORECASE,
)

SHORT_RESULT_ID_MIN_LEN = 6
SHORT_RESULT_ID_MAX_LEN = 12
MAX_OPTIONAL_SOURCE_ARRAYS = 2
MAX_OPTIONAL_SOURCE_PATH_CHARS = 160
MAX_OPTIONAL_SOURCE_FIELDS = 8
MAX_OPTIONAL_SOURCE_FIELD_CHARS = 48
MAX_OPTIONAL_SOURCE_HINT_CHARS = 900
PROSE_INSPECTION_TOTAL_CHARS = 8_000
PROSE_INSPECTION_MIN_CHARS = 400
PROSE_INSPECTION_MAX_CHARS = 2_000
EXPLICIT_SECTION_DELIMITERS = {
    "\n---\n": "char(10)||'---'||char(10)",
    "\n===\n": "char(10)||'==='||char(10)",
}
_LABELED_SECTION_DELIMITER_RE = re.compile(
    r"\n((?:-{3,}|={3,})[ A-Za-z0-9_.:/]{1,64}(?:-{3,}|={3,}))\n"
)


@dataclass(frozen=True)
class ExplicitSectionShape:
    count: int
    delimiter_sql: str
    fields: tuple[str, ...]
    stable_key: str


def _is_scrape_as_markdown_tool(tool_name: str) -> bool:
    return tool_name.endswith("scrape_as_markdown")


def _explicit_section_shape(text: str | None) -> ExplicitSectionShape | None:
    if not text:
        return None
    delimiters = dict(EXPLICIT_SECTION_DELIMITERS)
    for label in _LABELED_SECTION_DELIMITER_RE.findall(text):
        delimiter = f"\n{label}\n"
        delimiters[delimiter] = f"char(10)||'{label.replace(chr(39), chr(39) * 2)}'||char(10)"
    matches = []
    for delimiter, sql_expression in delimiters.items():
        delimiter_count = text.count(delimiter)
        if delimiter_count >= 2:
            matches.append((delimiter_count + 1, delimiter, sql_expression))
    if not matches:
        return None
    count, delimiter, delimiter_sql = max(matches)
    sections = text.split(delimiter)[:32]
    field_counts: dict[str, int] = {}
    field_order: list[str] = []
    for section in sections:
        for field in dict.fromkeys(re.findall(r"(?m)^([A-Za-z_][\w-]{0,63}):[ \t]*", section)):
            if field not in field_counts:
                field_order.append(field)
            field_counts[field] = field_counts.get(field, 0) + 1
    minimum_count = max(2, len(sections) // 4)
    fields = tuple(field for field in field_order if field_counts[field] >= minimum_count)
    stable_key = next(
        (
            field for field in fields
            if field.casefold() == "id" or field.casefold().endswith("_id")
        ),
        fields[0] if fields else "",
    )
    if not stable_key:
        return None
    return ExplicitSectionShape(
        count=count,
        delimiter_sql=delimiter_sql,
        fields=fields,
        stable_key=stable_key,
    )


def _mark_missing_item_links(text: str) -> str:
    text = _URL_PART_FIELD_RE.sub(r"\1[omitted: no item link]", text)
    matches = {match.group(1).lower(): match.group(2) for match in _LINK_FIELD_RE.finditer(text)}
    field = next((candidate for candidate in URL_FIELD_PRIORITY if candidate in matches), None) or next(
        (candidate for candidate in matches if not any(part in candidate for part in ("source", "feed", "page", "origin"))),
        None,
    )
    if not field:
        return text
    marker = f"{field}{matches[field]} [not provided]"

    def mark(record: str) -> str:
        if re.search(rf"\b{re.escape(field)}\s*[:=]", record, re.IGNORECASE):
            return record
        return f"{record}\n{marker}" if "\n" in record else f"{record} | {marker}"

    if "\n---\n" in text:
        return "\n---\n".join(mark(block) for block in text.split("\n---\n"))
    return "\n".join(
        mark(line) if " | " in line else line
        for line in text.splitlines()
    )


@dataclass(frozen=True)
class ToolCallResultRecord:
    step_id: str
    tool_name: str
    created_at: datetime
    result_text: str
    result_id: Optional[str] = None
    source_batch_id: Optional[str] = None
    source_url: Optional[str] = None
    will_continue_work: Optional[bool] = None
    source_bearing: Optional[bool] = None
    succeeded: bool = True


@dataclass(frozen=True)
class ToolResultPromptInfo:
    result_id: str
    meta: str
    preview_text: Optional[str]
    is_inline: bool
    source_reconciliation_directive: Optional[str]
    suppress_from_prompt: bool = False


def _record_is_source_bearing(record: ToolCallResultRecord) -> bool:
    return is_source_bearing_tool(record.tool_name) and record.source_bearing is not False


def _record_is_batch_eligible(record: ToolCallResultRecord) -> bool:
    return _record_is_source_bearing(record) or record.tool_name in COMPUTE_SOURCE_TOOL_NAMES


def entity_name_stem(value: str) -> str:
    words = re.sub(r"(?<!^)(?=[A-Z])", "_", value).casefold().replace("-", "_").split("_")
    leaf = next((word for word in reversed(words) if word), "")
    return f"{leaf[:-3]}y" if leaf.endswith("ies") else leaf[:-1] if leaf.endswith("s") else leaf


def _array_entity_stem(path: str) -> str:
    return entity_name_stem(path.rsplit(".", 1)[-1].strip('"'))


def _entity_arrays(analysis: ResultAnalysis | None) -> tuple:
    json_analysis = analysis.json_analysis if analysis else None
    if not json_analysis:
        return ()
    return tuple(sorted(
        (item for item in (json_analysis.primary_array, *json_analysis.secondary_arrays) if item and item.item_fields),
        key=lambda item: (sum(_is_entity_key(field) for field in item.item_fields), item.path),
    ))


def _is_entity_key(field: str) -> bool:
    return field == "id" or field.endswith("_id")


def source_array_entity_groups(result_text: str, tool_name: str) -> tuple[set[str], set[str]]:
    _meta, _stored_json, _stored_text, analysis = _summarize_result(result_text, "source", tool_name)
    arrays = _entity_arrays(analysis)
    return (
        {_array_entity_stem(item.path) for item in arrays},
        {
            _array_entity_stem(item.path) for item in arrays
            if any(_is_entity_key(field) for field in item.item_fields)
        },
    )


def _source_item_key(fields: Sequence[str]) -> str | None:
    for predicate in (
        lambda field: field == "id",
        lambda field: field.endswith("_id"),
        lambda field: field == "url",
        lambda field: field.endswith("_url") and field != "source_url",
        lambda field: field == "key",
        lambda field: field.endswith("_key"),
        lambda field: field == "slug",
    ):
        match = next((field for field in fields if predicate(field)), None)
        if match:
            return match
    return None


def _optional_source_array_schemas(
    analysis: ResultAnalysis | None,
) -> tuple[tuple[str, str, str | None, tuple[str, ...]], ...]:
    json_analysis = analysis.json_analysis if analysis else None
    if not json_analysis:
        return ()

    ordered = (json_analysis.primary_array, *json_analysis.secondary_arrays)
    schemas: list[tuple[str, str, str | None, tuple[str, ...]]] = []
    seen_paths: set[str] = set()
    for item in ordered:
        if (
            not item
            or not item.item_fields
            or item.path in seen_paths
            or len(item.path) > MAX_OPTIONAL_SOURCE_PATH_CHARS
        ):
            continue
        fields = [
            field
            for field in item.item_fields
            if len(field) <= MAX_OPTIONAL_SOURCE_FIELD_CHARS
        ][:MAX_OPTIONAL_SOURCE_FIELDS]
        field_summary = ",".join(fields)
        schemas.append((
            item.path,
            f"{item.path}({field_summary})",
            _source_item_key(fields),
            tuple(fields),
        ))
        seen_paths.add(item.path)
        if len(schemas) >= MAX_OPTIONAL_SOURCE_ARRAYS:
            break
    return tuple(schemas)


def _build_optional_source_write_hint(
    tool_name: str,
    analysis: ResultAnalysis | None,
    model_tables: Sequence[str] = (),
    payload: object | None = None,
    reusable_source_set: bool = False,
) -> str:
    """Give source-shape mechanics that fit both new and existing domain models."""
    schemas = _optional_source_array_schemas(analysis)
    if not schemas or len(tool_name) > 100:
        return ""

    json_analysis = analysis.json_analysis if analysis else None
    source_arrays = (
        (json_analysis.primary_array, *json_analysis.secondary_arrays)
        if json_analysis
        else ()
    )
    has_reusable_set = any(
        item
        and item.item_fields
        and item.path == schemas[0][0]
        and item.length >= 2
        for item in source_arrays
    )
    if not model_tables and not has_reusable_set and not reusable_source_set:
        return ""

    first_path, _first_schema, first_key, first_fields = schemas[0]
    first_path = first_path.replace("'", "''")
    parent_path = first_path.rsplit(".", 1)[0]
    parent_value = payload
    if parent_path != "$":
        for segment in parent_path.removeprefix("$.").split("."):
            if not isinstance(parent_value, dict):
                parent_value = None
                break
            parent_value = parent_value.get(segment)
    parent_fields = (
        tuple(
            str(key)
            for key, value in parent_value.items()
            if not isinstance(value, (dict, list))
            and len(str(key)) <= MAX_OPTIONAL_SOURCE_FIELD_CHARS
        )[:MAX_OPTIONAL_SOURCE_FIELDS]
        if isinstance(parent_value, dict)
        else ()
    )
    escaped_tool_name = tool_name.replace("'", "''")

    def render_hint(schema_text: str) -> str:
        key_expr = (
            f"`json_extract(j.value,'$.{first_key}')`"
            if first_key
            else "a stable key derived only from shown item fields"
        )
        safe_fields = [
            field for field in first_fields
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", field)
        ]
        select_shape = ", ".join(
            f"json_extract(j.value,'$.{field}') AS {field}"
            for field in safe_fields
        )
        if model_tables:
            table_list = ",".join(sorted(model_tables)[:4])
            model_guidance = (
                f"Existing tables: {table_list}. Upsert in place; never DELETE/rebuild or lose unrelated rows. "
                f"Join its scalar key to {key_expr}. "
            )
        else:
            model_guidance = f"No model: CREATE a user table; key {key_expr} PRIMARY KEY/UNIQUE. "
        parent_guidance = (
            f" Parent {parent_path}({','.join(parent_fields)}): "
            f"json_extract(t.result_json,'{parent_path}.<field>')."
            if parent_fields and not model_tables
            else ""
        )
        select_guidance = (
            f"INSERT ... SELECT {select_shape} "
            if select_shape
            else "INSERT ... SELECT item fields "
        )
        return (
            f"[SOURCE SET: {schema_text}. "
            f"{model_guidance}"
            "FIRST/NOW: one sqlite_batch rows=[]; __tool_results is read-only, never a write target. "
            "No pre-read/bind/copy. "
            f"INSERT INTO model {select_guidance.removeprefix('INSERT ... ')}FROM __tool_results AS t, "
            f"json_each(t.result_json,'{first_path}') AS j "
            f"WHERE t.is_current_batch=1 AND t.tool_name='{escaped_tool_name}'. "
            "Upsert mutable fields/provenance; then decision SELECT in the same batch."
            f"{parent_guidance}]\n"
        )

    hint = render_hint("; ".join(schema for _path, schema, _key, _fields in schemas))
    if len(hint) > MAX_OPTIONAL_SOURCE_HINT_CHARS:
        hint = render_hint("; ".join(path for path, _schema, _key, _fields in schemas))
    return hint if len(hint) <= MAX_OPTIONAL_SOURCE_HINT_CHARS else ""


def build_short_result_id_map(result_ids: Sequence[str]) -> Dict[str, str]:
    normalized: Dict[str, str] = {str(rid): str(rid) for rid in result_ids}
    uuid_ids = [str(rid) for rid in result_ids if _UUID_RESULT_ID_RE.match(str(rid))]
    if not uuid_ids:
        return normalized

    hex_map = {rid: rid.replace("-", "").lower() for rid in uuid_ids}
    length = SHORT_RESULT_ID_MIN_LEN
    while length <= SHORT_RESULT_ID_MAX_LEN:
        seen: set[str] = set()
        collision = False
        for rid in result_ids:
            rid_str = str(rid)
            if rid_str in hex_map:
                candidate = hex_map[rid_str][:length]
            else:
                candidate = rid_str
            if candidate in seen:
                collision = True
                break
            seen.add(candidate)
        if not collision:
            break
        length += 1

    if length > SHORT_RESULT_ID_MAX_LEN:
        length = 32

    for rid, hex_id in hex_map.items():
        normalized[rid] = hex_id[:length]
    return normalized


def prepare_tool_results_for_prompt(
    records: Sequence[ToolCallResultRecord],
    *,
    recency_positions: Dict[str, int],
    fresh_tool_call_step_id: Optional[str] = None,
    fresh_tool_call_step_ids: Optional[Set[str]] = None,
    url_rewriter: Optional[Callable[[str, ToolCallResultRecord], str]] = None,
    paired_url_rewriter: Optional[Callable[[str, ToolCallResultRecord], str]] = None,
    paired_url_step_ids: Optional[Set[str]] = None,
    named_model_tables: Optional[Set[str]] = None,
    named_model_columns: Optional[Mapping[str, Set[str]]] = None,
) -> Dict[str, ToolResultPromptInfo]:
    prompt_info: Dict[str, ToolResultPromptInfo] = {}
    rows: List[Tuple] = []
    csv_candidates: List[Tuple[str, str, ResultAnalysis]] = []
    fresh_step_ids = set(fresh_tool_call_step_ids or ())
    if fresh_tool_call_step_id:
        fresh_step_ids.add(fresh_tool_call_step_id)
    paired_step_ids = set(paired_url_step_ids or ())
    model_tables = {table.casefold() for table in (named_model_tables or ())}
    model_columns = {
        table.casefold(): {column.casefold() for column in columns}
        for table, columns in (named_model_columns or {}).items()
        if table.casefold() in model_tables
    }
    emitted_source_write_hints: set[str] = set()
    emitted_source_import_sets: set[tuple[str, str]] = set()
    emitted_prose_guidance: set[tuple[str, tuple[str, ...]]] = set()
    emitted_link_guidance: set[tuple[str, str]] = set()
    short_id_map = build_short_result_id_map(
        [
            record.step_id
            for record in records
            if record.result_text and not record.result_id
        ]
    )
    source_records = [
        record
        for record in records
        if _record_is_batch_eligible(record) and record.succeeded
    ]
    current_source_record = (
        max(source_records, key=lambda record: record.created_at)
        if source_records
        else None
    )
    current_source_batch_id = (
        current_source_record.source_batch_id
        or current_source_record.result_id
        or short_id_map.get(current_source_record.step_id, current_source_record.step_id)
        if current_source_record
        else None
    )
    current_source_tool_name = current_source_record.tool_name if current_source_record else None
    source_work_set_ids: Dict[str, List[str]] = {}
    for record in sorted(records, key=lambda item: item.created_at):
        if (
            not record.result_text
            or not _record_is_source_bearing(record)
            or not record.succeeded
            or (
                record.source_batch_id is not None
                and record.tool_name == current_source_tool_name
                and record.source_batch_id != current_source_batch_id
            )
        ):
            continue
        source_work_set_ids.setdefault(record.tool_name, []).append(
            record.result_id or short_id_map.get(record.step_id, record.step_id)
        )

    for record in sorted(records, key=lambda item: item.created_at):
        result_text = record.result_text
        if not result_text:
            continue

        result_id = record.result_id or short_id_map.get(record.step_id, record.step_id)
        source_batch_id = record.source_batch_id or result_id
        legacy_result_id = None
        if record.result_id and record.result_id != record.step_id:
            legacy_result_id = record.step_id
        elif result_id != record.step_id and _UUID_RESULT_ID_RE.match(str(record.step_id)):
            legacy_result_id = record.step_id

        meta, stored_json, stored_text, analysis = _summarize_result(result_text, result_id, record.tool_name)
        stored_in_db = record.tool_name not in EXCLUDED_TOOL_NAMES
        is_analysis_eligible = record.tool_name.startswith(SCHEMA_ELIGIBLE_TOOL_PREFIXES)
        is_unwrapped_http_body = (
            record.tool_name == "http_request"
            and stored_text is not None
            and stored_text != result_text
        )

        recency_position = recency_positions.get(record.step_id)
        is_fresh_tool_call = record.step_id in fresh_step_ids
        is_current_source_batch = (
            current_source_batch_id is not None
            and record.succeeded
            and source_batch_id == current_source_batch_id
        )

        context_hint = None
        if is_analysis_eligible and (stored_json or (is_fresh_tool_call and meta.get("is_json"))):
            payload = _load_json_payload(stored_json, analysis)
            if payload is not None:
                json_digest = analysis.json_analysis.json_digest if analysis and analysis.json_analysis else None
                if json_digest and json_digest.action in {"skip", "inspect_manually"}:
                    payload = None
            if payload is not None:
                try:
                    context_hint = extract_context_hint(
                        record.tool_name,
                        payload,
                        allow_barbell=recency_position is not None,
                        allow_goldilocks=is_fresh_tool_call,
                        payload_bytes=meta.get("bytes"),
                    )
                except Exception:
                    pass  # Optimistic - no hint is fine
        elif (
            is_analysis_eligible
            and (is_fresh_tool_call or is_unwrapped_http_body)
            and _should_add_barbell_hint(analysis, meta)
        ):
            analysis_text = analysis.prepared_text if analysis and analysis.prepared_text is not None else result_text
            context_hint = hint_from_unstructured_text(analysis_text)

        preview_source = (
            stored_json
            if is_fresh_tool_call and stored_json is not None and meta.get("result_json_path")
            else stored_text
            if stored_text is not None
            else analysis.prepared_text if analysis and analysis.prepared_text is not None
            else result_text
        )
        work_set = source_work_set_ids.get(record.tool_name, ())
        preview_recency_position = recency_position
        if (
            stored_in_db
            and is_current_source_batch
            and _record_is_source_bearing(record)
            and not _entity_arrays(analysis)
            and 1 < len(work_set) <= MAX_ACTIVE_PROSE_PREVIEWS
            and (
                preview_recency_position is None
                or preview_recency_position >= len(PREVIEW_TIERS_EXTERNAL)
            )
        ):
            preview_recency_position = len(PREVIEW_TIERS_EXTERNAL) - 1
        preview_text, is_inline = _build_prompt_preview(
            preview_source,
            meta["bytes"],
            recency_position=preview_recency_position,
            tool_name=record.tool_name,
            is_fresh_tool_call=is_fresh_tool_call,
        )
        is_historical_same_tool_source = (
            current_source_batch_id is not None
            and not is_current_source_batch
            and record.tool_name == current_source_tool_name
            and _record_is_source_bearing(record)
        )
        if is_historical_same_tool_source:
            context_hint = None
            preview_text = None
            is_inline = False
        keep_source_import_hint = (
            _record_is_source_bearing(record)
            and is_fresh_tool_call
            and is_current_source_batch
        )
        source_import_prefix, source_write_hint_prefix = "", ""
        refresh_model_tables: set[str] = set()
        requires_source_import = False
        hide_literal_result_id = keep_source_import_hint and bool(_optional_source_array_schemas(analysis))
        if keep_source_import_hint:
            arrays = _entity_arrays(analysis)
            model_entities = {entity_name_stem(table) for table in model_tables}
            relevant_arrays = tuple(
                item for item in arrays
                if any(_is_entity_key(field) for field in item.item_fields)
                or _array_entity_stem(item.path) in model_entities
            )
            array_specs = tuple(
                (item, _source_item_key(item.item_fields))
                for item in relevant_arrays
            )
            schemas = [
                f"{item.path}({','.join(item.item_fields[:10])})"
                + (f"[stable_key={stable_key}]" if stable_key else "[no_stable_key]")
                for item, stable_key in array_specs
            ]
            exact_iterators = "; ".join(
                f"json_each(t.result_json,'{item.path.replace(chr(39), chr(39) * 2)}') AS j{index}"
                for index, (item, _stable_key) in enumerate(array_specs, start=1)
            )
            matching_arrays = tuple(
                item for item in relevant_arrays
                if _array_entity_stem(item.path) in model_entities
            )
            if schemas and matching_arrays:
                requires_source_import = True
                source_import_key = (source_batch_id, record.tool_name)
                if source_import_key not in emitted_source_import_sets:
                    source_import_prefix = (
                        f"[SOURCE ARRAYS; paths: {'; '.join(schemas)}. NEXT: one sqlite_batch only. "
                        f"Exact iterators: {exact_iterators}; keep every wrapper segment such as $.content. "
                        f"Use rows=[]. Create/evolve keyed tables; INSERT ... SELECT/json_each all is_current_batch=1 "
                        f"AND tool_name='{record.tool_name}' siblings, then SELECT bounded task rows. Use stable_key as "
                        "entity ID; t.result_id is "
                        "provenance only. Final rows preserve exact source associations. With no stable key, use newest "
                        "observed_at. No source_url/result_id or mutable-name freshness filters, pre-read, refetch, blob "
                        "inspection, copied literals, or split calls. Never delete/clear the model.]\n"
                    )
                    emitted_source_import_sets.add(source_import_key)
        if not requires_source_import and hide_literal_result_id:
            source_schemas = _optional_source_array_schemas(analysis)
            source_key = source_schemas[0][2] if source_schemas else None
            if source_key:
                refresh_model_tables = {
                    table for table, columns in model_columns.items()
                    if source_key.casefold() in columns
                }
            source_write_hint_prefix = _build_optional_source_write_hint(
                record.tool_name,
                analysis,
                tuple(refresh_model_tables),
                _load_json_payload(stored_json, analysis),
                reusable_source_set=len(source_work_set_ids.get(record.tool_name, ())) >= 2,
            )
        preserve_raw_model_source_values = bool(
            source_write_hint_prefix and refresh_model_tables
        )
        if source_write_hint_prefix in emitted_source_write_hints:
            source_write_hint_prefix = ""
        elif source_write_hint_prefix:
            emitted_source_write_hints.add(source_write_hint_prefix)
            if record.will_continue_work is True:
                # The source set stays losslessly available in __tool_results. For
                # continuing work, a second value-level copy invites transcription
                # instead of the set-wise import needed for the durable model.
                context_hint = None
                preview_text = (
                    "[NEXT: one sqlite_batch rows=[],bindings={}. CREATE/evolve the model, use result_meta's "
                    "INSERT ... SELECT, then decision SELECT in that batch. Never SELECT/inspect/copy "
                    "__tool_results first.]"
                )
                is_inline = False
        if requires_source_import:
            # A second visible copy invites literal transcription and wastes prompt space.
            context_hint = None
            preview_text = source_import_prefix or None
            is_inline = False
        has_focus = "\nFOCUS:\n" in (context_hint or "")
        if has_focus and not is_inline:
            preview_text = None
        active_url_rewriter = (
            paired_url_rewriter
            if paired_url_rewriter and record.step_id in paired_step_ids
            else url_rewriter
        )
        if active_url_rewriter and not preserve_raw_model_source_values:
            if context_hint:
                context_hint = active_url_rewriter(context_hint, record)
            if preview_text and not source_import_prefix:
                preview_text = active_url_rewriter(preview_text, record)
        has_link_tokens = "$[link:" in f"{context_hint or ''}{preview_text or ''}"
        if has_link_tokens:
            context_hint = _mark_missing_item_links(context_hint or "") or None
            preview_text = _mark_missing_item_links(preview_text or "")
        link_guidance_key = (source_batch_id, record.tool_name)
        if (
            preview_text
            and has_link_tokens
            and keep_source_import_hint
            and not source_import_prefix
            and link_guidance_key not in emitted_link_guidance
        ):
            emitted_link_guidance.add(link_guidance_key)
            preview_text = (
                "[VERIFIED LINK PRESENTATION: when presenting these sourced items, anchor each token on its exact "
                "entity name using the active surface syntax ([entity]($[link:ID]) or "
                "<a href='$[link:ID]'>entity</a>). Copy the complete `$[link:ID]` destination verbatim, including "
                "`$[link:` and `]`; never use the bare ID, a host, URL, generic "
                "'link/page', or related entity. No separate URL/link column unless requested. "
                "For an owner report with 4+ items, say "
                "Covered N/N and use one channel-appropriate table for every item/requested field. Say Not returned "
                "where a requested URL is absent. Follow any preceding source-write directive for persistence; "
                "otherwise use this visible source directly rather than creating or querying SQLite. This link "
                "guidance does not change the "
                "requested audience or action.]\n"
                f"{preview_text}"
            )
        is_scrape_markdown = _is_scrape_as_markdown_tool(record.tool_name)
        explicit_section_shape = (
            _explicit_section_shape(stored_text)
            if is_fresh_tool_call and stored_text
            else None
        )
        is_prose_work_set = (
            stored_in_db
            and is_current_source_batch
            and _record_is_source_bearing(record)
            and not _entity_arrays(analysis)
            and (not has_focus or explicit_section_shape is not None)
            and (
                is_scrape_markdown
                or len(work_set) > 1
                or (
                    is_fresh_tool_call
                    and not is_inline
                    and meta["bytes"] > FRESH_RESULT_INLINE_THRESHOLD
                )
            )
        )
        if is_scrape_markdown:
            # The page preview is the useful context; a repeated title digest competes
            # with the single work-set import instruction.
            context_hint = None
        meta_text = _format_meta_text(
            result_id,
            meta,
            analysis=(
                None
                if is_scrape_markdown or is_historical_same_tool_source
                else analysis if is_analysis_eligible else None
            ),
            stored_in_db=stored_in_db,
            result_is_inline=is_inline or has_focus,
            context_hint=context_hint,
            allow_fallback_query_hints=not is_scrape_markdown and not is_historical_same_tool_source,
            include_result_id=not hide_literal_result_id and not is_historical_same_tool_source,
        )
        terminal_directive = _terminal_result_directive(result_text, record.tool_name)
        if terminal_directive:
            meta_text += f"\n{terminal_directive}"
        if record.tool_name == "sqlite_batch" and sqlite_result_has_query_result(result_text):
            if record.will_continue_work is False and _tool_result_succeeded(result_text):
                meta_text += (
                    "\nSQLITE RESULT: terminal answer rows. Deliver now; no more tools. Preserve returned values and "
                    "associations exactly: NULL/unavailable stays unavailable, never inferred from neighboring fields."
                )
            else:
                meta_text += (
                    "\nSQLITE RESULT: use returned rows directly. If they satisfy the requested decision, deliver now; "
                    "do not reread the same model merely to validate them."
                )
        if source_write_hint_prefix:
            meta_text = f"{source_write_hint_prefix.strip()}\n{meta_text}"
        if is_historical_same_tool_source:
            meta_text += (
                "\nHISTORICAL SOURCE BATCH: excluded from is_current_batch=1 and kept in SQLite only. "
                "Query its source_batch_id only when the task explicitly requires older evidence."
            )
        if is_prose_work_set and not is_historical_same_tool_source:
            work_set = work_set or (result_id,)
            prose_guidance_key = (record.tool_name, tuple(work_set))
            if prose_guidance_key not in emitted_prose_guidance:
                emitted_prose_guidance.add(prose_guidance_key)
                source_ids = json.dumps(list(work_set), separators=(",", ":"))
                result_label = "result" if len(work_set) == 1 else "results"
                work_set_label = (
                    "SCRAPE MARKDOWN WORK SET"
                    if is_scrape_markdown
                    else "PROSE SOURCE WORK SET"
                )
                work_set_records = [
                    candidate
                    for candidate in records
                    if (
                        candidate.tool_name == record.tool_name
                        and (
                            candidate.result_id
                            or short_id_map.get(candidate.step_id, candidate.step_id)
                        ) in work_set
                    )
                ]
                previews_are_complete = bool(work_set_records) and all(
                    len(candidate.result_text.encode("utf-8")) <= SMALL_RESULT_ALWAYS_INLINE
                    for candidate in work_set_records
                )
                section_shape = (
                    explicit_section_shape
                    if len(work_set) == 1 and not previews_are_complete
                    else None
                )
                evidence_chars = max(
                    PROSE_INSPECTION_MIN_CHARS,
                    min(
                        PROSE_INSPECTION_MAX_CHARS,
                        PROSE_INSPECTION_TOTAL_CHARS // max(len(work_set), 1),
                    ),
                )
                escaped_tool_name = record.tool_name.replace("'", "''")
                inspection_query = (
                    f"`SELECT t.result_id,t.source_url,"
                    f"substr(group_concat(c.value,'\\n'),1,{evidence_chars}) AS evidence FROM "
                    "__tool_results t,json_each(grep_context_all(t.result_text,"
                    "'^(?:#{1,6}\\s|[-*]\\s*[^:\\n]{1,64}:)',160,6)) c "
                    f"WHERE t.is_current_batch=1 AND t.tool_name='{escaped_tool_name}' "
                    "GROUP BY t.result_id,t.source_url`"
                )
                inspection_contract = (
                    "(json_each aliases expose key and value, never seq). This structural Markdown pattern and exact "
                    "tool name are ready to use; do not turn them into undeclared bindings or enumerate labels. "
                    "Never select raw blobs or use read_file. These rows already give exact result_id and source_url; "
                    "do not look either up again. The next SQLite call is the final import/decision call; set "
                    "will_continue_work=false unless a specific non-SQLite action remains. "
                )
                direct_import_guidance = ""
                if section_shape:
                    field_list = ",".join(section_shape.fields[:12])
                    stable_key_pattern = f"'(?m)^{section_shape.stable_key}:[ ]*(.*)$'"
                    extraction_sql = ", ".join(
                        (
                            "regexp_extract(s.value,"
                            f"'(?m)^{re.escape(field)}:[ ]*(.*)$',1) AS \"{field}\""
                        )
                        for field in section_shape.fields[:12]
                    )
                    inspection_guidance = (
                        f"The stored source has about {section_shape.count} explicit repeated sections with recurring "
                        f"labels `{field_list}` and stable key `{section_shape.stable_key}`. PROSE CONTRACT: "
                        "`result_json` is NULL; never use `json_each/json_extract` on it. Do not inspect or return the "
                        "raw blob first. Make one terminal sqlite_batch with "
                        "`rows=[],bindings={}`: create/evolve the keyed domain model; INSERT ... SELECT requested "
                        "records from `__tool_results t,json_each(split_sections(t.result_text,"
                        f"{section_shape.delimiter_sql})) s WHERE t.is_current_batch=1 AND "
                        f"t.tool_name='{escaped_tool_name}'`; keep records where "
                        f"`regexp_extract(s.value,{stable_key_pattern},1) IS NOT NULL`, apply that expression shape "
                        f"using these ready source-derived columns: `{extraction_sql}`. Missing fields stay NULL; never "
                        "construct one from another identifier. Upsert provenance, end with the decision SELECT in "
                        "that same call, and set `will_continue_work=false` because its returned rows are the answer. "
                    )
                    direct_import_guidance = (
                        "The split-section SELECT is the final import path; do not transcribe records into `rows`, "
                        "copy source facts into SQL literals, pre-read, refetch, or later reread. "
                    )
                elif len(work_set) > 1:
                    inspection_guidance = (
                        "Before any multi-source prose model write, make exactly one compact set-wide evidence "
                        "inspection; do not import from memory or previews alone. Use it even when previews look "
                        "complete. Do not inventory IDs, URLs, tool names, or shape. Return one output row and at most "
                        f"{evidence_chars} evidence characters per source: {inspection_query} {inspection_contract}"
                    )
                elif previews_are_complete:
                    inspection_guidance = (
                        "This single-source preview is complete. Do not query __tool_results to reread it or inventory "
                        "IDs/URLs/shape; the next SQLite call is the final import/decision call. "
                    )
                else:
                    inspection_guidance = (
                        "If this preview covers the decision, make the final import/decision SQLite call immediately. "
                        "Otherwise make exactly one compact inspection with one output row and at most "
                        f"{evidence_chars} evidence characters: {inspection_query} {inspection_contract}"
                    )
                manual_import_guidance = (
                    "Final import: top-level `rows=[{\"result_id\":\"exact ID\",\"fields\":{...}},...]`, one non-empty "
                    "record per source; INSERT SELECT from `json_each(:rows) r JOIN __tool_results t ON "
                    "t.result_id=json_extract(r.value,'$.result_id')`, reading facts at $.fields.<name> and provenance "
                    "from t. "
                    if not direct_import_guidance
                    else direct_import_guidance
                )
                meta_text += (
                    f"\n{work_set_label} ({len(work_set)} {result_label}; exact source result IDs: "
                    f"`{source_ids}`): unstructured prose; this is the complete set. "
                    f"{inspection_guidance}"
                    f"{manual_import_guidance}"
                    "End with decision-ready SELECTs. Upsert stable keys; no sourced SQL literals, ID-only "
                    "rows, destructive rebuild, dump, or later reread. `fields` is evidence transcription, not "
                    "enrichment: preserve each source's specificity and omit unsupported fields. Never turn a "
                    "qualitative claim into a number, feature variant, certification level, integration, or "
                    "availability detail."
                )

        prompt_info[record.step_id] = ToolResultPromptInfo(
            result_id=result_id,
            meta=meta_text,
            preview_text=preview_text,
            is_inline=is_inline,
            source_reconciliation_directive=source_import_prefix or None,
            suppress_from_prompt=is_historical_same_tool_source,
        )

        if stored_in_db:
            analysis_json_str = None
            if analysis:
                try:
                    analysis_json_str = json.dumps(
                        analysis_to_dict(analysis),
                        ensure_ascii=True,
                        separators=(",", ":"),
                    )
                except Exception:
                    logger.debug("Failed to serialize analysis", exc_info=True)

            rows.append(
                (
                    result_id,
                    legacy_result_id,
                    record.tool_name,
                    source_batch_id,
                    1 if is_current_source_batch else 0,
                    record.source_url,
                    record.created_at.isoformat(),
                    meta["bytes"],
                    meta["line_count"],
                    1 if meta["is_json"] else 0,
                    meta["json_type"],
                    meta["top_keys"],
                    1 if meta["is_binary"] else 0,
                    1 if meta["has_images"] else 0,
                    1 if meta["has_base64"] else 0,
                    1 if meta["is_truncated"] else 0,
                    meta["truncated_bytes"],
                    stored_json,
                    analysis_json_str,
                    stored_text,
                )
            )

            if analysis and is_analysis_eligible:
                csv_info = None
                if analysis.text_analysis and analysis.text_analysis.csv_info:
                    csv_info = analysis.text_analysis.csv_info
                if csv_info and csv_info.has_header and csv_info.columns:
                    csv_candidates.append((result_id, stored_text or result_text, analysis))

    _store_tool_results(rows)

    if csv_candidates:
        _auto_load_csv_tables(csv_candidates)

    return prompt_info


def _should_add_barbell_hint(
    analysis: Optional[ResultAnalysis],
    meta: Dict[str, object],
) -> bool:
    if not analysis or analysis.is_json:
        return False
    if meta.get("is_binary"):
        return False
    text_analysis = analysis.text_analysis
    if not text_analysis:
        return False
    is_headerless_csv = bool(
        text_analysis.format == "csv"
        and text_analysis.csv_info
        and not text_analysis.csv_info.has_header
    )
    if text_analysis.format not in BARBELL_TEXT_FORMATS and not is_headerless_csv:
        return False
    if text_analysis.text_digest and text_analysis.text_digest.action == "skip":
        return False
    return meta.get("bytes", 0) > PREVIEW_TIERS_EXTERNAL[0]


def _load_json_payload(
    stored_json: Optional[str],
    analysis: Optional[ResultAnalysis],
) -> Optional[object]:
    if stored_json:
        try:
            return json.loads(stored_json)
        except Exception:
            return None
    if analysis and analysis.is_json:
        raw = analysis.normalized_json or analysis.prepared_text
        if raw:
            try:
                return json.loads(raw)
            except Exception:
                return None
    return None


def _store_tool_results(rows: Sequence[Tuple]) -> None:
    db_path = get_sqlite_db_path()
    if not db_path:
        logger.warning("SQLite DB path unavailable; tool results not stored.")
        return

    conn = None
    try:
        conn = open_guarded_sqlite_connection(db_path)
        _ensure_tool_results_table(conn)
        conn.execute(f'DELETE FROM "{TOOL_RESULTS_TABLE}";')
        if rows:
            conn.executemany(
                f"""
                INSERT OR REPLACE INTO "{TOOL_RESULTS_TABLE}" (
                    result_id,
                    legacy_result_id,
                    tool_name,
                    source_batch_id,
                    is_current_batch,
                    source_url,
                    created_at,
                    bytes,
                    line_count,
                    is_json,
                    json_type,
                    top_keys,
                    is_binary,
                    has_images,
                    has_base64,
                    is_truncated,
                    truncated_bytes,
                    result_json,
                    analysis_json,
                    result_text
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                rows,
            )
        conn.commit()
    except Exception:
        logger.exception("Failed to store tool results in SQLite.")
    finally:
        if conn is not None:
            try:
                clear_guarded_connection(conn)
                conn.close()
            except Exception:
                pass


def _ensure_tool_results_table(conn) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS "{TOOL_RESULTS_TABLE}" (
            result_id TEXT PRIMARY KEY,
            legacy_result_id TEXT,
            tool_name TEXT,
            source_batch_id TEXT,
            is_current_batch INTEGER NOT NULL DEFAULT 0,
            source_url TEXT,
            created_at TEXT,
            bytes INTEGER,
            line_count INTEGER,
            is_json INTEGER,
            json_type TEXT,
            top_keys TEXT,
            is_binary INTEGER,
            has_images INTEGER,
            has_base64 INTEGER,
            is_truncated INTEGER,
            truncated_bytes INTEGER,
            result_json TEXT,
            analysis_json TEXT,
            result_text TEXT
        )
        """
    )
    _ensure_tool_results_columns(conn)


def _ensure_tool_results_columns(conn) -> None:
    existing = {
        row[1]
        for row in conn.execute(
            f"PRAGMA table_info('{TOOL_RESULTS_TABLE}')"
        )
    }
    if "legacy_result_id" not in existing:
        conn.execute(
            f'ALTER TABLE "{TOOL_RESULTS_TABLE}" ADD COLUMN legacy_result_id TEXT;'
        )
    if "analysis_json" not in existing:
        conn.execute(
            f'ALTER TABLE "{TOOL_RESULTS_TABLE}" ADD COLUMN analysis_json TEXT;'
        )
    if "source_batch_id" not in existing:
        conn.execute(
            f'ALTER TABLE "{TOOL_RESULTS_TABLE}" ADD COLUMN source_batch_id TEXT;'
        )
    if "is_current_batch" not in existing:
        conn.execute(
            f'ALTER TABLE "{TOOL_RESULTS_TABLE}" ADD COLUMN is_current_batch INTEGER NOT NULL DEFAULT 0;'
        )
    if "source_url" not in existing:
        conn.execute(
            f'ALTER TABLE "{TOOL_RESULTS_TABLE}" ADD COLUMN source_url TEXT;'
        )


CSV_AUTO_LOAD_MAX_BYTES = 5_000_000  # 5MB
CSV_AUTO_LOAD_MAX_ROWS = 10_000
CSV_AUTO_LOAD_MAX_COLUMNS = 100


def _sanitize_column_name(col: str) -> str:
    sanitized = re.sub(r'[.\s\[\]\'"$,;:!@#%^&*()\-+=<>?/\\|`~{}]', '_', col)
    sanitized = re.sub(r'_+', '_', sanitized).strip('_')
    if not sanitized:
        return 'col'
    if sanitized[0].isdigit():
        sanitized = 'col_' + sanitized
    return sanitized


def _dedupe_column_names(columns: List[str]) -> List[str]:
    seen: Dict[str, int] = {}
    deduped: List[str] = []
    for col in columns:
        if col in seen:
            seen[col] += 1
            deduped.append(f"{col}_{seen[col]}")
        else:
            seen[col] = 1
            deduped.append(col)
    return deduped


def _auto_load_csv_tables(
    csv_candidates: List[Tuple[str, str, ResultAnalysis]],
) -> Dict[str, str]:
    if not csv_candidates:
        return {}

    db_path = get_sqlite_db_path()
    if not db_path:
        return {}

    auto_loaded: Dict[str, str] = {}
    conn = None
    try:
        conn = open_guarded_sqlite_connection(db_path)

        for result_id, result_text, analysis in csv_candidates:
            table_name = _maybe_auto_load_csv(conn, result_id, result_text, analysis)
            if table_name:
                auto_loaded[result_id] = table_name

        if auto_loaded:
            conn.commit()

    except Exception:
        logger.exception("Failed to auto-load CSV tables")
    finally:
        if conn is not None:
            try:
                clear_guarded_connection(conn)
                conn.close()
            except Exception:
                pass

    return auto_loaded


def _maybe_auto_load_csv(
    conn,
    result_id: str,
    result_text: str,
    analysis: ResultAnalysis,
) -> Optional[str]:
    csv_info = None
    if analysis.text_analysis and analysis.text_analysis.csv_info:
        csv_info = analysis.text_analysis.csv_info

    if not csv_info:
        return None
    if not csv_info.has_header:
        return None
    if not csv_info.columns:
        return None
    if len(result_text) > CSV_AUTO_LOAD_MAX_BYTES:
        return None
    if csv_info.row_count_estimate > CSV_AUTO_LOAD_MAX_ROWS:
        return None
    if len(csv_info.columns) > CSV_AUTO_LOAD_MAX_COLUMNS:
        return None

    short_id = result_id[:6]
    table_name = f"_csv_{short_id}"

    cur = conn.cursor()
    cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
    if cur.fetchone():
        return None

    sanitized_cols = [_sanitize_column_name(c) for c in csv_info.columns]
    deduped_cols = _dedupe_column_names(sanitized_cols)

    col_types = csv_info.column_types or []
    sql_types: List[str] = []
    for i in range(len(deduped_cols)):
        ctype = col_types[i] if i < len(col_types) else "text"
        sql_types.append("REAL" if ctype in ("int", "float") else "TEXT")

    extracts = ", ".join(
        f"CAST(r.value->>'{_safe_json_path(orig)}' AS {stype}) AS \"{san}\""
        for orig, san, stype in zip(csv_info.columns, deduped_cols, sql_types)
    )

    create_sql = f'''
        CREATE TABLE "{table_name}" AS
        SELECT {extracts}
        FROM "{TOOL_RESULTS_TABLE}" t, json_each(csv_parse(t.result_text)) r
        WHERE t.result_id = '{result_id}'
    '''

    try:
        conn.execute(create_sql)
        logger.debug(f"Auto-loaded CSV to table {table_name} for result {result_id}")
        return table_name
    except Exception as e:
        logger.warning(f"CSV auto-load failed for {result_id}: {e}")
        return None


def _unwrap_http_text_body(result_text: str, tool_name: str) -> str:
    if tool_name != "http_request":
        return result_text
    try:
        payload = json.loads(result_text)
    except json.JSONDecodeError:
        return result_text
    content = payload.get("content") if isinstance(payload, dict) else None
    return content if isinstance(content, str) else result_text


def _summarize_result(
    result_text: str,
    result_id: str,
    tool_name: str = "",
) -> Tuple[Dict[str, object], Optional[str], Optional[str], Optional[ResultAnalysis]]:
    result_text = _unwrap_http_text_body(result_text, tool_name)
    analysis: Optional[ResultAnalysis] = None
    try:
        analysis = analyze_result(result_text, result_id)
    except Exception:
        logger.debug("Failed to analyze tool result", exc_info=True)

    analysis_text = analysis.prepared_text if analysis and analysis.prepared_text is not None else result_text
    encoded = analysis_text.encode("utf-8")
    full_bytes = len(encoded)
    line_count = analysis_text.count("\n") + 1 if analysis_text else 0

    is_binary = _is_probably_binary(analysis_text)
    has_images = bool(_IMAGE_RE.search(analysis_text))
    has_base64 = bool(_BASE64_RE.search(result_text))

    is_json = analysis.is_json if analysis else False
    json_type = ""
    top_keys: List[str] = []

    if analysis and analysis.json_analysis:
        ja = analysis.json_analysis
        json_type = ja.pattern
        if ja.primary_array and ja.primary_array.item_fields:
            top_keys = ja.primary_array.item_fields[:MAX_TOP_KEYS]
        elif ja.primary_array and ja.primary_array.table_info and ja.primary_array.table_info.columns:
            top_keys = ja.primary_array.table_info.columns[:MAX_TOP_KEYS]
        elif ja.field_types:
            top_keys = [ft.name for ft in ja.field_types[:MAX_TOP_KEYS]]
    elif is_json:
        try:
            parsed = json.loads(result_text)
            json_type = _get_json_type(parsed)
            if isinstance(parsed, dict):
                top_keys = list(parsed.keys())[:MAX_TOP_KEYS]
        except Exception:
            pass

    if is_json and analysis and analysis.normalized_json:
        storage_text = analysis.normalized_json
    else:
        storage_text = analysis_text if is_json else analysis_text
    truncated_text, truncated_bytes = _truncate_to_bytes(storage_text, MAX_TOOL_RESULT_BYTES)
    is_truncated = truncated_bytes > 0

    result_json = truncated_text if is_json and not is_truncated else None
    result_text_store = truncated_text  # Always set for robust querying
    result_json_path = None

    if is_json and not is_truncated:
        try:
            parsed = json.loads(truncated_text)
            content = None
            if isinstance(parsed, dict):
                if tool_name == "http_request" and "content" in parsed:
                    content = parsed["content"]
                    result_json_path = "$.content"
                elif _is_scrape_as_markdown_tool(tool_name) and "result" in parsed:
                    content = parsed["result"]

            if isinstance(content, str):
                result_text_store = content
                # Scrape previews have their own barbell path and deliberately
                # keep the wrapper analysis used by that established contract.
                result_json = None
                is_json = False
                json_type = ""
                top_keys = []
            elif content is not None:
                if (
                    tool_name == "http_request"
                    and isinstance(content, dict)
                    and isinstance(content.get("text"), str)
                ):
                    # Keep the structured envelope for JSON paths while making
                    # authored page text directly searchable as text.
                    result_text_store = content["text"]
                else:
                    result_text_store = json.dumps(content, ensure_ascii=False)
        except Exception:
            pass  # Keep original on any error

    meta = {
        "bytes": full_bytes,
        "line_count": line_count,
        "is_json": is_json,
        "json_type": json_type,
        "top_keys": ",".join(top_keys),
        "is_binary": is_binary,
        "has_images": has_images,
        "has_base64": has_base64,
        "is_truncated": is_truncated,
        "truncated_bytes": truncated_bytes,
        "result_json_path": result_json_path,
    }
    if analysis and analysis.decode_info and analysis.decode_info.steps:
        meta["decoded_from"] = "+".join(analysis.decode_info.steps)
        if analysis.decode_info.encoding:
            meta["decoded_encoding"] = analysis.decode_info.encoding
    if analysis and analysis.parse_info:
        meta["parsed_from"] = analysis.parse_info.source
        meta["parsed_with"] = analysis.parse_info.mode
    return meta, result_json, result_text_store, analysis


def _tool_result_succeeded(result_text: str) -> bool:
    try:
        payload = json.loads(result_text)
    except (json.JSONDecodeError, TypeError):
        return False
    return isinstance(payload, dict) and payload.get("status") == "ok"


def sqlite_result_has_query_result(result_text: str) -> bool:
    try:
        payload = json.loads(result_text)
    except (json.JSONDecodeError, TypeError):
        return False
    results = payload.get("results") if isinstance(payload, dict) else None
    return isinstance(results, list) and any(
        isinstance(item, dict) and "result" in item
        for item in results
    )


def _terminal_result_directive(result_text: str, tool_name: str) -> str:
    if tool_name in COMPUTE_SOURCE_TOOL_NAMES:
        return ""
    try:
        payload = json.loads(result_text)
    except (json.JSONDecodeError, TypeError):
        return ""
    if not isinstance(payload, dict) or payload.get("retryable") is not False:
        return ""
    status = str(payload.get("status") or "").casefold()
    if status not in {"error", "failed", "failure", "blocked"} and payload.get("terminal_error") is not True:
        return ""
    return (
        "TERMINAL RESULT (`retryable=false`): do not call this tool again or try a query/parameter variant for this "
        "request. Use another path only when its exact tool, endpoint, or URL was known before this call; otherwise "
        "report the constraint now."
    )


def _build_prompt_preview(
    result_text: str,
    full_bytes: int,
    *,
    recency_position: Optional[int],
    tool_name: str,
    is_fresh_tool_call: bool = False,
) -> Tuple[Optional[str], bool]:
    is_sqlite = tool_name in EXCLUDED_TOOL_NAMES or tool_name.startswith("sqlite")
    if full_bytes <= SMALL_RESULT_ALWAYS_INLINE:
        return result_text, True

    tiers = PREVIEW_TIERS_SQLITE if is_sqlite else PREVIEW_TIERS_EXTERNAL
    tier_count = len(tiers)

    if is_fresh_tool_call and full_bytes <= FRESH_RESULT_INLINE_THRESHOLD:
        return f"[FULL RESULT ({full_bytes} chars) - ONE-TIME VIEW; later turns show a preview]\n{result_text}", True

    if recency_position is None or recency_position >= tier_count:
        return None, False

    max_bytes = tiers[recency_position]

    if not is_sqlite:
        if full_bytes >= HUGE_RESULT_THRESHOLD:
            max_bytes = min(max_bytes, HUGE_RESULT_PREVIEW_CAP)
        elif full_bytes >= LARGE_RESULT_THRESHOLD:
            max_bytes = min(max_bytes, LARGE_RESULT_PREVIEW_CAP)

    if full_bytes <= max_bytes:
        return result_text, True

    if is_sqlite:
        preview_text, truncated_bytes = _truncate_to_bytes(result_text, max_bytes)
    else:
        preview_text, truncated_bytes = _truncate_head_tail_to_bytes(result_text, max_bytes)
    if truncated_bytes > 0:
        if is_sqlite:
            preview_text = f"{preview_text}\n... [{truncated_bytes} more bytes truncated]"
        elif full_bytes >= HUGE_RESULT_THRESHOLD:
            kb_size = full_bytes // 1024
            preview_text = (
                f"{preview_text}\n"
                f"... [{kb_size}KB total - query only if the visible evidence is insufficient]"
            )
        else:
            preview_text = (
                f"{preview_text}\n"
                f"... [{truncated_bytes} more bytes - query only if the visible evidence is insufficient]"
            )
    return preview_text, False


def _format_meta_text(
    result_id: str,
    meta: Dict[str, object],
    *,
    analysis: Optional[ResultAnalysis],
    stored_in_db: bool,
    result_is_inline: bool = False,
    context_hint: Optional[str] = None,
    allow_fallback_query_hints: bool = True,
    include_result_id: bool = True,
) -> str:
    parts = [
        f"in_db={1 if stored_in_db else 0}",
        f"bytes={meta['bytes']}",
    ]
    if include_result_id:
        parts.insert(0, f"result_id={result_id}")

    if meta.get("is_binary"):
        parts.append("is_binary=1")
    if meta.get("has_images"):
        parts.append("has_images=1")
    if meta.get("has_base64"):
        parts.append("has_base64=1")
    if meta.get("decoded_from"):
        parts.append(f"decoded_from={meta['decoded_from']}")
    if meta.get("decoded_encoding"):
        parts.append(f"decoded_encoding={meta['decoded_encoding']}")
    if meta.get("parsed_from"):
        parts.append(f"parsed_from={meta['parsed_from']}")
    if meta.get("parsed_with"):
        parts.append(f"parsed_with={meta['parsed_with']}")
    if meta.get("result_json_path"):
        parts.append(f"result_json_path={meta['result_json_path']}")
    if meta.get("is_truncated") and meta.get("truncated_bytes"):
        parts.append(f"truncated_bytes={meta['truncated_bytes']}")

    meta_line = ", ".join(parts)

    show_query_hints = stored_in_db and not result_is_inline and include_result_id

    if analysis and analysis.compact_summary and show_query_hints:
        meta_line += "\n" + analysis.compact_summary
    elif allow_fallback_query_hints and show_query_hints and meta["bytes"] > PREVIEW_TIERS_EXTERNAL[0]:
        if meta.get("is_json"):
            meta_line += (
                f"\n[JSON: {meta.get('json_type', 'unknown')}]"
            )
            top_keys = meta.get("top_keys") or ""
            if top_keys:
                meta_line += f"\nfields: {top_keys}"
            meta_line += (
                f"\n-> json_extract(result_json,'$.field') or json_each(result_json,'$.array')"
                f"\n-> FROM __tool_results WHERE result_id='{result_id}'"
            )
        else:
            meta_line += (
                f"\n[Text: ~{meta.get('line_count', '?')} lines]"
                f"\n-> instr(result_text,'keyword') to find, substr() to extract"
                f"\n-> FROM __tool_results WHERE result_id='{result_id}'"
            )

    if context_hint:
        meta_line += f"\nFor an unrelated one-off, use visible FOCUS directly when it covers the request; query only missing facts.\n{context_hint}"

    return meta_line


def _truncate_to_bytes(text: str, max_bytes: int) -> Tuple[str, int]:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, 0
    truncated = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return truncated, len(encoded) - max_bytes


def _truncate_head_tail_to_bytes(text: str, max_bytes: int) -> Tuple[str, int]:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, 0

    marker = b"\n... [middle omitted] ...\n"
    content_budget = max(2, max_bytes - len(marker))
    head_bytes = max(1, content_budget // 4)
    tail_bytes = max(1, content_budget - head_bytes)
    head = encoded[:head_bytes].decode("utf-8", errors="ignore")
    tail = encoded[-tail_bytes:].decode("utf-8", errors="ignore")
    omitted_bytes = max(0, len(encoded) - head_bytes - tail_bytes)
    return f"{head}{marker.decode()}{tail}", omitted_bytes


def _is_probably_binary(text: str) -> bool:
    if "\x00" in text:
        return True
    sample = text[:1000]
    if not sample:
        return False
    non_printable = sum(
        1
        for ch in sample
        if ord(ch) < 9 or (ord(ch) > 13 and ord(ch) < 32)
    )
    return (non_printable / len(sample)) > 0.3
