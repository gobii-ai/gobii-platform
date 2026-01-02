"""
Rich metadata analysis for tool results.

Analyzes JSON and text data to extract actionable query patterns,
structure information, and hints that help agents write correct SQL queries.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union


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


@dataclass
class ArrayInfo:
    """Information about an array found in JSON."""
    path: str
    length: int
    item_fields: List[str] = field(default_factory=list)
    item_sample: Optional[str] = None
    nested_arrays: List[str] = field(default_factory=list)  # paths relative to item
    item_data_key: Optional[str] = None  # e.g., "data" if items are {"kind": ..., "data": {...actual fields...}}


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
    sample_row: Optional[str] = None


@dataclass
class DocStructure:
    """Structure information for markdown/HTML documents."""
    sections: List[Dict[str, Any]] = field(default_factory=list)  # [{heading, position}]
    has_tables: bool = False
    has_code_blocks: bool = False
    has_lists: bool = False


@dataclass
class TextHints:
    """Hints for searching/extracting from text."""
    key_positions: Dict[str, int] = field(default_factory=dict)  # keyword -> first position
    line_count: int = 0
    avg_line_length: int = 0


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
    csv_info: Optional[CsvInfo] = None
    line_count: int = 0
    byte_size: int = 0


@dataclass
class QueryPatterns:
    """Ready-to-use SQL query patterns."""
    list_all: Optional[str] = None
    count: Optional[str] = None
    sample: Optional[str] = None
    filter_template: Optional[str] = None


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
    embedded_content: Optional[EmbeddedContent] = None  # CSV/structured content in string field


@dataclass
class TextAnalysis:
    """Complete analysis of text data."""
    format: str  # csv, markdown, html, log, json_lines, plain
    confidence: float = 0.0
    csv_info: Optional[CsvInfo] = None
    doc_structure: Optional[DocStructure] = None
    text_hints: Optional[TextHints] = None


@dataclass
class ResultAnalysis:
    """Complete analysis result."""
    is_json: bool
    size_strategy: SizeStrategy
    json_analysis: Optional[JsonAnalysis] = None
    text_analysis: Optional[TextAnalysis] = None
    query_patterns: Optional[QueryPatterns] = None
    compact_summary: str = ""  # One-line summary for prompt


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


def _analyze_array(arr: List, path: str) -> ArrayInfo:
    """Analyze a JSON array."""
    length = len(arr)
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


def _detect_pagination(data: Dict) -> PaginationInfo:
    """Detect pagination patterns in response."""
    info = PaginationInfo()

    pagination_fields = {
        "next": ["next_cursor", "nextCursor", "next_page_token", "nextPageToken", "next", "cursor", "after"],
        "total": ["total", "total_count", "totalCount", "count", "total_results", "totalResults"],
        "has_more": ["has_more", "hasMore", "has_next", "hasNext", "more"],
        "page": ["page", "current_page", "currentPage", "page_number", "pageNumber"],
        "limit": ["limit", "per_page", "perPage", "page_size", "pageSize"],
    }

    def find_field(candidates: List[str], obj: Dict) -> Optional[str]:
        for key in candidates:
            if key in obj:
                return f"$.{key}"
        return None

    # Flatten nested structures for searching
    search_obj = data
    if "meta" in data and isinstance(data["meta"], dict):
        search_obj = {**data, **data["meta"]}
    if "pagination" in data and isinstance(data["pagination"], dict):
        search_obj = {**data, **data["pagination"]}

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


def _detect_embedded_content(data: Dict) -> Optional[EmbeddedContent]:
    """Detect structured content (CSV, etc.) embedded in JSON string fields.

    When http_request fetches a CSV file, the result is:
    {"url": "...", "status_code": 200, "content": "id,name,email\\n1,Alice,..."}

    This detects when a string field contains parseable structured data.
    """
    # Fields that commonly contain fetched content
    content_fields = ["content", "body", "data", "text", "response", "payload"]

    for field_name in content_fields:
        if field_name not in data:
            continue
        val = data[field_name]
        if not isinstance(val, str) or len(val) < 20:
            continue

        # Check for CSV
        is_csv, csv_info = _detect_csv(val)
        if is_csv and csv_info.columns and len(csv_info.columns) >= 2:
            return EmbeddedContent(
                path=f"$.{field_name}",
                format="csv",
                csv_info=csv_info,
                line_count=csv_info.row_count_estimate + (1 if csv_info.has_header else 0),
                byte_size=len(val.encode("utf-8")),
            )

        # Check for JSON lines (newline-delimited JSON)
        lines = val.strip().split('\n', 5)
        if len(lines) >= 2:
            try:
                # Try parsing first two lines as JSON
                json.loads(lines[0])
                json.loads(lines[1])
                return EmbeddedContent(
                    path=f"$.{field_name}",
                    format="json_lines",
                    line_count=val.count('\n') + 1,
                    byte_size=len(val.encode("utf-8")),
                )
            except (json.JSONDecodeError, ValueError):
                pass

    return None


def analyze_json(data: Any, result_id: str) -> JsonAnalysis:
    """Perform complete JSON analysis."""
    analysis = JsonAnalysis(pattern="unknown")

    if isinstance(data, list):
        # Direct array at root
        analysis.pattern = "array"
        analysis.primary_array = _analyze_array(data, "$")
        analysis.detected_patterns = _detect_patterns(data, None)

    elif isinstance(data, dict):
        # Check for wrapper
        wrapper_path, unwrapped = _detect_wrapper_path(data)
        analysis.wrapper_path = wrapper_path

        # Find all arrays
        all_arrays = _find_all_arrays(data)

        if all_arrays:
            # Sort by depth (shallower first) then by size (larger first)
            all_arrays.sort(key=lambda x: (x[0].count('.'), -len(x[1])))

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
                fields, type_infos, sample = _analyze_array_item_fields([unwrapped])
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

        # Check for embedded structured content (CSV in string fields)
        analysis.embedded_content = _detect_embedded_content(data)

    return analysis


# ---------------------------------------------------------------------------
# Text Analysis
# ---------------------------------------------------------------------------

def _detect_csv(text: str) -> Tuple[bool, CsvInfo]:
    """Detect if text is CSV format."""
    info = CsvInfo()
    lines = text.split('\n', 20)  # Check first 20 lines
    if len(lines) < 2:
        return False, info

    # Try different delimiters
    delimiters = [',', '\t', ';', '|']
    best_delimiter = ','
    best_consistency = 0

    for delim in delimiters:
        counts = [line.count(delim) for line in lines[:10] if line.strip()]
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

    # Parse header
    header_line = lines[0]
    columns = [c.strip().strip('"\'') for c in header_line.split(best_delimiter)]

    # Check if first row looks like a header (non-numeric, reasonable names)
    looks_like_header = all(
        not re.match(r'^-?\d+\.?\d*$', col) and len(col) < 50
        for col in columns if col
    )

    info.has_header = looks_like_header
    if looks_like_header:
        info.columns = columns[:20]
        if len(lines) > 1:
            info.sample_row = lines[1][:200]
    else:
        info.columns = [f"col{i}" for i in range(len(columns))][:20]
        info.sample_row = lines[0][:200]

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


def _detect_json_lines(text: str) -> bool:
    """Detect if text is newline-delimited JSON."""
    lines = [l.strip() for l in text.split('\n', 10) if l.strip()]
    if len(lines) < 2:
        return False

    json_lines = 0
    for line in lines[:10]:
        if line.startswith('{') and line.endswith('}'):
            try:
                json.loads(line)
                json_lines += 1
            except Exception:
                pass

    return json_lines >= 3


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
    if _detect_json_lines(text):
        analysis.format = "json_lines"
        analysis.confidence = 0.9
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

            # If items have a data wrapper (e.g., {"kind": ..., "data": {...fields...}}),
            # prefix field paths with the wrapper key
            field_prefix = f".{arr.item_data_key}" if arr.item_data_key else ""

            # Build field extracts
            fields = arr.item_fields[:5]
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
                fields = [ft.name for ft in json_analysis.field_types[:5]]
                extracts = ", ".join(f"json_extract(result_json,'$.{f}')" for f in fields)
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
) -> str:
    """Generate a compact, actionable summary for the prompt.

    Format priorities:
    1. QUERY first - the exact SQL to copy/use
    2. PATH explicitly labeled - the json_each path
    3. Brief structure info
    """
    parts = []

    if is_json and json_analysis:
        if json_analysis.primary_array:
            arr = json_analysis.primary_array
            path = arr.path

            # QUERY FIRST - most important, put at top
            if query_patterns and query_patterns.list_all:
                parts.append(f"→ QUERY: {query_patterns.list_all}")
            else:
                # Generate inline if no pattern
                if path == "$":
                    each_expr = "json_each(result_json)"
                else:
                    each_expr = f"json_each(result_json,'{path}')"
                field_prefix = f".{arr.item_data_key}" if arr.item_data_key else ""
                fields_to_show = arr.item_fields[:3]
                if fields_to_show:
                    extracts = ", ".join(f"json_extract(r.value,'${field_prefix}.{f}')" for f in fields_to_show)
                    parts.append(
                        f"→ QUERY: SELECT {extracts} "
                        f"FROM __tool_results, {each_expr} AS r "
                        f"WHERE result_id='{result_id}' LIMIT 25"
                    )

            # PATH explicitly labeled - critical for correct queries
            # Include item_data_key if present so agent knows full path
            if arr.item_data_key:
                parts.append(f"  PATH: {path} ({arr.length} items, fields in $.{arr.item_data_key})")
            else:
                parts.append(f"  PATH: {path} ({arr.length} items)")

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
                extracts = ", ".join(f"json_extract(result_json,'$.{f}')" for f in fields)
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

        # Embedded CSV content - show extraction pattern
        if json_analysis.embedded_content and json_analysis.embedded_content.format == "csv":
            emb = json_analysis.embedded_content
            csv = emb.csv_info
            if csv and csv.columns:
                # Generate CREATE TABLE with proper columns
                col_defs = ", ".join(f"{c} TEXT" for c in csv.columns[:15])
                col_names = ", ".join(csv.columns[:15])

                parts.append(f"\n  📄 CSV DATA in {emb.path} ({csv.row_count_estimate} rows)")
                parts.append(f"  COLUMNS: {', '.join(csv.columns[:10])}")
                parts.append(f"  → TO EXTRACT: Create table, split lines, parse with instr/substr")
                parts.append(f"  → SCHEMA: CREATE TABLE IF NOT EXISTS csv_data ({col_defs})")
                parts.append(f"  → See examples for multi-step CSV extraction flow")

    else:
        # Text data
        if text_analysis:
            fmt = text_analysis.format

            if fmt == "csv" and text_analysis.csv_info:
                csv = text_analysis.csv_info
                parts.append(
                    f"→ QUERY: SELECT substr(result_text, 1, 500) "
                    f"FROM __tool_results WHERE result_id='{result_id}'"
                )
                parts.append(f"  TYPE: CSV (~{csv.row_count_estimate} rows)")
                if csv.columns:
                    parts.append(f"  COLUMNS: {', '.join(csv.columns[:10])}")

            elif fmt in ("markdown", "html") and text_analysis.doc_structure:
                doc = text_analysis.doc_structure
                pos = doc.sections[0]["position"] if doc.sections else 1
                parts.append(
                    f"→ QUERY: SELECT substr(result_text, {pos}, 2000) "
                    f"FROM __tool_results WHERE result_id='{result_id}'"
                )
                parts.append(f"  TYPE: {fmt.upper()} ({len(doc.sections)} sections)")

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
    byte_count = len(result_text.encode("utf-8"))
    size_strategy = _determine_size_strategy(byte_count)

    # Try parsing as JSON
    is_json = False
    parsed = None
    json_analysis = None
    text_analysis = None

    try:
        parsed = json.loads(result_text)
        is_json = True
    except Exception:
        pass

    if is_json and parsed is not None:
        json_analysis = analyze_json(parsed, result_id)
    else:
        text_analysis = analyze_text(result_text)

    # Generate query patterns
    query_patterns = _generate_query_patterns(
        result_id, json_analysis, text_analysis, is_json
    )

    # Generate compact summary (pass query_patterns for complete example queries)
    compact_summary = _generate_compact_summary(
        result_id, is_json, size_strategy, json_analysis, text_analysis, query_patterns
    )

    return ResultAnalysis(
        is_json=is_json,
        size_strategy=size_strategy,
        json_analysis=json_analysis,
        text_analysis=text_analysis,
        query_patterns=query_patterns,
        compact_summary=compact_summary,
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
        if ja.pagination and ja.pagination.detected:
            result["json"]["pagination"] = {
                "type": ja.pagination.pagination_type,
                "next": ja.pagination.next_field,
                "total": ja.pagination.total_field,
            }
        if ja.embedded_content:
            ec = ja.embedded_content
            result["json"]["embedded_content"] = {
                "path": ec.path,
                "format": ec.format,
                "line_count": ec.line_count,
                "byte_size": ec.byte_size,
            }
            if ec.csv_info:
                result["json"]["embedded_content"]["csv"] = {
                    "columns": ec.csv_info.columns[:15],
                    "rows": ec.csv_info.row_count_estimate,
                    "delimiter": ec.csv_info.delimiter,
                }

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
            }
        if ta.doc_structure:
            result["text"]["sections"] = [
                {"heading": s["heading"], "pos": s["position"]}
                for s in ta.doc_structure.sections[:10]
            ]

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
