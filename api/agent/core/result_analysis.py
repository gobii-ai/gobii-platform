"""
Rich metadata analysis for tool results.

Analyzes JSON and text data to extract actionable query patterns,
structure information, and hints that help agents write correct SQL queries.
"""

import base64
import binascii
import csv
import gzip
import io
import json
import re
import urllib.parse
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

import json5
from bs4 import BeautifulSoup
from charset_normalizer import from_bytes

# Size thresholds for strategy recommendations
SIZE_SMALL = 4 * 1024        # 4KB - can inline fully
SIZE_MEDIUM = 50 * 1024      # 50KB - targeted extraction
SIZE_LARGE = 500 * 1024      # 500KB - aggregate first
# Above 500KB = huge, must chunk

# Limits for analysis to avoid performance issues
MAX_ARRAY_SCAN = 1000        # Max items to scan in an array
MAX_DEPTH = 10               # Max nesting depth to analyze
MAX_FIELDS = 50              # Max fields to report
MAX_SAMPLE_BYTES = 300       # Max bytes for sample values
MAX_EMBEDDED_SCAN_DEPTH = 6
MAX_EMBEDDED_SCAN_LIST_ITEMS = 25
MAX_EMBEDDED_CANDIDATES = 50
MAX_EMBEDDED_STRING_BYTES = 50000
MAX_JSON_EXTRACT_BYTES = 200000
MIN_EMBEDDED_CHARS = 20
MAX_JSON_CANDIDATES = 20
MAX_BASE64_CHARS = 3000000
MAX_DECODED_BYTES = 2000000
MAX_HTML_SCAN_BYTES = 500000
MAX_JSON_LINES_SCAN = 50
MAX_SSE_SCAN_LINES = 200

_JSON_PREFIXES = (
    ")]}',",
    ")]}'",
    "while(1);",
    "for(;;);",
    "/*-secure-*/",
)

_PREFERRED_ARRAY_KEYS = {
    "items",
    "results",
    "data",
    "content",
    "rows",
    "records",
    "entries",
    "children",
}


@dataclass
class TableInfo:
    """Information about JSON arrays of arrays (tabular data)."""
    has_header: bool = False
    columns: List[str] = field(default_factory=list)
    row_count: int = 0
    column_types: List[str] = field(default_factory=list)
    sample_rows: List[str] = field(default_factory=list)


@dataclass
class ArrayInfo:
    """Information about an array found in JSON."""
    path: str
    length: int
    item_fields: List[str] = field(default_factory=list)
    item_sample: Optional[str] = None
    nested_arrays: List[str] = field(default_factory=list)  # paths relative to item
    item_data_key: Optional[str] = None  # e.g., "data" if items are {"kind": ..., "data": {...actual fields...}}
    table_info: Optional[TableInfo] = None


@dataclass
class FieldTypeInfo:
    """Type information for a field."""
    name: str
    json_type: str  # string, number, boolean, null, array, object
    inferred_type: Optional[str] = None  # datetime, email, url, numeric_string, etc.


@dataclass
class PaginationInfo:
    """Detected pagination structure."""
    detected: bool = False
    pagination_type: Optional[str] = None  # cursor, offset, page
    next_field: Optional[str] = None
    total_field: Optional[str] = None
    has_more_field: Optional[str] = None
    page_field: Optional[str] = None
    limit_field: Optional[str] = None


@dataclass
class CsvInfo:
    """Information about CSV-formatted text."""
    delimiter: str = ","
    has_header: bool = True
    columns: List[str] = field(default_factory=list)
    row_count_estimate: int = 0
    sample_rows: List[str] = field(default_factory=list)  # First 2-3 data rows (not header)
    column_types: List[str] = field(default_factory=list)  # Inferred: int, float, text

@dataclass
class DocStructure:
    """Structure information for markdown/HTML documents."""
    sections: List[Dict[str, Any]] = field(default_factory=list)  # [{heading, position}]
    has_tables: bool = False
    has_code_blocks: bool = False
    has_lists: bool = False


@dataclass
class XmlInfo:
    """Structure information for XML documents."""
    root_tag: Optional[str] = None
    element_count: int = 0
    depth: int = 0


@dataclass
class TextHints:
    """Hints for searching/extracting from text."""
    key_positions: Dict[str, int] = field(default_factory=dict)  # keyword -> first position
    line_count: int = 0
    avg_line_length: int = 0


@dataclass
class JsonLinesInfo:
    """Summary information for newline-delimited JSON."""
    line_count: int = 0
    parsed_line_count: int = 0
    fields: List[str] = field(default_factory=list)
    sample_objects: List[str] = field(default_factory=list)


@dataclass
class SseInfo:
    """Summary information for Server-Sent Events."""
    data_line_count: int = 0
    event_count_estimate: int = 0
    json_fields: List[str] = field(default_factory=list)


@dataclass
class SizeStrategy:
    """Size-based query strategy recommendation."""
    category: str  # small, medium, large, huge
    bytes: int
    recommendation: str  # direct_query, targeted_extract, aggregate_first, chunked
    warning: Optional[str] = None


@dataclass
class DetectedPatterns:
    """Common data patterns detected."""
    api_response: bool = False
    error_present: bool = False
    empty_result: bool = False
    single_item: bool = False
    collection: bool = False


@dataclass
class EmbeddedContent:
    """Structured content embedded in a JSON string field (e.g., CSV in $.content)."""
    path: str  # e.g., "$.content"
    format: str  # csv, json_lines, etc.
    confidence: float = 0.0
    csv_info: Optional[CsvInfo] = None
    doc_structure: Optional[DocStructure] = None
    xml_info: Optional[XmlInfo] = None
    json_info: Optional["EmbeddedJsonInfo"] = None
    line_count: int = 0
    byte_size: int = 0


@dataclass
class EmbeddedJsonInfo:
    """Summary information for JSON embedded as a string."""
    pattern: str
    wrapper_path: Optional[str] = None
    primary_array_path: Optional[str] = None
    primary_array_length: Optional[int] = None
    primary_array_fields: List[str] = field(default_factory=list)
    object_fields: List[str] = field(default_factory=list)


@dataclass
class QueryPatterns:
    """Ready-to-use SQL query patterns."""
    list_all: Optional[str] = None
    count: Optional[str] = None
    sample: Optional[str] = None
    filter_template: Optional[str] = None


@dataclass
class DecodeInfo:
    """Information about decoded payloads (base64, gzip, etc.)."""
    steps: List[str] = field(default_factory=list)
    bytes_before: int = 0
    bytes_after: int = 0
    encoding: Optional[str] = None


@dataclass
class ParseInfo:
    """Information about how JSON was parsed/extracted."""
    mode: str = "json"  # json, json5
    source: str = "raw"  # raw, jsonp, html, data_url, base64, urlencoded, extracted


@dataclass
class JsonAnalysis:
    """Complete analysis of JSON data."""
    pattern: str  # paginated_list, array, single_object, nested_collection, unknown
    wrapper_path: Optional[str] = None  # path to unwrap (e.g., $.content)
    primary_array: Optional[ArrayInfo] = None
    secondary_arrays: List[ArrayInfo] = field(default_factory=list)
    scalar_fields: List[str] = field(default_factory=list)
    field_types: List[FieldTypeInfo] = field(default_factory=list)
    pagination: Optional[PaginationInfo] = None
    detected_patterns: Optional[DetectedPatterns] = None
    embedded_content: Optional[EmbeddedContent] = None  # Backward-compatible first hit
    embedded_contents: List[EmbeddedContent] = field(default_factory=list)


@dataclass
class TextAnalysis:
    """Complete analysis of text data."""
    format: str  # csv, markdown, html, log, json_lines, sse, xml, plain
    confidence: float = 0.0
    csv_info: Optional[CsvInfo] = None
    doc_structure: Optional[DocStructure] = None
    xml_info: Optional[XmlInfo] = None
    text_hints: Optional[TextHints] = None
    json_lines_info: Optional[JsonLinesInfo] = None
    sse_info: Optional[SseInfo] = None


@dataclass
class ResultAnalysis:
    """Complete analysis result."""
    is_json: bool
    size_strategy: SizeStrategy
    json_analysis: Optional[JsonAnalysis] = None
    text_analysis: Optional[TextAnalysis] = None
    query_patterns: Optional[QueryPatterns] = None
    compact_summary: str = ""  # One-line summary for prompt
    decode_info: Optional[DecodeInfo] = None
    parse_info: Optional[ParseInfo] = None
    prepared_text: Optional[str] = None
    normalized_json: Optional[str] = None


@dataclass
class PreparedResult:
    """Prepared payload and parse metadata for analysis/storage."""
    analysis_text: str
    parsed_json: Optional[Any] = None
    is_json: bool = False
    normalized_json: Optional[str] = None
    decode_info: Optional[DecodeInfo] = None
    parse_info: Optional[ParseInfo] = None


# ---------------------------------------------------------------------------
# Payload preparation
# ---------------------------------------------------------------------------

def _is_text_like(text: str) -> bool:
    if not text:
        return False
    sample = text[:1000]
    if not sample:
        return False
    non_printable = sum(
        1
        for ch in sample
        if ord(ch) < 9 or (ord(ch) > 13 and ord(ch) < 32)
    )
    return (non_printable / len(sample)) < 0.2


def _decode_bytes_to_text(data: bytes) -> Tuple[Optional[str], Optional[str]]:
    if not data:
        return None, None
    match = from_bytes(data).best()
    if match is not None:
        text = str(match)
        if _is_text_like(text):
            return text, match.encoding
    try:
        text = data.decode("utf-8", errors="replace")
        if _is_text_like(text):
            return text, "utf-8"
    except Exception:
        pass
    return None, None


def _safe_gzip_decompress(data: bytes) -> Optional[bytes]:
    if not data:
        return None
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as gz:
            out = gz.read(MAX_DECODED_BYTES + 1)
        if len(out) > MAX_DECODED_BYTES:
            return None
        return out
    except Exception:
        return None


def _extract_data_url(text: str) -> Tuple[Optional[bytes], Optional[str]]:
    if not text:
        return None, None
    if not text.startswith("data:"):
        return None, None
    header, sep, payload = text.partition(",")
    if not sep:
        return None, None
    if ";base64" not in header.lower():
        return None, None
    if len(payload) > MAX_BASE64_CHARS:
        return None, None
    try:
        decoded = base64.b64decode(payload, validate=True)
        return decoded, header
    except (binascii.Error, ValueError):
        return None, None


