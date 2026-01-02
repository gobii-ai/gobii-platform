import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Set, Tuple

from genson import SchemaBuilder

from ..tools.sqlite_guardrails import clear_guarded_connection, open_guarded_sqlite_connection
from ..tools.sqlite_state import TOOL_RESULTS_TABLE, get_sqlite_db_path
from ..tools.tool_manager import SQLITE_TOOL_NAME

logger = logging.getLogger(__name__)

INLINE_RESULT_MAX_BYTES = 2048
PREVIEW_MAX_BYTES = 512
LAST_N_PREVIEW = 5
MAX_TOOL_RESULT_BYTES = 5_000_000
MAX_TOP_KEYS = 20
MAX_SCHEMA_BYTES = 1_000_000

EXCLUDED_TOOL_NAMES = {SQLITE_TOOL_NAME, "sqlite_query"}

_BASE64_RE = re.compile(r"base64,", re.IGNORECASE)
_IMAGE_RE = re.compile(r"data:image/|image_base64|image_url", re.IGNORECASE)


@dataclass(frozen=True)
class ToolCallResultRecord:
    step_id: str
    tool_name: str
    created_at: datetime
    result_text: str


@dataclass(frozen=True)
class ToolResultPromptInfo:
    meta: str
    preview_text: Optional[str]
    is_inline: bool
    schema_text: Optional[str]


def prepare_tool_results_for_prompt(
    records: Sequence[ToolCallResultRecord],
    *,
    recent_preview_ids: Set[str],
) -> Dict[str, ToolResultPromptInfo]:
    prompt_info: Dict[str, ToolResultPromptInfo] = {}
    rows: List[Tuple] = []

    for record in records:
        if record.result_text is None:
            continue
        result_text = record.result_text
        if not result_text:
            continue

        meta, stored_json, stored_text, stored_schema = _summarize_result(result_text)
        stored_in_db = record.tool_name not in EXCLUDED_TOOL_NAMES

        meta_text = _format_meta_text(
            record.step_id,
            meta,
            stored_in_db=stored_in_db,
        )
        preview_text, is_inline = _build_prompt_preview(
            result_text,
            meta["bytes"],
            include_preview=record.step_id in recent_preview_ids,
        )

        prompt_info[record.step_id] = ToolResultPromptInfo(
            meta=meta_text,
            preview_text=preview_text,
            is_inline=is_inline,
            schema_text=stored_schema,
        )

        if stored_in_db:
            rows.append(
                (
                    record.step_id,
                    record.tool_name,
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
                    stored_schema,
                    stored_text,
                )
            )

    _store_tool_results(rows)
    return prompt_info


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
                    tool_name,
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
                    json_schema,
                    result_text
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
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
            tool_name TEXT,
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
            json_schema TEXT,
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
    if "json_schema" not in existing:
        conn.execute(
            f'ALTER TABLE "{TOOL_RESULTS_TABLE}" ADD COLUMN json_schema TEXT;'
        )


def _summarize_result(
    result_text: str,
) -> Tuple[Dict[str, object], Optional[str], Optional[str], Optional[str]]:
    encoded = result_text.encode("utf-8")
    full_bytes = len(encoded)
    line_count = result_text.count("\n") + 1 if result_text else 0

    is_binary = _is_probably_binary(result_text)
    has_images = bool(_IMAGE_RE.search(result_text))
    has_base64 = bool(_BASE64_RE.search(result_text))

    is_json = False
    json_type = ""
    top_keys: List[str] = []
    parsed: object | None = None
    schema_text: Optional[str] = None
    schema_bytes = 0
    schema_truncated = False
    try:
        parsed = json.loads(result_text)
        is_json = True
        json_type = _json_type(parsed)
        if isinstance(parsed, dict):
            top_keys = list(parsed.keys())[:MAX_TOP_KEYS]
    except Exception:
        pass
    if is_json and parsed is not None:
        try:
            schema_text, schema_bytes, schema_truncated = _infer_json_schema(parsed)
        except Exception:
            logger.debug("Failed to infer JSON schema for tool result.", exc_info=True)

    truncated_text, truncated_bytes = _truncate_to_bytes(result_text, MAX_TOOL_RESULT_BYTES)
    is_truncated = truncated_bytes > 0

    result_json = truncated_text if is_json and not is_truncated else None
    result_text_store = None if result_json else truncated_text

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
        "schema_bytes": schema_bytes,
        "schema_truncated": schema_truncated,
    }
    return meta, result_json, result_text_store, schema_text


def _build_prompt_preview(result_text: str, full_bytes: int, *, include_preview: bool) -> Tuple[Optional[str], bool]:
    if full_bytes <= INLINE_RESULT_MAX_BYTES:
        return result_text, True
    if not include_preview:
        return None, False
    preview_text, truncated_bytes = _truncate_to_bytes(result_text, PREVIEW_MAX_BYTES)
    if truncated_bytes > 0:
        preview_text = (
            f"{preview_text}\n... (truncated, {truncated_bytes} more bytes)"
        )
    return preview_text, False


def _format_meta_text(result_id: str, meta: Dict[str, object], *, stored_in_db: bool) -> str:
    parts = [
        f"result_id={result_id}",
        f"in_db={1 if stored_in_db else 0}",
        f"bytes={meta['bytes']}",
        f"lines={meta['line_count']}",
        f"is_json={1 if meta['is_json'] else 0}",
        f"json_type={meta['json_type'] or 'unknown'}",
    ]
    top_keys = meta.get("top_keys") or ""
    if top_keys:
        parts.append(f"top_keys={top_keys}")
    schema_bytes = meta.get("schema_bytes") or 0
    if schema_bytes:
        parts.append(f"schema_bytes={schema_bytes}")
    if meta.get("schema_truncated"):
        parts.append("schema_truncated=1")
    parts.extend(
        [
            f"is_binary={1 if meta['is_binary'] else 0}",
            f"has_images={1 if meta['has_images'] else 0}",
            f"has_base64={1 if meta['has_base64'] else 0}",
            f"truncated_bytes={meta['truncated_bytes']}",
        ]
    )
    return ", ".join(parts)


def _infer_json_schema(value: object) -> Tuple[Optional[str], int, bool]:
    builder = SchemaBuilder()
    builder.add_object(value)
    schema = builder.to_schema()
    schema_text = json.dumps(
        schema,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    schema_bytes = len(schema_text.encode("utf-8"))
    if schema_bytes > MAX_SCHEMA_BYTES:
        return None, schema_bytes, True
    return schema_text, schema_bytes, False


def _truncate_to_bytes(text: str, max_bytes: int) -> Tuple[str, int]:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, 0
    truncated = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return truncated, len(encoded) - max_bytes


def _json_type(value: object) -> str:
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if value is None:
        return "null"
    return "unknown"


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
