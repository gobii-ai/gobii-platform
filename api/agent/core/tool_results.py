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

# Tiered preview system - exponential taper by recency
# Position 0: generous preview (active result)
# Position 1-2: medium preview (recent context)
# Position 3-4: small preview (memory jog)
# Position 5+: meta only (query via sqlite if needed)
PREVIEW_TIERS = [
    4096,   # Position 0: 4KB - "I'm working with this now"
    1024,   # Position 1: 1KB - "Recent context"
    1024,   # Position 2: 1KB - "Recent context"
    256,    # Position 3: 256B - "I remember this"
    256,    # Position 4: 256B - "I remember this"
    # Position 5+: None (meta only)
]
PREVIEW_TIER_COUNT = len(PREVIEW_TIERS)

MAX_TOOL_RESULT_BYTES = 5_000_000
MAX_TOP_KEYS = 20
MAX_SCHEMA_BYTES = 1_000_000

EXCLUDED_TOOL_NAMES = {SQLITE_TOOL_NAME, "sqlite_query"}

# Tools that fetch external data with unknown structure - schema generation helps agent query it
SCHEMA_ELIGIBLE_TOOL_PREFIXES = ("http_request", "mcp_")

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
    recency_positions: Dict[str, int],
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
        # Only show schema for tools that fetch external data with unknown structure
        is_schema_eligible = record.tool_name.startswith(SCHEMA_ELIGIBLE_TOOL_PREFIXES)
        prompt_schema = stored_schema if is_schema_eligible else None

        meta_text = _format_meta_text(
            record.step_id,
            meta,
            stored_in_db=stored_in_db,
            is_json=meta["is_json"],  # For query hint - JSON vs text query syntax
        )
        recency_position = recency_positions.get(record.step_id)
        preview_text, is_inline = _build_prompt_preview(
            result_text,
            meta["bytes"],
            recency_position=recency_position,
        )

        prompt_info[record.step_id] = ToolResultPromptInfo(
            meta=meta_text,
            preview_text=preview_text,
            is_inline=is_inline,
            schema_text=prompt_schema,
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
    schema_target = _extract_json_payload_for_schema(parsed) if is_json else None
    if schema_target is not None:
        if isinstance(schema_target, dict):
            top_keys = list(schema_target.keys())[:MAX_TOP_KEYS]
        json_type = _json_type(schema_target)
        try:
            schema_text, schema_bytes, schema_truncated = _infer_json_schema(schema_target)
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


def _build_prompt_preview(
    result_text: str, full_bytes: int, *, recency_position: Optional[int]
) -> Tuple[Optional[str], bool]:
    # No position means meta only (old result beyond tier range)
    if recency_position is None or recency_position >= PREVIEW_TIER_COUNT:
        return None, False

    max_bytes = PREVIEW_TIERS[recency_position]

    # If result fits within tier limit, show full (inline)
    if full_bytes <= max_bytes:
        return result_text, True

    # Otherwise truncate to tier limit
    preview_text, truncated_bytes = _truncate_to_bytes(result_text, max_bytes)
    if truncated_bytes > 0:
        preview_text = (
            f"{preview_text}\n... (truncated, {truncated_bytes} more bytes)"
        )
    return preview_text, False


def _format_meta_text(
    result_id: str,
    meta: Dict[str, object],
    *,
    stored_in_db: bool,
    is_json: bool = False,
) -> str:
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
    meta_line = ", ".join(parts)
    # Add query hint for large results stored in DB (exceeds most generous tier)
    if stored_in_db and meta["bytes"] > PREVIEW_TIERS[0]:
        if is_json:
            meta_line += (
                f"\n→ Use sqlite_batch to query/analyze this result: "
                f"SELECT json_extract(result_json, '$.key') FROM __tool_results WHERE result_id='{result_id}'"
            )
        else:
            meta_line += (
                f"\n→ Use sqlite_batch to query/analyze this result: "
                f"SELECT substr(result_text, 1, 500), instr(result_text, 'keyword') FROM __tool_results WHERE result_id='{result_id}'"
            )
    return meta_line


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


def _extract_json_payload_for_schema(value: object | None) -> object | None:
    def unwrap_json_container(candidate: object | None) -> object | None:
        if isinstance(candidate, (dict, list)):
            return candidate
        if isinstance(candidate, str):
            stripped = candidate.lstrip()
            if not stripped or stripped[0] not in "{[":
                return None
            try:
                parsed = json.loads(candidate)
            except Exception:
                return None
            if isinstance(parsed, (dict, list)):
                return parsed
        return None

    def looks_like_sqlite_envelope(container: dict) -> bool:
        if "db_size_mb" in container:
            return True
        results = container.get("results")
        if not isinstance(results, list) or not results:
            return False
        for item in results:
            if not isinstance(item, dict):
                return False
            item_keys = set(item.keys())
            if not item_keys or item_keys - {"message", "result", "error"}:
                return False
        return True

    def looks_like_status_envelope(container: dict) -> bool:
        if "status" not in container:
            return False
        envelope_keys = {
            "status",
            "message",
            "message_id",
            "error",
            "errors",
            "details",
            "results",
            "db_size_mb",
            "headers",
            "status_code",
            "proxy_used",
            "auto_sleep_ok",
            "tool_manager",
            "created_count",
            "already_allowed_count",
            "already_pending_count",
            "approval_url",
            "filename",
            "path",
            "node_id",
            "task_id",
            "conversation_id",
            "step_id",
        }
        return not (set(container.keys()) - envelope_keys)

    payload_keys = ("content", "data", "result", "payload", "response")

    if isinstance(value, str):
        value = unwrap_json_container(value)
        if value is None:
            return None
    if isinstance(value, dict):
        for key in payload_keys:
            if key in value:
                candidate = unwrap_json_container(value.get(key))
                if candidate is not None:
                    return candidate
        if looks_like_sqlite_envelope(value) or looks_like_status_envelope(value):
            return None
        return value
    if isinstance(value, list):
        return value
    return None


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
