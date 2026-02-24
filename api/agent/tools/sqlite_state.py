"""
Shared SQLite state and helpers for persistent agents.

This module centralizes the SQLite DB context management, schema prompt
generation, and storage key logic so multiple tools (e.g., sqlite_batch)
can share the same implementation.
"""

import collections
import contextlib
import contextvars
import csv
import json
import logging
import os
import re
import shutil
import tempfile
from typing import Optional

import zstandard as zstd
from django.core.files import File
from django.core.files.storage import default_storage

from .sqlite_guardrails import clear_guarded_connection, open_guarded_sqlite_connection
from . import sqlite_analysis, sqlite_digest

logger = logging.getLogger(__name__)

# Context variable to expose the SQLite DB path to tool execution helpers
_sqlite_db_path_var: contextvars.ContextVar[str] = contextvars.ContextVar("sqlite_db_path", default=None)

TOOL_RESULTS_TABLE = "__tool_results"
AGENT_CONFIG_TABLE = "__agent_config"
KANBAN_CARDS_TABLE = "__kanban_cards"
MESSAGES_TABLE = "__messages"
FILES_TABLE = "__files"
AGENT_SKILLS_TABLE = "__agent_skills"
EPHEMERAL_TABLES = {
    TOOL_RESULTS_TABLE,
    AGENT_CONFIG_TABLE,
    KANBAN_CARDS_TABLE,
    MESSAGES_TABLE,
    FILES_TABLE,
    AGENT_SKILLS_TABLE,
}
BUILTIN_TABLE_NOTES = {
    TOOL_RESULTS_TABLE: "built-in, ephemeral (dropped before persistence)",
    AGENT_CONFIG_TABLE: "built-in, ephemeral (reset every LLM call; charter/schedule updates)",
    KANBAN_CARDS_TABLE: "built-in, ephemeral (syncs to kanban cards after tool execution)",
    MESSAGES_TABLE: "built-in, ephemeral (recent messages snapshot for this cycle)",
    FILES_TABLE: "built-in, ephemeral (recent file index for this cycle; metadata only)",
    AGENT_SKILLS_TABLE: "built-in, ephemeral (versioned skill mirror synced to persistent storage after tool execution)",
}

MAX_PROMPT_BYTES = 30000
MAX_TABLES = 25
MAX_SAMPLE_ROWS_DISPLAY = 3
MAX_SAMPLE_COLS_DISPLAY = 8
ANALYSIS_HEAD_ROWS = 30
ANALYSIS_TAIL_ROWS = 10
FULL_SAMPLE_ROW_LIMIT = 200
MAX_OFFSET_ROW_COUNT = 50000
MAX_COLUMN_SUMMARIES = 12
MAX_COLUMN_SUMMARY_CHARS = 220
MAX_VALUE_CHARS = 60
MAX_TEXT_PEEK_CHARS = 140
MAX_TEXT_PEEKS = 2
MAX_TEXT_PATTERN_VALUES = 12
MAX_JSON_KEYS = 8
MAX_JSON_PATHS = 6
MAX_NESTED_JSON_PATHS = 4
MAX_JSON_PARSE_CHARS = 8000
MAX_CREATE_STMT_CHARS = 600
MAX_CSV_SAMPLE_CHARS = 2000
MAX_CSV_COLUMNS = 8
MAX_CSV_ROWS = 6
MAX_DISTINCT_VALUES = 4
LONG_TEXT_LENGTH = 120
JSON_DETECTION_THRESHOLD = 0.6
JSON_HINT_THRESHOLD = 0.2
CSV_DETECTION_THRESHOLD = 0.4