def _looks_like_base64(text: str) -> bool:
    if not text:
        return False
    candidate = "".join(text.split())
    if len(candidate) < 16 or len(candidate) > MAX_BASE64_CHARS:
        return False
    if len(candidate) % 4 != 0:
        return False
    if not re.fullmatch(r"[A-Za-z0-9+/=]+", candidate):
        return False
    return True


def _decode_base64_text(text: str) -> Tuple[Optional[str], Optional[DecodeInfo]]:
    if not text:
        return None, None
    data_bytes, _ = _extract_data_url(text.strip())
    steps = []
    bytes_before = len(text.encode("utf-8"))
    if data_bytes is not None:
        steps.append("base64")
    elif _looks_like_base64(text):
        candidate = "".join(text.split())
        try:
            data_bytes = base64.b64decode(candidate, validate=True)
            steps.append("base64")
        except (binascii.Error, ValueError):
            data_bytes = None
    if data_bytes is None:
        return None, None

    if data_bytes.startswith(b"\x1f\x8b"):
        decompressed = _safe_gzip_decompress(data_bytes)
        if decompressed:
            data_bytes = decompressed
            steps.append("gzip")

    decoded_text, encoding = _decode_bytes_to_text(data_bytes)
    if not decoded_text:
        return None, None

    info = DecodeInfo(
        steps=steps,
        bytes_before=bytes_before,
        bytes_after=len(decoded_text.encode("utf-8")),
        encoding=encoding,
    )
    return decoded_text, info


def _parse_json_any(text: str) -> Tuple[Optional[Any], Optional[str]]:
    if not text:
        return None, None
    try:
        return json.loads(text), "json"
    except Exception:
        pass
    try:
        return json5.loads(text), "json5"
    except Exception:
        return None, None


def _unwrap_jsonp(text: str) -> Optional[str]:
    if not text:
        return None
    stripped = text.strip()
    match = re.match(r"^[$\w\.]+\s*\(", stripped)
    if not match:
        return None
    start = match.end() - 1
    depth = 0
    in_str = False
    quote_char = ""
    escape = False
    for idx in range(start, len(stripped)):
        ch = stripped[idx]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == quote_char:
                in_str = False
            continue
        if ch in ("\"", "'"):
            in_str = True
            quote_char = ch
            continue
        if ch == "(":
            depth += 1
            continue
        if ch == ")":
            depth -= 1
            if depth == 0:
                payload = stripped[start + 1:idx]
                return payload.strip()
    return None


def _extract_first_json_block(text: str) -> Optional[str]:
    if not text:
        return None
    start_idx = None
    for idx, ch in enumerate(text):
        if ch in "{[":
            start_idx = idx
            break
    if start_idx is None:
        return None
    limit = min(len(text), start_idx + MAX_JSON_EXTRACT_BYTES)
    stack = []
    in_str = False
    quote_char = ""
    escape = False
    for idx in range(start_idx, limit):
        ch = text[idx]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == quote_char:
                in_str = False
            continue
        if ch in ("\"", "'"):
            in_str = True
            quote_char = ch
            continue
        if ch in "{[":
            stack.append(ch)
            continue
        if ch in "}]":
            if not stack:
                break
            opener = stack.pop()
            if (opener == "{" and ch != "}") or (opener == "[" and ch != "]"):
                return None
            if not stack:
                return text[start_idx:idx + 1]
    return None


def _looks_like_html(text: str) -> bool:
    if not text:
        return False
    sample = text[:2000].lower()
    return "<html" in sample or "<body" in sample or "<script" in sample


def _extract_json_from_html(text: str) -> List[Tuple[str, str]]:
    if not text or not _looks_like_html(text):
        return []
    scan_text = text if len(text) <= MAX_HTML_SCAN_BYTES else text[:MAX_HTML_SCAN_BYTES]
    soup = BeautifulSoup(scan_text, "html.parser")
    candidates: List[Tuple[str, str]] = []

    for script in soup.find_all("script"):
        script_text = script.string or script.get_text()
        if not script_text:
            continue
        script_text = script_text.strip()
        if not script_text:
            continue
        script_type = (script.get("type") or "").lower()
        script_id = (script.get("id") or "").lower()
        if "json" in script_type or script_id in {"__next_data__", "__nuxt__"}:
            candidates.append((script_text, "html_script"))
            continue
        if "__next_data__" in script_text or "__nuxt__" in script_text:
            extracted = _extract_first_json_block(script_text)
            if extracted:
                candidates.append((extracted, "html_script"))
                continue
        if "=" in script_text and "{" in script_text:
            extracted = _extract_first_json_block(script_text)
            if extracted:
                candidates.append((extracted, "html_script"))

    for pre in soup.find_all("pre"):
        pre_text = pre.get_text().strip()
        if pre_text.startswith("{") or pre_text.startswith("["):
            candidates.append((pre_text, "html_pre"))

    return candidates[:MAX_JSON_CANDIDATES]


def _extract_urlencoded_json(text: str) -> Optional[str]:
    if not text:
        return None
    if "%7b" not in text.lower() and "%5b" not in text.lower():
        return None
    decoded = urllib.parse.unquote_plus(text)
    decoded = decoded.strip()
    if decoded.startswith("{") or decoded.startswith("["):
        return decoded
    if "=" not in decoded:
        return None
    pairs = urllib.parse.parse_qs(decoded, keep_blank_values=True)
    for values in pairs.values():
        for value in values:
            if not value:
                continue
            candidate = value.strip()
            if candidate.startswith("{") or candidate.startswith("["):
                return candidate
    return None


def prepare_result_text(result_text: str) -> PreparedResult:
    raw_text = result_text or ""
    if _analyze_json_lines(raw_text) or _analyze_sse(raw_text):
        return PreparedResult(analysis_text=raw_text, parsed_json=None, is_json=False)
    candidates: List[Tuple[str, str, Optional[DecodeInfo]]] = []

    candidates.append((raw_text, "raw", None))

    jsonp = _unwrap_jsonp(raw_text)
    if jsonp:
        candidates.append((jsonp, "jsonp", None))

    urlencoded = _extract_urlencoded_json(raw_text)
    if urlencoded:
        candidates.append((urlencoded, "urlencoded", None))

    for candidate, source in _extract_json_from_html(raw_text):
        candidates.append((candidate, source, None))

    extracted = _extract_first_json_block(raw_text)
    if extracted and extracted != raw_text:
        candidates.append((extracted, "extracted", None))

    decoded_text, decode_info = _decode_base64_text(raw_text)
    if decoded_text:
        if _analyze_json_lines(decoded_text) or _analyze_sse(decoded_text):
            return PreparedResult(
                analysis_text=decoded_text,
                parsed_json=None,
                is_json=False,
                decode_info=decode_info,
            )
        candidates.append((decoded_text, "base64", decode_info))

    for text, source, info in candidates[:MAX_JSON_CANDIDATES]:
        parsed, mode = _parse_json_any(_strip_json_prefixes(text).strip())
        if parsed is not None:
            normalized = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
            return PreparedResult(
                analysis_text=normalized,
                parsed_json=parsed,
                is_json=True,
                normalized_json=normalized,
                decode_info=info,
                parse_info=ParseInfo(mode=mode or "json", source=source),
            )

    if decoded_text:
        return PreparedResult(
            analysis_text=decoded_text,
            parsed_json=None,
            is_json=False,
            normalized_json=None,
            decode_info=decode_info,
        )

    return PreparedResult(analysis_text=raw_text, parsed_json=None, is_json=False)


# ---------------------------------------------------------------------------
# JSON Analysis
# ---------------------------------------------------------------------------

