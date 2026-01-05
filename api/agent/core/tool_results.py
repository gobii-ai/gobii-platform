import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Set, Tuple

from ..tools.context_hints import extract_context_hint, hint_from_unstructured_text
from ..tools.sqlite_guardrails import clear_guarded_connection, open_guarded_sqlite_connection
from ..tools.sqlite_state import TOOL_RESULTS_TABLE, get_sqlite_db_path
from ..tools.tool_manager import SQLITE_TOOL_NAME
from .result_analysis import ResultAnalysis, analyze_result, analysis_to_dict

logger = logging.getLogger(__name__)

# Tiered preview system for EXTERNAL data (http_request, mcp_* tools)
# These are structure hints only - agent must use SQLite to extract data.
# Position 0: structure hint (active result)
# Position 1-2: brief structure hint
# Position 3+: meta only (query via sqlite)
PREVIEW_TIERS_EXTERNAL = [
    512,    # Position 0: 512B - Structure hint only
    256,    # Position 1: 256B - Brief hint
    256,    # Position 2: 256B - Brief hint
    # Position 3+: None (meta only - use query)
]

# For large external results, reduce preview to force query usage
LARGE_RESULT_THRESHOLD = 5_000   # 5KB - start capping early
LARGE_RESULT_PREVIEW_CAP = 200   # Max 200 bytes for large external results

# For very large results, be aggressive - minimal structure hint only
HUGE_RESULT_THRESHOLD = 15_000   # 15KB - this is already a lot of text
HUGE_RESULT_PREVIEW_CAP = 100    # Minimal preview - rely on analysis hints

# SQLite results get MUCH more generous previews - this IS the extracted data
# the agent needs to work with. Show full results up to reasonable limits.
PREVIEW_TIERS_SQLITE = [
    16384,  # Position 0: 16KB - Show full query result
    8192,   # Position 1: 8KB - Recent query results
    4096,   # Position 2: 4KB - Older query results
    2048,   # Position 3: 2KB
    1024,   # Position 4: 1KB
    # Position 5+: None (very old)
]

PREVIEW_TIER_COUNT = max(len(PREVIEW_TIERS_EXTERNAL), len(PREVIEW_TIERS_SQLITE))

MAX_TOOL_RESULT_BYTES = 5_000_000
MAX_TOP_KEYS = 20

EXCLUDED_TOOL_NAMES = {SQLITE_TOOL_NAME, "sqlite_query"}

# Tools that fetch external data with unknown structure - schema generation helps agent query it
SCHEMA_ELIGIBLE_TOOL_PREFIXES = ("http_request", "mcp_")