_JSON_START_RE = re.compile(r"^\s*[\[{]")
_CSV_DELIMS = [",", "\t", "|", ";"]
_TEXT_PATTERNS = [
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("url", re.compile(r"https?://\S+|www\.[^\s]+")),
    ("uuid", re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}\b")),
    ("iso_date", re.compile(r"\b\d{4}-\d{2}-\d{2}\b")),
    ("iso_datetime", re.compile(r"\b\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}(?::\d{2})?\b")),
    ("ipv4", re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")),
    ("phone", re.compile(r"\b\+?\d[\d\s().-]{7,}\d\b")),
]


def get_sqlite_schema_prompt() -> str:
    """Return a human-readable SQLite schema summary capped to ~30 KB.

    The summary includes CREATE TABLE statements, row counts, compact samples,
    and lightweight heuristics for JSON/CSV/text patterns. It is aggressively
    bounded to avoid large prompt expansions.
    """

    db_path = _sqlite_db_path_var.get(None)
    if not db_path or not os.path.exists(db_path):
        return "SQLite database not initialised – no schema present yet."

    conn = None
    try:
        conn = open_guarded_sqlite_connection(db_path)
        cur = conn.cursor()
        cur.execute("SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name;")
        tables = cur.fetchall()

        if not tables:
            return "SQLite database has no user tables yet."

        lines: list[str] = []
        total_bytes = 0
        table_limit = min(len(tables), MAX_TABLES)
        for idx, (name, create_stmt) in enumerate(tables):
            if idx >= table_limit:
                break
            # Get row count for each table (best-effort)
            try:
                cur.execute(f"SELECT COUNT(*) FROM \"{name}\";")
                (count,) = cur.fetchone()
            except Exception:
                count = "?"
            if name == TOOL_RESULTS_TABLE and create_stmt:
                create_stmt = _redact_tool_results_schema(create_stmt)
            create_stmt_single_line = _truncate_text(
                " ".join((create_stmt or "").split()),
                MAX_CREATE_STMT_CHARS,
            )
            note = BUILTIN_TABLE_NOTES.get(name)
            if note:
                line = f"Table {name} (rows: {count}, {note}): {create_stmt_single_line}"
            else:
                line = f"Table {name} (rows: {count}): {create_stmt_single_line}"

            total_bytes, added = _append_line(lines, total_bytes, line, MAX_PROMPT_BYTES)
            if not added:
                return _finalize_truncated(lines, total_bytes, MAX_PROMPT_BYTES)

            if name in EPHEMERAL_TABLES or not isinstance(count, int) or count <= 0:
                continue

            for extra_line in _summarize_table(cur, name, count):
                total_bytes, added = _append_line(lines, total_bytes, extra_line, MAX_PROMPT_BYTES)
                if not added:
                    return _finalize_truncated(lines, total_bytes, MAX_PROMPT_BYTES)

        if len(tables) > table_limit:
            omitted = len(tables) - table_limit
            total_bytes, _ = _append_line(
                lines,
                total_bytes,
                f"... ({omitted} more tables omitted)",
                MAX_PROMPT_BYTES,
            )

        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001
        return f"Failed to inspect SQLite DB: {e}"
    finally:
        if conn is not None:
            try:
                clear_guarded_connection(conn)
                conn.close()
            except Exception:
                pass


def get_sqlite_digest_prompt() -> str:
    """Return a compact SQLite digest for the agent's database."""
    db_path = _sqlite_db_path_var.get(None)
    if not db_path or not os.path.exists(db_path):
        return "SQLite digest unavailable - no database present."

    conn = None
    try:
        conn = open_guarded_sqlite_connection(db_path)
        digest = sqlite_digest.digest_connection(conn)
        return digest.to_prompt()
    except Exception as e:  # noqa: BLE001
        return f"Failed to digest SQLite DB: {e}"
    finally:
        if conn is not None:
            try:
                clear_guarded_connection(conn)
                conn.close()
            except Exception:
                pass


def _append_line(lines: list[str], total_bytes: int, line: str, max_bytes: int) -> tuple[int, bool]:
    line_len = len(line.encode("utf-8"))
    if lines:
        line_len += 1
    if total_bytes + line_len > max_bytes:
        return total_bytes, False
    lines.append(line)
    return total_bytes + line_len, True


def _finalize_truncated(lines: list[str], total_bytes: int, max_bytes: int) -> str:
    notice = "... (truncated - schema exceeds 30KB limit)"
    total_bytes, _ = _append_line(lines, total_bytes, notice, max_bytes)
    return "\n".join(lines)


def _truncate_text(text: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    if max_chars <= 3:
        return text[:max_chars]
    return text[: max_chars - 3] + "..."


def _redact_tool_results_schema(create_stmt: str) -> str:
    cleaned = re.sub(
        r"\blegacy_result_id\s+TEXT\s*,?",
        "",
        create_stmt,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r",\s*,", ", ", cleaned)
    cleaned = re.sub(r"\(\s*,", "(", cleaned)
    cleaned = re.sub(r",\s*\)", ")", cleaned)
    return cleaned


def _compact_text(text: str, max_chars: int, *, preserve_newlines: bool = False) -> str:
    if preserve_newlines:
        text = text.replace("\r", "\\r").replace("\n", "\\n")
    text = " ".join(text.split())
    return _truncate_text(text, max_chars)


def _format_value(value: object, *, max_chars: int = MAX_VALUE_CHARS) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, bytes):
        return f"<blob {len(value)} bytes>"
    if isinstance(value, str):
        display = _compact_text(value, max_chars)
        return f"'{display}'"
    return str(value)


def _format_row(row: tuple[object, ...]) -> str:
    display_vals = list(row[:MAX_SAMPLE_COLS_DISPLAY])
    formatted_vals = [_format_value(val) for val in display_vals]
    if len(row) > MAX_SAMPLE_COLS_DISPLAY:
        formatted_vals.append(f"...+{len(row) - MAX_SAMPLE_COLS_DISPLAY} cols")
    return f"({', '.join(formatted_vals)})"


def _select_display_rows(rows: list[tuple[object, ...]]) -> list[tuple[object, ...]]:
    if not rows:
        return []
    indices = [0]
    if len(rows) > 2:
        indices.append(len(rows) // 2)
    if len(rows) > 1:
        indices.append(len(rows) - 1)
    selected: list[tuple[object, ...]] = []
    seen = set()
    for idx in indices:
        row = tuple(rows[idx])
        if row in seen:
            continue
        selected.append(row)
        seen.add(row)
        if len(selected) >= MAX_SAMPLE_ROWS_DISPLAY:
            break
    return selected


def _format_sample_rows(rows: list[tuple[object, ...]]) -> str:
    if not rows:
        return ""
    formatted_rows = [_format_row(row) for row in rows]
    return f"  sample: {', '.join(formatted_rows)}"


def _declared_type_category(declared_type: str) -> str:
    decl = (declared_type or "").upper()
    if not decl:
        return ""
    if "JSON" in decl:
        return "json"
    if "INT" in decl:
        return "int"
    if "CHAR" in decl or "TEXT" in decl or "CLOB" in decl:
        return "text"
    if "BLOB" in decl:
        return "blob"
    if "REAL" in decl or "FLOA" in decl or "DOUB" in decl:
        return "float"
    if "BOOL" in decl:
        return "bool"
    if "NUM" in decl or "DEC" in decl:
        return "num"
    return ""


def _observe_type_labels(values: list[object]) -> set[str]:
    labels = set()
    for val in values:
        if val is None:
            continue
        if isinstance(val, bool):
            labels.add("bool")
        elif isinstance(val, int):
            labels.add("int")
        elif isinstance(val, float):
            labels.add("float")
        elif isinstance(val, str):
            labels.add("text")
        elif isinstance(val, bytes):
            labels.add("blob")
        else:
            labels.add("other")
    return labels


def _safe_distinct(values: list[object]) -> list[object]:
    distinct = []
    seen = set()
    for val in values:
        try:
            key = val
            if key in seen:
                continue
            seen.add(key)
        except TypeError:
            key = repr(val)
            if key in seen:
                continue
            seen.add(key)
        distinct.append(val)
    return distinct


def _looks_like_json(text: str) -> bool:
    if not text:
        return False
    if len(text) > MAX_JSON_PARSE_CHARS:
        return False
    return _JSON_START_RE.match(text) is not None


def _safe_json_loads(text: str) -> object | None:
    if not _looks_like_json(text):
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def _json_type_label(value: object) -> str:
    if isinstance(value, dict):
        return "obj"
    if isinstance(value, list):
        return "arr"
    if isinstance(value, str):
        return "text"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "num"
    if value is None:
        return "null"
    return "other"


def _collect_json_paths(
    value: object,
    paths: list[str],
    seen: set[str],
    prefix: str = "",
    depth: int = 0,
    max_depth: int = 3,
    max_paths: int = MAX_JSON_PATHS,
) -> None:
    if depth >= max_depth or len(paths) >= max_paths:
        return
    if isinstance(value, dict):
        keys = list(value.keys())[:MAX_JSON_KEYS]
        for key in keys:
            path = f"{prefix}.{key}" if prefix else str(key)
            if path not in seen:
                paths.append(path)
                seen.add(path)
                if len(paths) >= max_paths:
                    return
            _collect_json_paths(
                value.get(key),
                paths,
                seen,
                prefix=path,
                depth=depth + 1,
                max_depth=max_depth,
                max_paths=max_paths,
            )
    elif isinstance(value, list):
        path = f"{prefix}[]" if prefix else "[]"
        if path not in seen:
            paths.append(path)
            seen.add(path)
        if value:
            _collect_json_paths(
                value[0],
                paths,
                seen,
                prefix=path,
                depth=depth + 1,
                max_depth=max_depth,
                max_paths=max_paths,
            )


def _collect_json_strings(
    value: object,
    strings: list[str],
    *,
    max_values: int = 20,
    depth: int = 0,
    max_depth: int = 3,
) -> None:
    if len(strings) >= max_values or depth >= max_depth:
        return
    if isinstance(value, dict):
        for item in list(value.values())[:MAX_JSON_KEYS]:
            _collect_json_strings(item, strings, max_values=max_values, depth=depth + 1, max_depth=max_depth)
            if len(strings) >= max_values:
                return
    elif isinstance(value, list):
        for item in value[:MAX_JSON_KEYS]:
            _collect_json_strings(item, strings, max_values=max_values, depth=depth + 1, max_depth=max_depth)
            if len(strings) >= max_values:
                return
    elif isinstance(value, str):
        strings.append(value)


def _maybe_parse_csv(text: str) -> dict[str, object] | None:
    if not text:
        return None
    sample = text.strip()
    if not sample:
        return None
    sample = sample[:MAX_CSV_SAMPLE_CHARS]
    lines = sample.splitlines()
    if not lines:
        return None
    first_line = lines[0]
    delim = max(_CSV_DELIMS, key=lambda d: first_line.count(d))
    if first_line.count(delim) < 1:
        return None

    rows = []
    try:
        reader = csv.reader(lines, delimiter=delim)
        for row in reader:
            if row:
                rows.append(row)
            if len(rows) >= MAX_CSV_ROWS:
                break
    except Exception:
        return None

    if len(rows) < 1:
        return None
    col_counts = {len(row) for row in rows}
    if len(col_counts) != 1:
        return None
    col_count = col_counts.pop()
    if col_count < 2:
        return None

    header = None
    if len(rows) >= 2 and _looks_like_header(rows[0], rows[1]):
        header = [_compact_text(item, 20) for item in rows[0][:MAX_CSV_COLUMNS]]

    return {
        "delimiter": delim,
        "col_count": col_count,
        "header": header,
        "row_count": len(rows),
    }


def _looks_like_number(value: str) -> bool:
    try:
        float(value)
        return True
    except Exception:
        return False


def _looks_like_header(first_row: list[str], second_row: list[str]) -> bool:
    if len(first_row) != len(second_row) or not first_row:
        return False
    first_numeric = sum(_looks_like_number(val) for val in first_row)
    second_numeric = sum(_looks_like_number(val) for val in second_row)
    if first_numeric == 0 and second_numeric >= max(1, len(second_row) // 2):
        return True
    if len(set(first_row)) == len(first_row) and first_numeric == 0:
        return True
    return False


def _analyze_csv_values(values: list[str]) -> dict[str, object] | None:
    if not values:
        return None
    infos = []
    for val in values:
        info = _maybe_parse_csv(val)
        if info:
            infos.append(info)
        if len(infos) >= 3:
            break
    if not infos:
        return None
    ratio = len(infos) / max(1, len(values))
    col_counter = collections.Counter(info["col_count"] for info in infos)
    col_count = col_counter.most_common(1)[0][0]
    header = None
    for info in infos:
        if info.get("header"):
            header = info["header"]
            break
    return {
        "ratio": ratio,
        "col_count": col_count,
        "header": header,
    }


def _analyze_text_patterns(values: list[str]) -> list[str]:
    counts = {name: 0 for name, _ in _TEXT_PATTERNS}
    for text in values:
        for name, pattern in _TEXT_PATTERNS:
            try:
                if pattern.search(text):
                    counts[name] += 1
            except Exception:
                continue
    matched = [(name, count) for name, count in counts.items() if count > 0]
    matched.sort(key=lambda item: (-item[1], item[0]))
    return [name for name, _ in matched[:4]]


def _analyze_json_values(values: list[str]) -> dict[str, object] | None:
    if not values:
        return None
    parsed = []
    for text in values:
        obj = _safe_json_loads(text)
        if obj is not None:
            parsed.append(obj)
    if not parsed:
        return None
    ratio = len(parsed) / max(1, len(values))
    if ratio < JSON_DETECTION_THRESHOLD:
        return {"ratio": ratio}

    type_counts = collections.Counter(_json_type_label(obj) for obj in parsed)
    kind = type_counts.most_common(1)[0][0] if type_counts else "mixed"
    keys = collections.Counter()
    paths: list[str] = []
    path_seen: set[str] = set()
    array_lengths = []
    elem_types = collections.Counter()

    for obj in parsed[:3]:
        if isinstance(obj, dict):
            keys.update(obj.keys())
            _collect_json_paths(obj, paths, path_seen)
        elif isinstance(obj, list):
            array_lengths.append(len(obj))
            for item in obj[:MAX_JSON_KEYS]:
                elem_types.update([_json_type_label(item)])
            _collect_json_paths(obj, paths, path_seen)

    nested_json_count = 0
    nested_paths: list[str] = []
    nested_path_seen: set[str] = set()
    csv_in_json_count = 0

    string_values: list[str] = []
    for obj in parsed[:3]:
        _collect_json_strings(obj, string_values, max_values=20)

    for text in string_values:
        nested = _safe_json_loads(text)
        if nested is not None:
            nested_json_count += 1
            if len(nested_paths) < MAX_NESTED_JSON_PATHS:
                _collect_json_paths(
                    nested,
                    nested_paths,
                    nested_path_seen,
                    max_paths=MAX_NESTED_JSON_PATHS,
                )
        if _maybe_parse_csv(text):
            csv_in_json_count += 1

    return {
        "ratio": ratio,
        "kind": kind,
        "keys": [str(k) for k, _ in keys.most_common(MAX_JSON_KEYS)],
        "paths": paths,
        "array_lengths": array_lengths,
        "elem_types": [t for t, _ in elem_types.most_common(3)],
        "nested_json_count": nested_json_count,
        "nested_paths": nested_paths,
        "csv_in_json_count": csv_in_json_count,
    }


def _format_json_summary(info: dict[str, object]) -> str:
    parts = ["json"]
    kind = info.get("kind")
    if kind:
        parts.append(str(kind))
    keys = info.get("keys") or []
    if keys:
        parts.append(f"keys[{', '.join(keys)}]")
    paths = info.get("paths") or []
    if paths:
        parts.append(f"paths[{', '.join(paths)}]")
    elem_types = info.get("elem_types") or []
    if elem_types:
        parts.append(f"elem_types[{', '.join(elem_types)}]")
    array_lengths = info.get("array_lengths") or []
    if array_lengths:
        parts.append(f"len {min(array_lengths)}-{max(array_lengths)}")
    nested_json_count = info.get("nested_json_count", 0)
    if nested_json_count:
        parts.append(f"nested_json={nested_json_count}")
    nested_paths = info.get("nested_paths") or []
    if nested_paths:
        parts.append(f"nested_paths[{', '.join(nested_paths)}]")
    csv_in_json_count = info.get("csv_in_json_count", 0)
    if csv_in_json_count:
        parts.append(f"csv_in_json={csv_in_json_count}")
    return " ".join(parts)


def _format_csv_summary(info: dict[str, object], *, tentative: bool = False) -> str:
    parts = ["csv?" if tentative else "csv"]
    col_count = info.get("col_count")
    if col_count:
        parts.append(f"cols={col_count}")
    header = info.get("header")
    if header:
        parts.append(f"header[{', '.join(header)}]")
    return " ".join(parts)


def _format_number(value: object) -> str:
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _summarize_table(cur, table_name: str, row_count: int) -> list[str]:
    lines: list[str] = []
    try:
        cur.execute(f"PRAGMA table_info(\"{table_name}\");")
        columns = [(row[1], row[2] or "") for row in cur.fetchall()]
    except Exception:
        return lines
    if not columns:
        return lines

    # Fetch rows for analysis and display
    analysis_rows = _fetch_analysis_rows(cur, table_name, row_count)

    # Sample rows display (keep for quick reference)
    display_rows = _select_display_rows(analysis_rows)
    sample_line = _format_sample_rows(display_rows)
    if sample_line:
        lines.append(sample_line)

    # Use deep analysis for comprehensive insights
    try:
        table_analysis = sqlite_analysis.analyze_table(cur, table_name, row_count)
        deep_lines = sqlite_analysis.format_table_analysis(table_analysis)
        if deep_lines:
            lines.extend(deep_lines)
    except Exception as e:
        # Fall back to legacy analysis on error
        logger.debug(f"Deep analysis failed for {table_name}, using legacy: {e}")
        column_insights = _analyze_columns(columns, analysis_rows, row_count)
        summaries = _select_column_summaries(column_insights)
        if summaries:
            summary_text = "; ".join(summaries)
            summary_text = _truncate_text(summary_text, MAX_PROMPT_BYTES // 2)
            lines.append(f"  profile: {summary_text}")

        text_peeks = _collect_text_peeks(column_insights)
        if text_peeks:
            lines.append(f"  text_peek: {text_peeks}")

    return lines


def _fetch_analysis_rows(cur, table_name: str, row_count: int) -> list[tuple[object, ...]]:
    if row_count <= 0:
        return []
    try:
        if row_count <= FULL_SAMPLE_ROW_LIMIT:
            cur.execute(f"SELECT * FROM \"{table_name}\";")
            return [tuple(row) for row in cur.fetchall()]
        rows: list[tuple[object, ...]] = []
        cur.execute(f"SELECT * FROM \"{table_name}\" LIMIT {ANALYSIS_HEAD_ROWS};")
        rows.extend(tuple(row) for row in cur.fetchall())
        if row_count > ANALYSIS_HEAD_ROWS and row_count <= MAX_OFFSET_ROW_COUNT:
            tail_rows = min(ANALYSIS_TAIL_ROWS, row_count - ANALYSIS_HEAD_ROWS)
            if tail_rows > 0:
                offset = max(row_count - tail_rows, 0)
                cur.execute(f"SELECT * FROM \"{table_name}\" LIMIT {tail_rows} OFFSET {offset};")
                rows.extend(tuple(row) for row in cur.fetchall())
        return rows
    except Exception:
        return []


def _analyze_columns(
    columns: list[tuple[str, str]],
    rows: list[tuple[object, ...]],
    row_count: int,
) -> list[dict[str, object]]:
    if not columns:
        return []
    values_by_col: list[list[object]] = [[] for _ in columns]
    for row in rows:
        if len(row) < len(columns):
            continue
        for idx, _ in enumerate(columns):
            values_by_col[idx].append(row[idx])

    sample_complete = row_count <= FULL_SAMPLE_ROW_LIMIT and len(rows) == row_count
    insights: list[dict[str, object]] = []

    for idx, (col_name, col_type) in enumerate(columns):
        values = values_by_col[idx]
        insight = _analyze_column(col_name, col_type, values, row_count, sample_complete)
        insight["index"] = idx
        insights.append(insight)

    return insights


def _analyze_column(
    col_name: str,
    col_type: str,
    values: list[object],
    row_count: int,
    sample_complete: bool,
) -> dict[str, object]:
    sample_size = len(values)
    non_null = [val for val in values if val is not None]
    text_values = [val for val in non_null if isinstance(val, str)]
    null_ratio = 0.0
    if sample_size:
        null_ratio = (sample_size - len(non_null)) / sample_size

    type_labels = _observe_type_labels(non_null)
    mixed_types = len(type_labels) > 1
    declared_category = _declared_type_category(col_type)

    json_info = None
    json_hint_ratio = 0.0
    csv_info = None
    text_patterns = []
    text_len = None
    text_max_len = 0
    text_peek = None
    numeric_range = None
    values_summary = None

    if text_values:
        json_info = _analyze_json_values(text_values)
        if json_info and json_info.get("ratio", 0) < JSON_DETECTION_THRESHOLD:
            ratio = float(json_info.get("ratio", 0))
            if ratio >= JSON_HINT_THRESHOLD:
                json_hint_ratio = ratio
            json_info = None

    if json_info is None and text_values:
        csv_info = _analyze_csv_values(text_values)
        text_patterns = _analyze_text_patterns(text_values[:MAX_TEXT_PATTERN_VALUES])
        lengths = [len(val) for val in text_values]
        if lengths:
            text_len = (min(lengths), max(lengths))
            text_max_len = text_len[1]
            if text_max_len >= LONG_TEXT_LENGTH or any("\n" in val for val in text_values):
                peek_source = text_values[:2]
                if peek_source:
                    text_peek = " | ".join(
                        _compact_text(item, MAX_TEXT_PEEK_CHARS, preserve_newlines=True)
                        for item in peek_source
                    )

    if not json_info and not csv_info:
        numeric_values = [
            val for val in non_null if isinstance(val, (int, float)) and not isinstance(val, bool)
        ]
        if numeric_values:
            numeric_range = (min(numeric_values), max(numeric_values))

    if non_null:
        distinct_vals = _safe_distinct(non_null)
        if len(distinct_vals) <= MAX_DISTINCT_VALUES:
            formatted = [
                _truncate_text(_format_value(val, max_chars=20), 20)
                for val in distinct_vals
            ]
            values_summary = f"values[{', '.join(formatted)}]"

    type_label = None
    priority = 10
    summary_parts = []

    if json_info:
        type_label = "json"
        summary_parts.append(_format_json_summary(json_info))
        priority = 100
    elif csv_info and csv_info.get("ratio", 0) >= CSV_DETECTION_THRESHOLD:
        type_label = "csv"
        summary_parts.append(_format_csv_summary(csv_info))
        priority = 90
    else:
        if csv_info:
            summary_parts.append(_format_csv_summary(csv_info, tentative=True))
            priority = max(priority, 80)
        if text_values:
            type_label = type_label or "text"
            if json_hint_ratio:
                summary_parts.append(f"json? {int(round(json_hint_ratio * 100))}%")
            if text_len:
                summary_parts.append(f"len {text_len[0]}-{text_len[1]}")
            if text_patterns:
                summary_parts.append(f"patterns {','.join(text_patterns)}")
            if text_peek:
                priority = max(priority, 80)
        if numeric_range:
            type_label = type_label or "num"
            summary_parts.append(f"range { _format_number(numeric_range[0]) }-{ _format_number(numeric_range[1]) }")
            priority = max(priority, 60)

    if values_summary:
        summary_parts.append(values_summary)

    if mixed_types:
        summary_parts.append(f"types {','.join(sorted(type_labels))}")

    if declared_category and type_label:
        numeric_compatible = declared_category in {"int", "float", "num"} and type_label == "num"
        text_compatible = declared_category == "text" and type_label in {"json", "csv", "text"}
        if not numeric_compatible and not text_compatible and declared_category != type_label:
            summary_parts.append(f"decl {declared_category}")

    if sample_size and null_ratio >= 0.1:
        null_pct = int(round(null_ratio * 100))
        prefix = "nulls~" if not sample_complete else "nulls"
        summary_parts.append(f"{prefix} {null_pct}%")

    summary = f"{col_name}: " + "; ".join(summary_parts) if summary_parts else col_name
    summary = _truncate_text(summary, MAX_COLUMN_SUMMARY_CHARS)

    return {
        "name": col_name,
        "summary": summary,
        "priority": priority,
        "text_peek": text_peek,
        "text_max_len": text_max_len,
        "type_label": type_label,
    }


def _select_column_summaries(insights: list[dict[str, object]]) -> list[str]:
    if not insights:
        return []
    ranked = sorted(insights, key=lambda item: item.get("priority", 0), reverse=True)
    selected = ranked[:MAX_COLUMN_SUMMARIES]
    selected = sorted(selected, key=lambda item: item.get("index", 0))
    return [item.get("summary", "") for item in selected if item.get("summary")]


def _collect_text_peeks(insights: list[dict[str, object]]) -> str:
    candidates = [
        item for item in insights
        if item.get("text_peek")
    ]
    if not candidates:
        return ""
    candidates.sort(key=lambda item: item.get("text_max_len", 0), reverse=True)
    parts = []
    for item in candidates[:MAX_TEXT_PEEKS]:
        name = item.get("name")
        peek = item.get("text_peek")
        if not name or not peek:
            continue
        safe_peek = peek.replace('"', "'")
        parts.append(f"{name}=\"{safe_peek}\"")
    return "; ".join(parts)


def set_sqlite_db_path(db_path: str) -> contextvars.Token:
    """Set the SQLite DB path in the context variable."""
    return _sqlite_db_path_var.set(db_path)


def get_sqlite_db_path() -> Optional[str]:
    """Return the current SQLite DB path from context, if available."""
    return _sqlite_db_path_var.get(None)


def reset_sqlite_db_path(token: contextvars.Token) -> None:
    """Reset the SQLite DB path context variable."""
    try:
        _sqlite_db_path_var.reset(token)
    except Exception:
        pass


def sqlite_storage_key(agent_uuid: str) -> str:
    """Return hierarchical object key for a persistent agent SQLite DB archive."""
    clean_uuid = str(agent_uuid).replace("-", "")
    return f"agent_state/{clean_uuid[:2]}/{clean_uuid[2:4]}/{agent_uuid}.db.zst"


@contextlib.contextmanager
def agent_sqlite_db(agent_uuid: str):  # noqa: D401 – simple generator context mgr
    """Context manager that restores/persists the per-agent SQLite DB.

    1. Attempts to download and decompress the DB from object storage.
    2. Yields the on-disk path to the SQLite file in a temporary directory.
    3. On exit, runs maintenance (VACUUM/PRAGMA optimize), then compresses
       the DB with zstd and uploads to object storage, unless the DB grew
       beyond 100MB, in which case we wipe persisted state.
    """
    storage_key = sqlite_storage_key(agent_uuid)

    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "state.db")

        # ---------------- Restore phase ---------------- #
        if default_storage.exists(storage_key):
            try:
                with default_storage.open(storage_key, "rb") as src:
                    dctx = zstd.ZstdDecompressor()
                    with dctx.stream_reader(src) as reader, open(db_path, "wb") as dst:
                        shutil.copyfileobj(reader, dst)
            except Exception:
                logger.warning(
                    "Failed to restore SQLite DB for agent %s – starting fresh.",
                    agent_uuid,
                    exc_info=True,
                )

        token = set_sqlite_db_path(db_path)

        try:
            yield db_path
        finally:
            if os.path.exists(db_path):
                try:
                    conn = open_guarded_sqlite_connection(db_path, allow_attach=True)
                    try:
                        _drop_ephemeral_tables(conn)
                        conn.execute("VACUUM;")
                        try:
                            conn.execute("PRAGMA optimize;")
                        except Exception:
                            pass
                        conn.commit()
                    finally:
                        try:
                            clear_guarded_connection(conn)
                            conn.close()
                        except Exception:
                            pass
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "SQLite maintenance (VACUUM/optimize) failed for agent %s",
                        agent_uuid,
                        exc_info=True,
                    )

                db_size_bytes = os.path.getsize(db_path)
                db_size_mb = db_size_bytes / (1024 * 1024)

                if db_size_mb > 100:
                    logger.info(
                        "SQLite DB for agent %s exceeds 100MB (%.2f MB) - wiping database instead of persisting",
                        agent_uuid,
                        db_size_mb,
                    )
                    if default_storage.exists(storage_key):
                        default_storage.delete(storage_key)
                else:
                    tmp_zst_path = db_path + ".zst"
                    try:
                        cctx = zstd.ZstdCompressor(level=3)
                        with open(db_path, "rb") as f_in, open(tmp_zst_path, "wb") as f_out:
                            cctx.copy_stream(f_in, f_out)

                        if default_storage.exists(storage_key):
                            default_storage.delete(storage_key)

                        with open(tmp_zst_path, "rb") as f_in:
                            default_storage.save(storage_key, File(f_in))
                    except Exception:  # noqa: BLE001
                        logger.exception(
                            "Failed to persist SQLite DB for agent %s", agent_uuid
                        )
                    finally:
                        try:
                            os.remove(tmp_zst_path)
                        except Exception:
                            pass

            reset_sqlite_db_path(token)


def _drop_ephemeral_tables(conn) -> None:
    for table_name in EPHEMERAL_TABLES:
        try:
            conn.execute(f'DROP TABLE IF EXISTS "{table_name}";')
        except Exception:
            logger.debug("Failed to drop ephemeral table %s", table_name, exc_info=True)