def _get_json_type(value: Any) -> str:
    """Get JSON type name for a value."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "unknown"


def _infer_string_type(value: str) -> Optional[str]:
    """Infer semantic type from string value."""
    if not value or len(value) > 500:
        return None

    # ISO datetime
    if re.match(r'^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}', value):
        return "datetime"
    # Date only
    if re.match(r'^\d{4}-\d{2}-\d{2}$', value):
        return "date"
    # Email
    if re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', value):
        return "email"
    # URL
    if re.match(r'^https?://', value):
        return "url"
    # Numeric string
    if re.match(r'^-?\d+\.?\d*$', value) and not value.startswith('0'):
        return "numeric_string"
    # UUID
    if re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', value.lower()):
        return "uuid"

    return None


def _truncate_sample(value: Any, max_bytes: int = MAX_SAMPLE_BYTES) -> str:
    """Create a truncated JSON sample."""
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
        if len(text) <= max_bytes:
            return text
        return text[:max_bytes - 3] + "..."
    except Exception:
        return ""


def _strip_json_prefixes(text: str) -> str:
    """Remove common anti-CSRF prefixes from JSON responses."""
    if not text:
        return text
    stripped = text.lstrip("\ufeff")
    trimmed = stripped.lstrip()
    for prefix in _JSON_PREFIXES:
        if trimmed.startswith(prefix):
            return trimmed[len(prefix):].lstrip()
    return trimmed


def _try_parse_json_string(text: str) -> Optional[Any]:
    """Parse JSON from a string if it looks like a standalone JSON payload."""
    if not text:
        return None
    candidate = _strip_json_prefixes(text).strip()
    if not candidate:
        return None
    if candidate[0] not in "{[":
        return None
    if candidate[-1] not in "}]":
        return None
    if len(candidate) > MAX_JSON_EXTRACT_BYTES:
        return None
    parsed, _ = _parse_json_any(candidate)
    return parsed


def normalize_json_text(text: str) -> Optional[str]:
    """Return a cleaned JSON payload if the text is JSON with known prefixes."""
    if not text:
        return None
    candidate = _strip_json_prefixes(text).strip()
    if not candidate:
        return None
    if candidate[0] not in "{[" or candidate[-1] not in "}]":
        return None
    parsed, _ = _parse_json_any(candidate)
    if parsed is None:
        return None
    return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))


def _analyze_array_item_fields(items: List[Any], max_items: int = 5) -> Tuple[List[str], List[FieldTypeInfo], Optional[str], Optional[str]]:
    """Analyze fields from array items, handling heterogeneous items.

    Also detects "data wrapper" pattern where items are like {"kind": "t3", "data": {...actual fields...}}
    and returns the nested path prefix if found.

    Returns: (field_names, field_type_infos, sample_json, nested_data_key)
    """
    if not items:
        return [], [], None, None

    field_counts: Dict[str, int] = {}
    field_types: Dict[str, set] = {}
    field_inferred: Dict[str, set] = {}

    sample_item = None
    nested_data_key = None  # e.g., "data" if items are {"kind": ..., "data": {...}}

    for i, item in enumerate(items[:max_items]):
        if not isinstance(item, dict):
            continue
        if sample_item is None:
            sample_item = item

        # Detect data wrapper pattern: item has 1-3 keys, one is a nested object with many fields
        # Common patterns: {"data": {...}}, {"kind": "x", "data": {...}}, {"type": "x", "attributes": {...}}
        if len(item) <= 3:
            wrapper_keys = ["data", "attributes", "item", "record", "node", "properties"]
            for wkey in wrapper_keys:
                if wkey in item and isinstance(item[wkey], dict) and len(item[wkey]) >= 3:
                    nested_data_key = wkey
                    break

        for key, val in item.items():
            field_counts[key] = field_counts.get(key, 0) + 1
            jtype = _get_json_type(val)
            if key not in field_types:
                field_types[key] = set()
            field_types[key].add(jtype)
            if jtype == "string" and isinstance(val, str):
                inferred = _infer_string_type(val)
                if inferred:
                    if key not in field_inferred:
                        field_inferred[key] = set()
                    field_inferred[key].add(inferred)

    # If we detected a data wrapper, analyze fields inside it instead
    if nested_data_key and sample_item and nested_data_key in sample_item:
        nested_obj = sample_item[nested_data_key]
        if isinstance(nested_obj, dict):
            # Re-analyze using the nested object's fields
            nested_items = [item.get(nested_data_key) for item in items[:max_items]
                           if isinstance(item, dict) and isinstance(item.get(nested_data_key), dict)]
            if nested_items:
                # Recursively analyze the nested objects (without further nesting detection)
                nested_fields, nested_types, nested_sample, _ = _analyze_array_item_fields_simple(nested_items)
                return nested_fields, nested_types, nested_sample, nested_data_key

    # Sort by frequency
    sorted_fields = sorted(field_counts.keys(), key=lambda k: -field_counts[k])[:MAX_FIELDS]

    type_infos = []
    for fname in sorted_fields:
        types = field_types.get(fname, set())
        primary_type = types.pop() if len(types) == 1 else "mixed"
        inferred = None
        if fname in field_inferred and len(field_inferred[fname]) == 1:
            inferred = field_inferred[fname].pop()
        type_infos.append(FieldTypeInfo(name=fname, json_type=primary_type, inferred_type=inferred))

    sample_str = _truncate_sample(sample_item) if sample_item else None
    return sorted_fields, type_infos, sample_str, None


def _analyze_array_item_fields_simple(items: List[Any], max_items: int = 5) -> Tuple[List[str], List[FieldTypeInfo], Optional[str], None]:
    """Simple field analysis without nested data detection (to avoid infinite recursion)."""
    if not items:
        return [], [], None, None

    field_counts: Dict[str, int] = {}
    field_types: Dict[str, set] = {}
    field_inferred: Dict[str, set] = {}

    sample_item = None
    for i, item in enumerate(items[:max_items]):
        if not isinstance(item, dict):
            continue
        if sample_item is None:
            sample_item = item
        for key, val in item.items():
            field_counts[key] = field_counts.get(key, 0) + 1
            jtype = _get_json_type(val)
            if key not in field_types:
                field_types[key] = set()
            field_types[key].add(jtype)
            if jtype == "string" and isinstance(val, str):
                inferred = _infer_string_type(val)
                if inferred:
                    if key not in field_inferred:
                        field_inferred[key] = set()
                    field_inferred[key].add(inferred)

    sorted_fields = sorted(field_counts.keys(), key=lambda k: -field_counts[k])[:MAX_FIELDS]

    type_infos = []
    for fname in sorted_fields:
        types = field_types.get(fname, set())
        primary_type = types.pop() if len(types) == 1 else "mixed"
        inferred = None
        if fname in field_inferred and len(field_inferred[fname]) == 1:
            inferred = field_inferred[fname].pop()
        type_infos.append(FieldTypeInfo(name=fname, json_type=primary_type, inferred_type=inferred))

    sample_str = _truncate_sample(sample_item) if sample_item else None
    return sorted_fields, type_infos, sample_str, None


def _find_nested_arrays(item: Dict, prefix: str = "") -> List[str]:
    """Find array fields within an object."""
    arrays = []
    if not isinstance(item, dict):
        return arrays
    for key, val in item.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(val, list) and len(val) > 0:
            arrays.append(path)
    return arrays[:5]  # Limit nested array reporting


def _detect_table_array(arr: List[Any]) -> Optional[TableInfo]:
    """Detect array-of-arrays structures that represent tabular data."""
    if not arr:
        return None
    sample_rows = [row for row in arr[:10] if isinstance(row, list)]
    if len(sample_rows) < 2:
        return None
    lengths = [len(row) for row in sample_rows if row]
    if not lengths:
        return None
    length_counts: Dict[int, int] = {}
    for length in lengths:
        length_counts[length] = length_counts.get(length, 0) + 1
    common_len = max(length_counts, key=length_counts.get)
    if common_len < 2:
        return None
    sample_rows = [row for row in sample_rows if len(row) == common_len]
    if len(sample_rows) < 2:
        return None

    header_row = sample_rows[0]
    header_cells = [
        cell for cell in header_row
        if isinstance(cell, str) and cell.strip() and len(cell) < 50
    ]
    numeric_headers = sum(
        1
        for cell in header_row
        if isinstance(cell, str) and re.match(r'^-?\d+(\.\d+)?$', cell.strip())
    )
    has_header = len(header_cells) == common_len and numeric_headers < max(1, common_len // 2)

    if has_header:
        columns = [cell.strip() for cell in header_row]
        data_rows = sample_rows[1:4]
        row_count = max(0, len(arr) - 1)
    else:
        columns = [f"col{i + 1}" for i in range(common_len)]
        data_rows = sample_rows[:3]
        row_count = len(arr)

    column_types: List[str] = []
    if data_rows:
        col_values = [[] for _ in columns]
        for row in data_rows:
            for idx, val in enumerate(row[:len(columns)]):
                col_values[idx].append(str(val) if val is not None else "")
        column_types = [_infer_column_type(values) for values in col_values]

    sample_rows_text = [json.dumps(row, ensure_ascii=False)[:300] for row in data_rows]

    return TableInfo(
        has_header=has_header,
        columns=columns[:20],
        row_count=row_count,
        column_types=column_types[:20],
        sample_rows=sample_rows_text,
    )


def _analyze_array(arr: List, path: str) -> ArrayInfo:
    """Analyze a JSON array."""
    length = len(arr)
    table_info = _detect_table_array(arr)
    if table_info:
        return ArrayInfo(
            path=path,
            length=length,
            table_info=table_info,
        )
    item_fields, field_types, sample, item_data_key = _analyze_array_item_fields(arr)

    # Check for nested arrays in first item
    nested = []
    if arr and isinstance(arr[0], dict):
        # If we detected a data wrapper, look for nested arrays inside it
        check_item = arr[0]
        if item_data_key and item_data_key in check_item:
            check_item = check_item[item_data_key]
        if isinstance(check_item, dict):
            nested = _find_nested_arrays(check_item)

    return ArrayInfo(
        path=path,
        length=length,
        item_fields=item_fields,
        item_sample=sample,
        nested_arrays=nested,
        item_data_key=item_data_key,
    )


def _detect_wrapper_path(data: Dict) -> Tuple[Optional[str], Any]:
    """Detect common API response wrappers and return unwrapped payload."""
    wrapper_keys = ["content", "data", "result", "results", "payload", "response", "body", "items"]

    # Check for status envelope
    if "status" in data and len(data) <= 5:
        for key in wrapper_keys:
            if key in data:
                val = data[key]
                if isinstance(val, (dict, list)):
                    return f"$.{key}", val

    # Check for simple wrapper
    if len(data) <= 3:
        for key in wrapper_keys:
            if key in data:
                val = data[key]
                if isinstance(val, (dict, list)):
                    return f"$.{key}", val

    return None, data


def _find_nested_pagination_dict(data: Any, depth: int = 0) -> Optional[Dict]:
    if depth > 3:
        return None
    if isinstance(data, dict):
        for key in ("pageInfo", "page_info", "pagination", "paging"):
            if key in data and isinstance(data[key], dict):
                return data[key]
        for value in data.values():
            found = _find_nested_pagination_dict(value, depth + 1)
            if found:
                return found
    elif isinstance(data, list):
        for item in data[:5]:
            found = _find_nested_pagination_dict(item, depth + 1)
            if found:
                return found
    return None


def _detect_pagination(data: Dict) -> PaginationInfo:
    """Detect pagination patterns in response."""
    info = PaginationInfo()

    pagination_fields = {
        "next": [
            "next_cursor", "nextCursor", "next_page_token", "nextPageToken",
            "next", "cursor", "after", "endCursor", "next_page", "nextPage",
            "next_url", "nextUrl",
        ],
        "total": [
            "total", "total_count", "totalCount", "count", "total_results",
            "totalResults", "result_count", "resultCount",
        ],
        "has_more": ["has_more", "hasMore", "has_next", "hasNext", "more", "hasNextPage"],
        "page": ["page", "current_page", "currentPage", "page_number", "pageNumber"],
        "limit": ["limit", "per_page", "perPage", "page_size", "pageSize", "first"],
    }

    def find_field(candidates: List[str], obj: Dict) -> Optional[str]:
        for key in candidates:
            if key in obj:
                return f"$.{key}"
        return None

    # Flatten nested structures for searching
    search_obj = dict(data)
    for key in ("meta", "pagination", "pageInfo", "page_info", "links", "link"):
        if key in data and isinstance(data[key], dict):
            search_obj.update(data[key])

    # Look for nested pagination hints if present
    nested_paging = _find_nested_pagination_dict(data, depth=0)
    if nested_paging:
        search_obj.update(nested_paging)

    info.next_field = find_field(pagination_fields["next"], search_obj)
    info.total_field = find_field(pagination_fields["total"], search_obj)
    info.has_more_field = find_field(pagination_fields["has_more"], search_obj)
    info.page_field = find_field(pagination_fields["page"], search_obj)
    info.limit_field = find_field(pagination_fields["limit"], search_obj)

    if info.next_field or info.has_more_field:
        info.detected = True
        if info.next_field and "cursor" in info.next_field.lower():
            info.pagination_type = "cursor"
        elif info.page_field:
            info.pagination_type = "page"
        else:
            info.pagination_type = "offset"

    return info


def _detect_patterns(data: Any, wrapper_path: Optional[str]) -> DetectedPatterns:
    """Detect common data patterns."""
    patterns = DetectedPatterns()

    if isinstance(data, dict):
        # API response detection
        if "status" in data or "error" in data or "message" in data:
            patterns.api_response = True

        # Error detection
        error_val = data.get("error") or data.get("errors")
        if error_val and error_val not in [None, "", [], {}]:
            patterns.error_present = True

        # Single item vs collection
        if wrapper_path:
            unwrapped = data
            for part in wrapper_path.replace("$.", "").split("."):
                if isinstance(unwrapped, dict):
                    unwrapped = unwrapped.get(part)
            if isinstance(unwrapped, list):
                patterns.collection = True
                if len(unwrapped) == 0:
                    patterns.empty_result = True
            elif isinstance(unwrapped, dict):
                patterns.single_item = True
        else:
            patterns.single_item = True
    elif isinstance(data, list):
        patterns.collection = True
        if len(data) == 0:
            patterns.empty_result = True

    return patterns


def _find_all_arrays(data: Any, current_path: str = "$", depth: int = 0) -> List[Tuple[str, List]]:
    """Recursively find all arrays in JSON structure."""
    if depth > MAX_DEPTH:
        return []

    results = []

    if isinstance(data, list) and len(data) > 0:
        results.append((current_path, data))
        # Also check inside first item for nested arrays
        if isinstance(data[0], dict):
            for key, val in data[0].items():
                nested = _find_all_arrays(val, f"{current_path}[*].{key}", depth + 1)
                results.extend(nested)
    elif isinstance(data, dict):
        for key, val in data.items():
            nested = _find_all_arrays(val, f"{current_path}.{key}", depth + 1)
            results.extend(nested)

    return results


def _get_scalar_fields(data: Dict, exclude_keys: set) -> List[str]:
    """Get non-array, non-object fields from a dict."""
    scalars = []
    for key, val in data.items():
        if key in exclude_keys:
            continue
        if not isinstance(val, (dict, list)):
            scalars.append(f"$.{key}")
    return scalars[:20]


def _summarize_embedded_json(parsed: Any) -> EmbeddedJsonInfo:
    analysis = analyze_json(parsed, "embedded-json", detect_embedded_content=False)
    info = EmbeddedJsonInfo(pattern=analysis.pattern, wrapper_path=analysis.wrapper_path)
    if analysis.primary_array:
        info.primary_array_path = analysis.primary_array.path
        info.primary_array_length = analysis.primary_array.length
        info.primary_array_fields = analysis.primary_array.item_fields[:10]
    elif analysis.field_types:
        info.object_fields = [ft.name for ft in analysis.field_types[:10]]
    return info


def _analyze_embedded_text(
    text: str,
    full_text: str,
    path: str,
) -> Optional[EmbeddedContent]:
    if len(text) < MIN_EMBEDDED_CHARS:
        return None

    line_count = full_text.count("\n") + (1 if full_text else 0)
    byte_size = len(full_text.encode("utf-8")) if full_text else 0

    jsonl_info = _analyze_json_lines(text)
    if jsonl_info:
        return EmbeddedContent(
            path=path,
            format="json_lines",
            confidence=0.9,
            line_count=line_count,
            byte_size=byte_size,
        )

    parsed_json = _try_parse_json_string(text)
    if parsed_json is not None:
        return EmbeddedContent(
            path=path,
            format="json",
            confidence=0.95,
            json_info=_summarize_embedded_json(parsed_json),
            line_count=line_count,
            byte_size=byte_size,
        )

    is_csv, csv_info = _detect_csv(text)
    if is_csv and csv_info.columns and len(csv_info.columns) >= 2:
        return EmbeddedContent(
            path=path,
            format="csv",
            confidence=0.9,
            csv_info=csv_info,
            line_count=csv_info.row_count_estimate + (1 if csv_info.has_header else 0),
            byte_size=byte_size,
        )

    is_xml, xml_info = _detect_xml(text)
    if is_xml:
        return EmbeddedContent(
            path=path,
            format="xml",
            confidence=0.85,
            xml_info=xml_info,
            line_count=line_count,
            byte_size=byte_size,
        )

    is_html, html_structure = _detect_html(text)
    if is_html:
        return EmbeddedContent(
            path=path,
            format="html",
            confidence=0.85,
            doc_structure=html_structure,
            line_count=line_count,
            byte_size=byte_size,
        )

    is_md, md_structure = _detect_markdown(text)
    if is_md:
        return EmbeddedContent(
            path=path,
            format="markdown",
            confidence=0.8,
            doc_structure=md_structure,
            line_count=line_count,
            byte_size=byte_size,
        )

    return None


def _iter_string_fields(
    data: Any,
    path: str = "$",
    depth: int = 0,
) -> List[Tuple[str, str]]:
    if depth > MAX_EMBEDDED_SCAN_DEPTH:
        return []

    results: List[Tuple[str, str]] = []
    if isinstance(data, dict):
        for key, val in data.items():
            key_path = f"{path}.{key}"
            if isinstance(val, str):
                results.append((key_path, val))
            elif isinstance(val, (dict, list)):
                results.extend(_iter_string_fields(val, key_path, depth + 1))
    elif isinstance(data, list):
        list_path = f"{path}[*]"
        for item in data[:MAX_EMBEDDED_SCAN_LIST_ITEMS]:
            if isinstance(item, str):
                results.append((list_path, item))
            elif isinstance(item, (dict, list)):
                results.extend(_iter_string_fields(item, list_path, depth + 1))

    return results


def _embedded_sort_key(entry: EmbeddedContent) -> Tuple[int, float, int]:
    format_priority = {
        "json": 4,
        "json_lines": 3,
        "csv": 3,
        "xml": 2,
        "html": 1,
        "markdown": 1,
    }
    return (
        format_priority.get(entry.format, 0),
        entry.confidence,
        entry.byte_size,
    )


def _detect_embedded_contents(data: Any) -> List[EmbeddedContent]:
    """Detect structured content embedded in JSON string fields."""
    if not isinstance(data, (dict, list)):
        return []

    found: Dict[str, EmbeddedContent] = {}
    scanned = 0
    for path, val in _iter_string_fields(data):
        if scanned >= MAX_EMBEDDED_CANDIDATES:
            break
        if not val or len(val) < MIN_EMBEDDED_CHARS:
            continue
        scanned += 1

        sample = val if len(val) <= MAX_EMBEDDED_STRING_BYTES else val[:MAX_EMBEDDED_STRING_BYTES]
        embedded = _analyze_embedded_text(sample, val, path)
        if not embedded:
            continue
        existing = found.get(path)
        if not existing or embedded.confidence > existing.confidence:
            found[path] = embedded

    results = list(found.values())
    results.sort(key=_embedded_sort_key, reverse=True)
    return results[:5]


def analyze_json(data: Any, result_id: str, *, detect_embedded_content: bool = True) -> JsonAnalysis:
    """Perform complete JSON analysis."""
    analysis = JsonAnalysis(pattern="unknown")

    if isinstance(data, list):
        # Direct array at root
        analysis.pattern = "array"
        analysis.primary_array = _analyze_array(data, "$")
        analysis.detected_patterns = _detect_patterns(data, None)
        if detect_embedded_content:
            analysis.embedded_contents = _detect_embedded_contents(data)
            analysis.embedded_content = analysis.embedded_contents[0] if analysis.embedded_contents else None

    elif isinstance(data, dict):
        # Check for wrapper
        wrapper_path, unwrapped = _detect_wrapper_path(data)
        analysis.wrapper_path = wrapper_path

        # Find all arrays
        all_arrays = _find_all_arrays(data)

        if all_arrays:
            def array_score(entry: Tuple[str, List]) -> Tuple[int, float, int, int]:
                path, arr = entry
                depth = path.count(".")
                length = len(arr)
                sample_items = arr[:5] if isinstance(arr, list) else []
                object_count = sum(1 for item in sample_items if isinstance(item, dict))
                object_ratio = object_count / max(1, len(sample_items))
                path_bonus = 0
                tokens = [t for t in re.split(r"[.\[]", path.replace("$", "")) if t]
                for key in _PREFERRED_ARRAY_KEYS:
                    if key in tokens:
                        path_bonus += 1
                return (path_bonus, object_ratio, length, -depth)

            all_arrays.sort(key=array_score, reverse=True)

            # Primary array is the most prominent one
            primary_path, primary_arr = all_arrays[0]
            analysis.primary_array = _analyze_array(primary_arr, primary_path)

            # Secondary arrays (different paths)
            for path, arr in all_arrays[1:5]:
                if path != primary_path and not path.startswith(primary_path + "["):
                    analysis.secondary_arrays.append(_analyze_array(arr, path))

            if len(primary_arr) > 1:
                analysis.pattern = "paginated_list" if _detect_pagination(data).detected else "collection"
            else:
                analysis.pattern = "single_item" if not wrapper_path else "collection"
        else:
            analysis.pattern = "single_object"
            # Get field types for single object
            if wrapper_path and isinstance(unwrapped, dict):
                fields, type_infos, sample, _ = _analyze_array_item_fields([unwrapped])
                analysis.field_types = type_infos

        # Pagination detection
        analysis.pagination = _detect_pagination(data)

        # Scalar fields at root
        exclude = set()
        if wrapper_path:
            exclude.add(wrapper_path.replace("$.", "").split(".")[0])
        analysis.scalar_fields = _get_scalar_fields(data, exclude)

        # Pattern detection
        analysis.detected_patterns = _detect_patterns(data, wrapper_path)

        # Check for embedded structured content (CSV/JSON/etc in string fields)
        if detect_embedded_content:
            analysis.embedded_contents = _detect_embedded_contents(data)
            analysis.embedded_content = analysis.embedded_contents[0] if analysis.embedded_contents else None

    return analysis


# ---------------------------------------------------------------------------
# Text Analysis
# ---------------------------------------------------------------------------

def _infer_column_type(values: List[str]) -> str:
    """Infer column type from sample values."""
    if not values:
        return "text"

    int_count = 0
    float_count = 0
    empty_count = 0

    for val in values:
        val = val.strip().strip('"\'')
        if not val:
            empty_count += 1
            continue
        # Try int
        if re.match(r'^-?\d+$', val):
            int_count += 1
        # Try float
        elif re.match(r'^-?\d+\.\d+$', val):
            float_count += 1

    non_empty = len(values) - empty_count
    if non_empty == 0:
        return "text"

    if int_count == non_empty:
        return "int"
    if float_count == non_empty or (int_count + float_count) == non_empty:
        return "float"
    return "text"


def _detect_csv(text: str) -> Tuple[bool, CsvInfo]:
    """Detect if text is CSV format and extract rich metadata.

    Extracts columns, sample rows (first 2-3 data rows), and inferred
    column types so agents can parse CSV without seeing full data.
    """
    info = CsvInfo()
    lines = text.split('\n', 50)  # Check first 50 lines
    if len(lines) < 2:
        return False, info

    sample_lines = [line for line in lines[:20] if line.strip()]
    if len(sample_lines) < 2:
        return False, info
    sample_text = "\n".join(sample_lines)

    # Try csv.Sniffer first for messy real-world files.
    sniffer = csv.Sniffer()
    dialect = None
    try:
        dialect = sniffer.sniff(sample_text, delimiters=[",", "\t", ";", "|"])
    except csv.Error:
        dialect = None

    # Fallback to simple delimiter consistency if sniffer fails.
    delimiters = [',', '\t', ';', '|']
    best_delimiter = ','
    best_consistency = 0

    if dialect:
        best_delimiter = dialect.delimiter
        best_consistency = 1
    else:
        for delim in delimiters:
            counts = [line.count(delim) for line in sample_lines[:10] if line.strip()]
            if not counts:
                continue
            # Check if counts are consistent
            if max(counts) > 0 and max(counts) == min(counts):
                if counts[0] > best_consistency:
                    best_consistency = counts[0]
                    best_delimiter = delim

    if best_consistency < 1:
        return False, info

    info.delimiter = best_delimiter

    reader = csv.reader(sample_lines, delimiter=best_delimiter)
    rows = list(reader)
    if not rows:
        return False, info

    header_row = rows[0]
    if header_row:
        header_row[0] = header_row[0].lstrip("\ufeff")
    columns = [c.strip().strip('"\'') for c in header_row]

    # Check if first row looks like a header (non-numeric, reasonable names).
    looks_like_header = sniffer.has_header(sample_text) if dialect else all(
        not re.match(r'^-?\d+\.?\d*$', col) and len(col) < 50
        for col in columns if col
    )

    info.has_header = looks_like_header
    data_start_idx = 1 if looks_like_header else 0

    if looks_like_header:
        info.columns = columns[:20]
    else:
        info.columns = [f"col{i}" for i in range(len(columns))][:20]

    # Extract first 2-3 data rows (after header if present)
    data_rows = [row for row in rows[data_start_idx:data_start_idx + 3] if any(cell.strip() for cell in row)]
    info.sample_rows = [best_delimiter.join(row)[:300] for row in data_rows]

    # Infer column types from sample data
    if data_rows and info.columns:
        # Parse sample rows into column values
        col_values: List[List[str]] = [[] for _ in info.columns]
        for row_values in data_rows:
            for i, val in enumerate(row_values[:len(info.columns)]):
                col_values[i].append(val)

        # Infer type for each column
        info.column_types = [_infer_column_type(vals) for vals in col_values]

    # Estimate row count
    total_lines = text.count('\n') + 1
    info.row_count_estimate = total_lines - (1 if looks_like_header else 0)

    return True, info


def _detect_markdown(text: str) -> Tuple[bool, DocStructure]:
    """Detect if text is markdown format."""
    structure = DocStructure()

    # Check for markdown indicators
    has_headers = bool(re.search(r'^#{1,6}\s+\w', text, re.MULTILINE))
    has_code = '```' in text or bool(re.search(r'^    \S', text, re.MULTILINE))
    has_lists = bool(re.search(r'^[\s]*[-*+]\s+\w', text, re.MULTILINE))
    has_links = bool(re.search(r'\[.+?\]\(.+?\)', text))

    indicators = sum([has_headers, has_code, has_lists, has_links])
    if indicators < 2:
        return False, structure

    # Extract sections
    for match in re.finditer(r'^(#{1,6})\s+(.+?)$', text, re.MULTILINE):
        level = len(match.group(1))
        heading = match.group(2).strip()
        structure.sections.append({
            "heading": heading[:60],
            "level": level,
            "position": match.start(),
        })

    structure.has_code_blocks = has_code
    structure.has_lists = has_lists
    structure.has_tables = bool(re.search(r'\|.+\|.+\|', text))

    return True, structure


def _normalize_xml_tag(tag: str) -> str:
    if not tag:
        return tag
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _detect_xml(text: str) -> Tuple[bool, XmlInfo]:
    """Detect if text is XML format."""
    info = XmlInfo()
    stripped = text.lstrip()
    if not stripped or "<" not in stripped or ">" not in stripped:
        return False, info

    if re.search(r"<!doctype\s+html|<html\b", stripped, re.IGNORECASE):
        return False, info

    if len(stripped) > MAX_JSON_EXTRACT_BYTES:
        return False, info

    try:
        root = ElementTree.fromstring(stripped)
    except ElementTree.ParseError:
        return False, info

    info.root_tag = _normalize_xml_tag(root.tag)
    element_count = 0
    max_depth = 0
    stack = [(root, 1)]
    while stack:
        node, depth = stack.pop()
        element_count += 1
        if depth > max_depth:
            max_depth = depth
        for child in list(node):
            stack.append((child, depth + 1))

    info.element_count = element_count
    info.depth = max_depth
    return True, info


def _detect_html(text: str) -> Tuple[bool, DocStructure]:
    """Detect if text is HTML format."""
    structure = DocStructure()

    # Check for HTML tags
    html_pattern = r'<(html|head|body|div|span|p|h[1-6]|table|script)[^>]*>'
    matches = re.findall(html_pattern, text.lower())
    if len(matches) < 3:
        return False, structure

    # Extract headings
    for match in re.finditer(r'<h([1-6])[^>]*>(.*?)</h\1>', text, re.IGNORECASE | re.DOTALL):
        level = int(match.group(1))
        heading = re.sub(r'<[^>]+>', '', match.group(2)).strip()
        if heading:
            structure.sections.append({
                "heading": heading[:60],
                "level": level,
                "position": match.start(),
            })

    structure.has_tables = bool(re.search(r'<table[^>]*>', text, re.IGNORECASE))
    structure.has_code_blocks = bool(re.search(r'<(pre|code)[^>]*>', text, re.IGNORECASE))

    return True, structure


def _detect_log_format(text: str) -> bool:
    """Detect if text looks like log output."""
    lines = text.split('\n', 10)
    if len(lines) < 3:
        return False

    # Look for timestamp patterns at line starts
    timestamp_patterns = [
        r'^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}',  # ISO format
        r'^\[\d{4}-\d{2}-\d{2}',                # Bracketed date
        r'^\d{2}:\d{2}:\d{2}',                  # Time only
        r'^[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:',  # Syslog format
    ]

    matches = 0
    for line in lines[:10]:
        for pattern in timestamp_patterns:
            if re.match(pattern, line):
                matches += 1
                break

    return matches >= 3


def _analyze_json_lines(text: str) -> Optional[JsonLinesInfo]:
    """Analyze newline-delimited JSON."""
    if not text:
        return None
    stripped = text.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        return None
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    if len(lines) < 2:
        return None

    parsed_count = 0
    field_counts: Dict[str, int] = {}
    samples: List[str] = []
    for line in lines[:MAX_JSON_LINES_SCAN]:
        if not (line.startswith("{") or line.startswith("[")):
            continue
        parsed, _ = _parse_json_any(line)
        if parsed is None:
            continue
        parsed_count += 1
        if isinstance(parsed, dict):
            for key in parsed.keys():
                field_counts[key] = field_counts.get(key, 0) + 1
        if len(samples) < 3:
            samples.append(line[:300])

    if parsed_count < 2:
        return None

    fields = sorted(field_counts.keys(), key=lambda k: -field_counts[k])[:15]
    return JsonLinesInfo(
        line_count=len(lines),
        parsed_line_count=parsed_count,
        fields=fields,
        sample_objects=samples,
    )


def _analyze_sse(text: str) -> Optional[SseInfo]:
    """Analyze Server-Sent Events text and extract JSON field hints."""
    if not text:
        return None
    lines = text.splitlines()
    data_lines: List[str] = []
    event_count = 0
    for line in lines[:MAX_SSE_SCAN_LINES]:
        if line.strip() == "":
            event_count += 1
            continue
        if line.startswith("data:"):
            payload = line[5:].lstrip()
            if payload and payload != "[DONE]":
                data_lines.append(payload)

    if len(data_lines) < 2:
        return None

    field_counts: Dict[str, int] = {}
    for payload in data_lines[:MAX_JSON_LINES_SCAN]:
        parsed, _ = _parse_json_any(payload)
        if isinstance(parsed, dict):
            for key in parsed.keys():
                field_counts[key] = field_counts.get(key, 0) + 1

    fields = sorted(field_counts.keys(), key=lambda k: -field_counts[k])[:15]
    return SseInfo(
        data_line_count=len(data_lines),
        event_count_estimate=max(event_count, 1),
        json_fields=fields,
    )


def _extract_text_hints(text: str) -> TextHints:
    """Extract useful search hints from text."""
    hints = TextHints()

    # Line statistics
    lines = text.split('\n')
    hints.line_count = len(lines)
    if lines:
        hints.avg_line_length = sum(len(l) for l in lines) // len(lines)

    # Key positions
    keywords = ['error', 'exception', 'warning', 'fail', 'success', '@', 'http://', 'https://']
    for keyword in keywords:
        pos = text.lower().find(keyword.lower())
        if pos >= 0:
            key = keyword.rstrip(':/').lstrip('@')
            hints.key_positions[key] = pos

    return hints


def analyze_text(text: str) -> TextAnalysis:
    """Perform complete text analysis."""
    analysis = TextAnalysis(format="plain")

    # Check JSON lines first (before CSV, since JSON has commas)
    json_lines_info = _analyze_json_lines(text)
    if json_lines_info:
        analysis.format = "json_lines"
        analysis.confidence = 0.9
        analysis.json_lines_info = json_lines_info
        analysis.text_hints = _extract_text_hints(text)
        return analysis

    sse_info = _analyze_sse(text)
    if sse_info:
        analysis.format = "sse"
        analysis.confidence = 0.8
        analysis.sse_info = sse_info
        analysis.text_hints = _extract_text_hints(text)
        return analysis

    # Try CSV detection
    is_csv, csv_info = _detect_csv(text)
    if is_csv:
        analysis.format = "csv"
        analysis.confidence = 0.9
        analysis.csv_info = csv_info
        analysis.text_hints = _extract_text_hints(text)
        return analysis

    is_xml, xml_info = _detect_xml(text)
    if is_xml:
        analysis.format = "xml"
        analysis.confidence = 0.85
        analysis.xml_info = xml_info
        analysis.text_hints = _extract_text_hints(text)
        return analysis

    is_html, html_structure = _detect_html(text)
    if is_html:
        analysis.format = "html"
        analysis.confidence = 0.85
        analysis.doc_structure = html_structure
        analysis.text_hints = _extract_text_hints(text)
        return analysis

    is_md, md_structure = _detect_markdown(text)
    if is_md:
        analysis.format = "markdown"
        analysis.confidence = 0.8
        analysis.doc_structure = md_structure
        analysis.text_hints = _extract_text_hints(text)
        return analysis

    if _detect_log_format(text):
        analysis.format = "log"
        analysis.confidence = 0.7
        analysis.text_hints = _extract_text_hints(text)
        return analysis

    # Default to plain text
    analysis.format = "plain"
    analysis.confidence = 0.5
    analysis.text_hints = _extract_text_hints(text)
    return analysis


# ---------------------------------------------------------------------------
# Size Strategy
# ---------------------------------------------------------------------------

def _determine_size_strategy(byte_count: int) -> SizeStrategy:
    """Determine query strategy based on data size."""
    if byte_count <= SIZE_SMALL:
        return SizeStrategy(
            category="small",
            bytes=byte_count,
            recommendation="direct_query",
            warning=None,
        )
    elif byte_count <= SIZE_MEDIUM:
        return SizeStrategy(
            category="medium",
            bytes=byte_count,
            recommendation="targeted_extract",
            warning=None,
        )
    elif byte_count <= SIZE_LARGE:
        return SizeStrategy(
            category="large",
            bytes=byte_count,
            recommendation="aggregate_first",
            warning="Large result - aggregate (COUNT, GROUP BY) before extracting details",
        )
    else:
        return SizeStrategy(
            category="huge",
            bytes=byte_count,
            recommendation="chunked",
            warning="Very large result - use position-based chunked extraction",
        )


# ---------------------------------------------------------------------------
# Query Pattern Generation
# ---------------------------------------------------------------------------

def _safe_sql_identifier(name: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", name).strip("_")
    return cleaned or fallback


def _generate_query_patterns(
    result_id: str,
    json_analysis: Optional[JsonAnalysis],
    text_analysis: Optional[TextAnalysis],
    is_json: bool,
) -> QueryPatterns:
    """Generate ready-to-use SQL query patterns."""
    patterns = QueryPatterns()

    if is_json and json_analysis:
        if json_analysis.primary_array:
            arr = json_analysis.primary_array
            path = arr.path

            # Determine json_each path
            if path == "$":
                each_expr = "json_each(result_json)"
            else:
                each_expr = f"json_each(result_json,'{path}')"

            if arr.table_info:
                table = arr.table_info
                col_names = table.columns[:2] if table.columns else []  # Limit to 2 for clarity
                extracts = ", ".join(
                    f"json_extract(r.value,'$[{idx}]') AS "
                    f"{_safe_sql_identifier(name, f'col{idx + 1}')}"
                    for idx, name in enumerate(col_names)
                )
                row_filter = ""
                if table.has_header:
                    row_filter = " AND CAST(r.key AS INTEGER) > 0"
                if extracts:
                    patterns.list_all = (
                        f"SELECT {extracts} "
                        f"FROM __tool_results, {each_expr} AS r "
                        f"WHERE result_id='{result_id}'{row_filter} LIMIT 25"
                    )
                patterns.count = (
                    f"SELECT COUNT(*) "
                    f"FROM __tool_results, {each_expr} AS r "
                    f"WHERE result_id='{result_id}'{row_filter}"
                )
                patterns.sample = (
                    f"SELECT r.value "
                    f"FROM __tool_results, {each_expr} AS r "
                    f"WHERE result_id='{result_id}'{row_filter} LIMIT 1"
                )
                if col_names:
                    patterns.filter_template = (
                        f"SELECT ... FROM __tool_results, {each_expr} AS r "
                        f"WHERE result_id='{result_id}'{row_filter} "
                        f"AND json_extract(r.value,'$[0]')='value'"
                    )
                return patterns

            # If items have a data wrapper (e.g., {"kind": ..., "data": {...fields...}}),
            # prefix field paths with the wrapper key
            field_prefix = f".{arr.item_data_key}" if arr.item_data_key else ""

            # Build field extracts - limit to 2 fields for clarity in hint
            fields = arr.item_fields[:2]
            if fields:
                extracts = ", ".join(f"json_extract(r.value,'${field_prefix}.{f}')" for f in fields)
                patterns.list_all = (
                    f"SELECT {extracts} "
                    f"FROM __tool_results, {each_expr} AS r "
                    f"WHERE result_id='{result_id}' LIMIT 25"
                )

                patterns.count = (
                    f"SELECT COUNT(*) "
                    f"FROM __tool_results, {each_expr} AS r "
                    f"WHERE result_id='{result_id}'"
                )

                if len(fields) >= 1:
                    patterns.filter_template = (
                        f"SELECT ... FROM __tool_results, {each_expr} AS r "
                        f"WHERE result_id='{result_id}' "
                        f"AND json_extract(r.value,'${field_prefix}.{fields[0]}')='value'"
                    )

            # Sample query
            patterns.sample = (
                f"SELECT r.value "
                f"FROM __tool_results, {each_expr} AS r "
                f"WHERE result_id='{result_id}' LIMIT 1"
            )

        elif json_analysis.pattern == "single_object":
            # Direct extraction for single objects
            if json_analysis.field_types:
                prefix = "$"
                if json_analysis.wrapper_path:
                    prefix = json_analysis.wrapper_path
                fields = [ft.name for ft in json_analysis.field_types[:5]]
                extracts = ", ".join(f"json_extract(result_json,'{prefix}.{f}')" for f in fields)
                patterns.list_all = (
                    f"SELECT {extracts} "
                    f"FROM __tool_results WHERE result_id='{result_id}'"
                )

    else:
        # Text patterns
        if text_analysis and text_analysis.format == "csv" and text_analysis.csv_info:
            csv = text_analysis.csv_info
            patterns.sample = (
                f"SELECT substr(result_text, 1, 500) "
                f"FROM __tool_results WHERE result_id='{result_id}'"
            )
            patterns.count = (
                f"SELECT (length(result_text) - length(replace(result_text, char(10), ''))) AS line_count "
                f"FROM __tool_results WHERE result_id='{result_id}'"
            )
        else:
            # Generic text patterns
            patterns.sample = (
                f"SELECT substr(result_text, 1, 500) "
                f"FROM __tool_results WHERE result_id='{result_id}'"
            )
            patterns.filter_template = (
                f"SELECT substr(result_text, instr(lower(result_text),'keyword')-50, 200) "
                f"FROM __tool_results WHERE result_id='{result_id}' "
                f"AND instr(lower(result_text),'keyword') > 0"
            )

    return patterns


# ---------------------------------------------------------------------------
# Compact Summary Generation
# ---------------------------------------------------------------------------

def _generate_compact_summary(
    result_id: str,
    is_json: bool,
    size_strategy: SizeStrategy,
    json_analysis: Optional[JsonAnalysis],
    text_analysis: Optional[TextAnalysis],
    query_patterns: Optional["QueryPatterns"] = None,
    parse_info: Optional[ParseInfo] = None,
    decode_info: Optional[DecodeInfo] = None,
) -> str:
    """Generate a compact, actionable summary for the prompt.

    Format priorities:
    1. QUERY first - the exact SQL to copy/use
    2. PATH explicitly labeled - the json_each path
    3. Brief structure info
    """
    parts = []

    def _split_wildcard_path(path: str) -> Tuple[Optional[str], Optional[str]]:
        if "[*]" not in path:
            return None, None
        prefix, suffix = path.split("[*]", 1)
        parent_path = prefix.rstrip(".") or "$"
        if suffix.startswith("."):
            item_path = f"${suffix}"
        elif suffix:
            item_path = f"${suffix}"
        else:
            item_path = "$"
        return parent_path, item_path

    if is_json and json_analysis:
        if json_analysis.primary_array:
            arr = json_analysis.primary_array
            path = arr.path

            # PATH FIRST - the exact path is critical, most common mistake is wrong path
            if arr.item_data_key:
                parts.append(f"→ PATH: {path} ({arr.length} items, fields in $.{arr.item_data_key})")
            else:
                parts.append(f"→ PATH: {path} ({arr.length} items)")

            # QUERY - ready-to-use example (limited fields for clarity)
            if query_patterns and query_patterns.list_all:
                parts.append(f"→ QUERY: {query_patterns.list_all}")
            else:
                # Generate inline if no pattern - limit to 2 fields for readability
                if path == "$":
                    each_expr = "json_each(result_json)"
                else:
                    each_expr = f"json_each(result_json,'{path}')"
                if arr.table_info and arr.table_info.columns:
                    extracts = ", ".join(
                        f"json_extract(r.value,'$[{idx}]')" for idx, _ in enumerate(arr.table_info.columns[:2])
                    )
                    row_filter = ""
                    if arr.table_info.has_header:
                        row_filter = " AND CAST(r.key AS INTEGER) > 0"
                    parts.append(
                        f"→ QUERY: SELECT {extracts} "
                        f"FROM __tool_results, {each_expr} AS r "
                        f"WHERE result_id='{result_id}'{row_filter} LIMIT 25"
                    )
                else:
                    field_prefix = f".{arr.item_data_key}" if arr.item_data_key else ""
                    fields_to_show = arr.item_fields[:2]  # Limit to 2 fields for clarity
                    if fields_to_show:
                        extracts = ", ".join(f"json_extract(r.value,'${field_prefix}.{f}')" for f in fields_to_show)
                        parts.append(
                            f"→ QUERY: SELECT {extracts} "
                            f"FROM __tool_results, {each_expr} AS r "
                            f"WHERE result_id='{result_id}' LIMIT 25"
                        )
                    else:
                        # Primitive array (numbers, strings) - just show value directly
                        parts.append(
                            f"→ QUERY: SELECT r.value "
                            f"FROM __tool_results, {each_expr} AS r "
                            f"WHERE result_id='{result_id}' LIMIT 25"
                        )

            if arr.table_info:
                table = arr.table_info
                col_count = len(table.columns)
                parts.append(f"  TABLE: ~{table.row_count} rows, {col_count} columns")
                if table.column_types and len(table.column_types) == len(table.columns):
                    col_with_types = [
                        f"{c}:{t}" for c, t in zip(table.columns[:10], table.column_types[:10])
                    ]
                    parts.append(f"  COLUMNS: {', '.join(col_with_types)}")
                elif table.columns:
                    parts.append(f"  COLUMNS: {', '.join(table.columns[:10])}")
                if table.sample_rows:
                    parts.append(f"  SAMPLE: {table.sample_rows[0]}")
            else:
                # Fields - brief
                if arr.item_fields:
                    parts.append(f"  FIELDS: {', '.join(arr.item_fields[:10])}")

                # Nested arrays if present
                if arr.nested_arrays:
                    parts.append(f"  NESTED: {', '.join(arr.nested_arrays[:3])}")

        elif json_analysis.pattern == "single_object":
            # Single object - simpler query
            if query_patterns and query_patterns.list_all:
                parts.append(f"→ QUERY: {query_patterns.list_all}")
            elif json_analysis.field_types:
                fields = [ft.name for ft in json_analysis.field_types[:3]]
                prefix = json_analysis.wrapper_path or "$"
                extracts = ", ".join(f"json_extract(result_json,'{prefix}.{f}')" for f in fields)
                parts.append(
                    f"→ QUERY: SELECT {extracts} "
                    f"FROM __tool_results WHERE result_id='{result_id}'"
                )

            parts.append("  TYPE: single object")
            if json_analysis.field_types:
                field_strs = [ft.name for ft in json_analysis.field_types[:10]]
                parts.append(f"  FIELDS: {', '.join(field_strs)}")

        # Error warning - important
        if json_analysis.detected_patterns and json_analysis.detected_patterns.error_present:
            parts.append("  ⚠ ERROR field present in response")

        # Empty result - important
        if json_analysis.detected_patterns and json_analysis.detected_patterns.empty_result:
            parts.append("  ⚠ Result array is empty")

        # Embedded structured content in JSON strings
        if json_analysis.embedded_contents:
            for emb in json_analysis.embedded_contents[:2]:
                parent_path, item_path = _split_wildcard_path(emb.path)
                if parent_path:
                    extract_query = (
                        f"SELECT json_extract(r.value,'{item_path}') "
                        f"FROM __tool_results, json_each(result_json,'{parent_path}') AS r "
                        f"WHERE result_id='{result_id}'"
                    )
                else:
                    extract_query = (
                        f"SELECT json_extract(result_json,'{emb.path}') "
                        f"FROM __tool_results WHERE result_id='{result_id}'"
                    )

                if emb.format == "csv" and emb.csv_info and emb.csv_info.columns:
                    csv = emb.csv_info
                    parts.append(f"\n  📄 CSV DATA in {emb.path} ({csv.row_count_estimate} rows) - THIS IS TEXT, NOT JSON")

                    col_count = len(csv.columns)
                    if csv.column_types and len(csv.column_types) == len(csv.columns):
                        col_with_types = [f"{c}:{t}" for c, t in zip(csv.columns[:12], csv.column_types[:12])]
                        parts.append(f"  SCHEMA ({col_count} columns): {', '.join(col_with_types)}")
                    else:
                        parts.append(f"  COLUMNS ({col_count}): {', '.join(csv.columns[:12])}")

                    if csv.sample_rows:
                        sample = csv.sample_rows[0][:150]
                        parts.append(f"  SAMPLE: {sample}")

                    parts.append(f"  → GET CSV: {extract_query}")
                    parts.append(f"  → CSV is TEXT - parse it line by line, do NOT use json_each()")
                    if col_count > 1:
                        cte_parts = []
                        for i in range(1, col_count):
                            if i < col_count - 1:
                                cte_parts.append(f"p{i}(c{i},r{i if i > 1 else ''})")
                            else:
                                cte_parts.append(f"p{i}(c{i},c{i+1})")
                        cte_chain = "→".join(cte_parts)
                        cols = ",".join(f"c{i}" for i in range(1, col_count + 1))
                        parts.append(f"  → PARSE: {cte_chain} then INSERT...SELECT {cols} FROM p{col_count-1}")

                elif emb.format == "json":
                    parts.append(f"\n  🧩 JSON DATA in {emb.path} - JSON stored as TEXT")
                    parts.append(f"  → GET JSON: {extract_query}")

                    if emb.json_info and not parent_path:
                        base_expr = f"json_extract(result_json,'{emb.path}')"
                        primary_path = emb.json_info.primary_array_path or "$"
                        if primary_path == "$":
                            each_expr = f"json_each({base_expr})"
                        else:
                            each_expr = f"json_each({base_expr},'{primary_path}')"
                        fields = emb.json_info.primary_array_fields[:3]
                        if fields:
                            extracts = ", ".join(f"json_extract(r.value,'$.{f}')" for f in fields)
                            parts.append(
                                f"  → QUERY: SELECT {extracts} "
                                f"FROM __tool_results, {each_expr} AS r "
                                f"WHERE result_id='{result_id}' LIMIT 25"
                            )
                        elif emb.json_info.object_fields:
                            fields = emb.json_info.object_fields[:3]
                            extracts = ", ".join(f"json_extract({base_expr},'$.{f}')" for f in fields)
                            parts.append(
                                f"  → QUERY: SELECT {extracts} "
                                f"FROM __tool_results WHERE result_id='{result_id}'"
                            )

                elif emb.format == "json_lines":
                    parts.append(f"\n  🧩 JSON LINES in {emb.path} (~{emb.line_count} lines)")
                    parts.append(f"  → GET JSONL: {extract_query}")

                elif emb.format == "xml":
                    root_tag = emb.xml_info.root_tag if emb.xml_info else None
                    tag_note = f" root={root_tag}" if root_tag else ""
                    parts.append(f"\n  📄 XML DATA in {emb.path}{tag_note}")
                    parts.append(f"  → GET XML: {extract_query}")

                elif emb.format in ("html", "markdown"):
                    # Suggest grep_context for large content (>10KB), substr for small
                    parts.append(f"\n  📄 {emb.format.upper()} in {emb.path} (~{emb.line_count} lines)")
                    extract_expr = f"json_extract(result_json,'{emb.path}')"
                    if emb.byte_size > 10000:
                        # Large content - suggest grep
                        parts.append(
                            f"  → SEARCH: SELECT grep_context_all({extract_expr}, 'keyword', 50, 5) "
                            f"FROM __tool_results WHERE result_id='{result_id}'"
                        )
                    else:
                        parts.append(
                            f"  → QUERY: SELECT substr({extract_expr},1,2000) "
                            f"FROM __tool_results WHERE result_id='{result_id}'"
                        )

    else:
        # Text data
        if text_analysis:
            fmt = text_analysis.format

            if fmt == "json_lines" and text_analysis.json_lines_info:
                info = text_analysis.json_lines_info
                parts.append(
                    f"→ QUERY: SELECT substr(result_text, 1, 2000) "
                    f"FROM __tool_results WHERE result_id='{result_id}'"
                )
                parts.append(f"  TYPE: JSON LINES (~{info.line_count} lines)")
                if info.fields:
                    parts.append(f"  FIELDS: {', '.join(info.fields[:12])}")
                parts.append("  → Extract line by line and parse as JSON")

            elif fmt == "sse" and text_analysis.sse_info:
                info = text_analysis.sse_info
                parts.append(
                    f"→ QUERY: SELECT substr(result_text, 1, 2000) "
                    f"FROM __tool_results WHERE result_id='{result_id}'"
                )
                parts.append(f"  TYPE: SSE (~{info.event_count_estimate} events)")
                if info.json_fields:
                    parts.append(f"  FIELDS: {', '.join(info.json_fields[:12])}")
                parts.append("  → Extract data: lines and parse JSON payloads")

            elif fmt == "csv" and text_analysis.csv_info:
                csv = text_analysis.csv_info
                col_count = len(csv.columns)
                parts.append(f"  TYPE: CSV (~{csv.row_count_estimate} rows, {col_count} columns)")

                # Show columns with inferred types
                if csv.column_types and len(csv.column_types) == len(csv.columns):
                    col_with_types = [f"{c}:{t}" for c, t in zip(csv.columns[:12], csv.column_types[:12])]
                    parts.append(f"  SCHEMA: {', '.join(col_with_types)}")
                elif csv.columns:
                    parts.append(f"  COLUMNS: {', '.join(csv.columns[:12])}")

                # Show sample data row
                if csv.sample_rows:
                    sample = csv.sample_rows[0][:150]
                    parts.append(f"  SAMPLE: {sample}")

                parts.append(
                    f"→ QUERY: SELECT result_text FROM __tool_results WHERE result_id='{result_id}'"
                )
                parts.append(f"  → Parse CSV line by line. Do NOT use json_each()")
                # Add pattern hint based on column count with concrete structure
                if col_count > 1:
                    # Build concrete CTE chain: p1(c1,r)→p2(c2,r2)→...→pN(cN,cN+1)
                    cte_parts = []
                    for i in range(1, col_count):
                        if i < col_count - 1:
                            cte_parts.append(f"p{i}(c{i},r{i if i > 1 else ''})")
                        else:
                            cte_parts.append(f"p{i}(c{i},c{i+1})")
                    cte_chain = "→".join(cte_parts)
                    cols = ",".join(f"c{i}" for i in range(1, col_count + 1))
                    parts.append(f"  → PARSE: {cte_chain} then INSERT...SELECT {cols} FROM p{col_count-1}")

            elif fmt in ("markdown", "html") and text_analysis.doc_structure:
                doc = text_analysis.doc_structure
                section_count = len(doc.sections) if doc.sections else 0
                if size_strategy.bytes > 10000:
                    # Large content (>10KB) - suggest grep
                    parts.append(
                        f"→ SEARCH: SELECT grep_context_all(result_text, 'keyword', 50, 5) "
                        f"FROM __tool_results WHERE result_id='{result_id}'"
                    )
                else:
                    pos = doc.sections[0]["position"] if doc.sections else 1
                    parts.append(
                        f"→ QUERY: SELECT substr(result_text, {pos}, 2000) "
                        f"FROM __tool_results WHERE result_id='{result_id}'"
                    )
                parts.append(f"  TYPE: {fmt.upper()} ({section_count} sections)")

            elif fmt == "xml" and text_analysis.xml_info:
                xml_info = text_analysis.xml_info
                root_note = f" root={xml_info.root_tag}" if xml_info and xml_info.root_tag else ""
                parts.append(
                    f"→ QUERY: SELECT substr(result_text, 1, 2000) "
                    f"FROM __tool_results WHERE result_id='{result_id}'"
                )
                parts.append(f"  TYPE: XML ({xml_info.element_count} elements{root_note})")

            elif fmt == "log":
                parts.append(
                    f"→ QUERY: SELECT substr(result_text, instr(lower(result_text),'error')-50, 300) "
                    f"FROM __tool_results WHERE result_id='{result_id}' "
                    f"AND instr(lower(result_text),'error') > 0"
                )
                hints = text_analysis.text_hints
                parts.append(f"  TYPE: Log (~{hints.line_count if hints else '?'} lines)")

            else:
                parts.append(
                    f"→ QUERY: SELECT substr(result_text, 1, 500) "
                    f"FROM __tool_results WHERE result_id='{result_id}'"
                )
                hints = text_analysis.text_hints
                parts.append(f"  TYPE: Text (~{hints.line_count if hints else '?'} lines)")

    # Size warning at end
    if parse_info and (parse_info.source != "raw" or parse_info.mode != "json"):
        parts.append(f"  PARSE: {parse_info.source} ({parse_info.mode})")
    if decode_info and decode_info.steps:
        steps = "+".join(decode_info.steps)
        encoding = f", encoding={decode_info.encoding}" if decode_info.encoding else ""
        parts.append(
            f"  DECODED: {steps} ({decode_info.bytes_before}->{decode_info.bytes_after} bytes{encoding})"
        )
    if size_strategy.warning:
        parts.append(f"  SIZE: {size_strategy.warning}")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Main Entry Point
# ---------------------------------------------------------------------------

def analyze_result(result_text: str, result_id: str) -> ResultAnalysis:
    """
    Analyze a tool result and return structured metadata.

    This is the main entry point for result analysis. It detects whether
    the content is JSON or text, analyzes the structure, and generates
    actionable query patterns and hints.
    """
    prepared = prepare_result_text(result_text)
    analysis_text = prepared.analysis_text
    byte_count = len(analysis_text.encode("utf-8"))
    size_strategy = _determine_size_strategy(byte_count)

    # Try parsing as JSON
    is_json = prepared.is_json
    parsed = prepared.parsed_json
    json_analysis = None
    text_analysis = None

    if is_json and parsed is None:
        parsed, _ = _parse_json_any(analysis_text)
        is_json = parsed is not None

    if is_json and parsed is not None:
        if isinstance(parsed, str):
            json_analysis = JsonAnalysis(pattern="string")
            embedded = _analyze_embedded_text(parsed, parsed, "$")
            if embedded:
                json_analysis.embedded_contents = [embedded]
                json_analysis.embedded_content = embedded
        elif isinstance(parsed, (dict, list)):
            json_analysis = analyze_json(parsed, result_id)
        else:
            json_analysis = JsonAnalysis(pattern="scalar")
    else:
        text_analysis = analyze_text(analysis_text)

    # Generate query patterns
    query_patterns = _generate_query_patterns(
        result_id, json_analysis, text_analysis, is_json
    )

    # Generate compact summary (pass query_patterns for complete example queries)
    compact_summary = _generate_compact_summary(
        result_id,
        is_json,
        size_strategy,
        json_analysis,
        text_analysis,
        query_patterns,
        parse_info=prepared.parse_info,
        decode_info=prepared.decode_info,
    )

    return ResultAnalysis(
        is_json=is_json,
        size_strategy=size_strategy,
        json_analysis=json_analysis,
        text_analysis=text_analysis,
        query_patterns=query_patterns,
        compact_summary=compact_summary,
        decode_info=prepared.decode_info,
        parse_info=prepared.parse_info,
        prepared_text=analysis_text,
        normalized_json=prepared.normalized_json,
    )


def analysis_to_dict(analysis: ResultAnalysis) -> Dict[str, Any]:
    """Convert analysis to a JSON-serializable dict for storage."""
    result: Dict[str, Any] = {
        "is_json": analysis.is_json,
        "size": {
            "category": analysis.size_strategy.category,
            "bytes": analysis.size_strategy.bytes,
            "recommendation": analysis.size_strategy.recommendation,
        },
    }
    if analysis.parse_info:
        result["parse"] = {
            "mode": analysis.parse_info.mode,
            "source": analysis.parse_info.source,
        }
    if analysis.decode_info and analysis.decode_info.steps:
        result["decode"] = {
            "steps": analysis.decode_info.steps,
            "bytes_before": analysis.decode_info.bytes_before,
            "bytes_after": analysis.decode_info.bytes_after,
            "encoding": analysis.decode_info.encoding,
        }

    if analysis.json_analysis:
        ja = analysis.json_analysis
        result["json"] = {
            "pattern": ja.pattern,
            "wrapper_path": ja.wrapper_path,
        }
        if ja.primary_array:
            result["json"]["primary_array"] = {
                "path": ja.primary_array.path,
                "length": ja.primary_array.length,
                "fields": ja.primary_array.item_fields[:15],
            }
            if ja.primary_array.table_info:
                table = ja.primary_array.table_info
                result["json"]["primary_array"]["table"] = {
                    "has_header": table.has_header,
                    "columns": table.columns[:15],
                    "row_count": table.row_count,
                    "column_types": table.column_types[:15],
                }
        if ja.pagination and ja.pagination.detected:
            result["json"]["pagination"] = {
                "type": ja.pagination.pagination_type,
                "next": ja.pagination.next_field,
                "total": ja.pagination.total_field,
            }

        def _serialize_embedded(ec: EmbeddedContent) -> Dict[str, Any]:
            payload: Dict[str, Any] = {
                "path": ec.path,
                "format": ec.format,
                "line_count": ec.line_count,
                "byte_size": ec.byte_size,
            }
            if ec.csv_info:
                payload["csv"] = {
                    "columns": ec.csv_info.columns[:15],
                    "rows": ec.csv_info.row_count_estimate,
                    "delimiter": ec.csv_info.delimiter,
                    "column_types": ec.csv_info.column_types[:15],
                    "sample_rows": ec.csv_info.sample_rows[:2],
                }
            if ec.json_info:
                payload["json"] = {
                    "pattern": ec.json_info.pattern,
                    "wrapper_path": ec.json_info.wrapper_path,
                    "primary_array_path": ec.json_info.primary_array_path,
                    "primary_array_length": ec.json_info.primary_array_length,
                    "primary_array_fields": ec.json_info.primary_array_fields[:15],
                    "object_fields": ec.json_info.object_fields[:15],
                }
            if ec.xml_info:
                payload["xml"] = {
                    "root_tag": ec.xml_info.root_tag,
                    "element_count": ec.xml_info.element_count,
                    "depth": ec.xml_info.depth,
                }
            return payload

        if ja.embedded_contents:
            serialized = [_serialize_embedded(ec) for ec in ja.embedded_contents[:3]]
            result["json"]["embedded_contents"] = serialized
            result["json"]["embedded_content"] = serialized[0]
        elif ja.embedded_content:
            result["json"]["embedded_content"] = _serialize_embedded(ja.embedded_content)

    if analysis.text_analysis:
        ta = analysis.text_analysis
        result["text"] = {
            "format": ta.format,
            "confidence": ta.confidence,
        }
        if ta.csv_info:
            result["text"]["csv"] = {
                "columns": ta.csv_info.columns[:15],
                "rows": ta.csv_info.row_count_estimate,
                "column_types": ta.csv_info.column_types[:15],
                "sample_rows": ta.csv_info.sample_rows[:2],
            }
        if ta.doc_structure:
            result["text"]["sections"] = [
                {"heading": s["heading"], "pos": s["position"]}
                for s in ta.doc_structure.sections[:10]
            ]
        if ta.xml_info:
            result["text"]["xml"] = {
                "root_tag": ta.xml_info.root_tag,
                "element_count": ta.xml_info.element_count,
                "depth": ta.xml_info.depth,
            }
        if ta.json_lines_info:
            result["text"]["json_lines"] = {
                "line_count": ta.json_lines_info.line_count,
                "parsed_lines": ta.json_lines_info.parsed_line_count,
                "fields": ta.json_lines_info.fields[:15],
                "samples": ta.json_lines_info.sample_objects[:2],
            }
        if ta.sse_info:
            result["text"]["sse"] = {
                "data_lines": ta.sse_info.data_line_count,
                "events": ta.sse_info.event_count_estimate,
                "fields": ta.sse_info.json_fields[:15],
            }

    if analysis.query_patterns:
        qp = analysis.query_patterns
        patterns = {}
        if qp.list_all:
            patterns["list_all"] = qp.list_all
        if qp.count:
            patterns["count"] = qp.count
        if qp.sample:
            patterns["sample"] = qp.sample
        if patterns:
            result["queries"] = patterns

    return result