_BASE64_RE = re.compile(r"base64,", re.IGNORECASE)
_IMAGE_RE = re.compile(r"data:image/|image_base64|image_url", re.IGNORECASE)
BARBELL_TEXT_FORMATS = frozenset({"html", "markdown", "plain", "log"})


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
    fresh_tool_call_step_id: Optional[str] = None,
) -> Dict[str, ToolResultPromptInfo]:
    prompt_info: Dict[str, ToolResultPromptInfo] = {}
    rows: List[Tuple] = []

    for record in records:
        if record.result_text is None:
            continue
        result_text = record.result_text
        if not result_text:
            continue

        meta, stored_json, stored_text, analysis = _summarize_result(
            result_text, record.step_id
        )
        stored_in_db = record.tool_name not in EXCLUDED_TOOL_NAMES
        # Only show rich analysis for tools that fetch external data with unknown structure
        is_analysis_eligible = record.tool_name.startswith(SCHEMA_ELIGIBLE_TOOL_PREFIXES)

        recency_position = recency_positions.get(record.step_id)
        is_fresh_tool_call = bool(
            fresh_tool_call_step_id and record.step_id == fresh_tool_call_step_id
        )

        # Extract context hint for lightning-fast agent decisions
        # This is optimistic - if extraction fails, we just skip it
        context_hint = None
        if is_analysis_eligible and (stored_json or (is_fresh_tool_call and meta.get("is_json"))):
            payload = _load_json_payload(stored_json, analysis)
            if payload is not None:
                json_digest = None
                if analysis and analysis.json_analysis:
                    json_digest = analysis.json_analysis.json_digest
                if json_digest and json_digest.action in {"skip", "inspect_manually"}:
                    payload = None
            if payload is not None:
                try:
                    context_hint = extract_context_hint(
                        record.tool_name,
                        payload,
                        allow_barbell=is_fresh_tool_call,
                        allow_goldilocks=is_fresh_tool_call,
                        payload_bytes=meta.get("bytes"),
                    )
                except Exception:
                    pass  # Optimistic - no hint is fine
        elif is_analysis_eligible and is_fresh_tool_call and _should_add_barbell_hint(analysis, meta):
            analysis_text = analysis.prepared_text if analysis and analysis.prepared_text is not None else result_text
            context_hint = hint_from_unstructured_text(analysis_text)

        meta_text = _format_meta_text(
            record.step_id,
            meta,
            analysis=analysis if is_analysis_eligible else None,
            stored_in_db=stored_in_db,
            context_hint=context_hint,
        )
        preview_source = analysis.prepared_text if analysis and analysis.prepared_text is not None else result_text
        preview_text, is_inline = _build_prompt_preview(
            preview_source,
            meta["bytes"],
            recency_position=recency_position,
            tool_name=record.tool_name,
        )

        prompt_info[record.step_id] = ToolResultPromptInfo(
            meta=meta_text,
            preview_text=preview_text,
            is_inline=is_inline,
            schema_text=None,  # Replaced by analysis in meta_text
        )

        if stored_in_db:
            # Serialize analysis for storage
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
                    analysis_json_str,
                    stored_text,
                )
            )

    _store_tool_results(rows)
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
    if not text_analysis or text_analysis.format not in BARBELL_TEXT_FORMATS:
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
                    analysis_json,
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
    # Migration: add analysis_json column if missing
    if "analysis_json" not in existing:
        conn.execute(
            f'ALTER TABLE "{TOOL_RESULTS_TABLE}" ADD COLUMN analysis_json TEXT;'
        )


def _summarize_result(
    result_text: str,
    result_id: str,
) -> Tuple[Dict[str, object], Optional[str], Optional[str], Optional[ResultAnalysis]]:
    """Summarize a tool result and perform rich analysis.

    Returns:
        Tuple of (meta dict, result_json for storage, result_text for storage, analysis)
    """
    # Perform rich analysis
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

    # Extract basic JSON info
    is_json = analysis.is_json if analysis else False
    json_type = ""
    top_keys: List[str] = []

    if analysis and analysis.json_analysis:
        ja = analysis.json_analysis
        json_type = ja.pattern
        # Get top keys from primary array or field types
        if ja.primary_array and ja.primary_array.item_fields:
            top_keys = ja.primary_array.item_fields[:MAX_TOP_KEYS]
        elif ja.primary_array and ja.primary_array.table_info and ja.primary_array.table_info.columns:
            top_keys = ja.primary_array.table_info.columns[:MAX_TOP_KEYS]
        elif ja.field_types:
            top_keys = [ft.name for ft in ja.field_types[:MAX_TOP_KEYS]]
    elif is_json:
        # Fallback: parse and extract basic info
        try:
            parsed = json.loads(result_text)
            json_type = _json_type(parsed)
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
    }
    if analysis and analysis.decode_info and analysis.decode_info.steps:
        meta["decoded_from"] = "+".join(analysis.decode_info.steps)
        if analysis.decode_info.encoding:
            meta["decoded_encoding"] = analysis.decode_info.encoding
    if analysis and analysis.parse_info:
        meta["parsed_from"] = analysis.parse_info.source
        meta["parsed_with"] = analysis.parse_info.mode
    return meta, result_json, result_text_store, analysis


def _build_prompt_preview(
    result_text: str,
    full_bytes: int,
    *,
    recency_position: Optional[int],
    tool_name: str,
) -> Tuple[Optional[str], bool]:
    """Build a preview for the prompt.

    For external data (http_request, mcp_*): small structure hints only.
    For sqlite results: generous preview since this IS the extracted data.

    Returns (preview_text, is_inline) where:
    - preview_text is a sample of the result
    - is_inline is True only for small results that fit entirely
    """
    # Determine which tier system to use
    is_sqlite = tool_name in EXCLUDED_TOOL_NAMES or tool_name.startswith("sqlite")
    tiers = PREVIEW_TIERS_SQLITE if is_sqlite else PREVIEW_TIERS_EXTERNAL
    tier_count = len(tiers)

    # No position means meta only (old result beyond tier range)
    if recency_position is None or recency_position >= tier_count:
        return None, False

    max_bytes = tiers[recency_position]

    # For large EXTERNAL results, cap preview to force query usage
    # (Don't cap sqlite results - agent needs to see query output)
    if not is_sqlite:
        if full_bytes >= HUGE_RESULT_THRESHOLD:
            # Very large result - minimal preview, rely on analysis hints
            max_bytes = min(max_bytes, HUGE_RESULT_PREVIEW_CAP)
        elif full_bytes >= LARGE_RESULT_THRESHOLD:
            max_bytes = min(max_bytes, LARGE_RESULT_PREVIEW_CAP)

    # If result fits within tier limit, show full (inline)
    if full_bytes <= max_bytes:
        return result_text, True

    # Truncate with appropriate guidance
    preview_text, truncated_bytes = _truncate_to_bytes(result_text, max_bytes)
    if truncated_bytes > 0:
        if is_sqlite:
            # SQLite result - just note truncation, no "use query" since this IS the query result
            preview_text = f"{preview_text}\n... [{truncated_bytes} more bytes truncated]"
        elif full_bytes >= HUGE_RESULT_THRESHOLD:
            # Huge external data - strong guidance to use chunked extraction
            kb_size = full_bytes // 1024
            preview_text = (
                f"{preview_text}\n"
                f"... [{kb_size}KB total - USE substr(col,1,2000) to extract chunks]"
            )
        else:
            # External data - remind to use query
            preview_text = (
                f"{preview_text}\n"
                f"... [{truncated_bytes} more bytes - USE QUERY ABOVE to access full data]"
            )
    return preview_text, False


def _format_meta_text(
    result_id: str,
    meta: Dict[str, object],
    *,
    analysis: Optional[ResultAnalysis],
    stored_in_db: bool,
    context_hint: Optional[str] = None,
) -> str:
    """Format metadata and analysis into actionable text for the prompt.

    When analysis is available, uses the compact summary with ready-to-use
    query patterns. Falls back to basic meta info otherwise.
    """
    # Basic meta line (always present)
    parts = [
        f"result_id={result_id}",
        f"in_db={1 if stored_in_db else 0}",
        f"bytes={meta['bytes']}",
    ]

    # Add binary/image flags only if present
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
    if meta.get("is_truncated") and meta.get("truncated_bytes"):
        parts.append(f"truncated_bytes={meta['truncated_bytes']}")

    meta_line = ", ".join(parts)

    # If we have rich analysis, use the compact summary
    if analysis and analysis.compact_summary and stored_in_db:
        meta_line += "\n" + analysis.compact_summary
    elif stored_in_db and meta["bytes"] > PREVIEW_TIERS_EXTERNAL[0]:
        # Fallback: basic query hints for large results without analysis
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

    # Append context hint if available (optimistic - only when extraction succeeded)
    # This gives the agent immediate actionable info without extra extraction steps
    if context_hint:
        meta_line += f"\n{context_hint}"

    return meta_line


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
