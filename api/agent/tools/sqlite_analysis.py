"""
Deep analysis module for SQLite schema introspection.

Provides comprehensive, robust analysis of messy real-world data including:
- Statistical profiling (distribution, percentiles, outliers)
- Temporal analysis (date ranges, recency, gaps)
- Semantic type detection (20+ patterns)
- Nested content analysis (JSON in JSON, CSV in JSON, etc.)
- Cardinality classification
- Data quality assessment
- Query suggestions

CPU is cheap, LLM inference is expensive - we do thorough preemptive analysis
to give agents a comprehensive understanding of what's in the database.
"""

import csv
import io
import json
import logging
import math
import re
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from dateutil import parser as date_parser
from dateutil.parser import ParserError

logger = logging.getLogger(__name__)

# =============================================================================
# Configuration
# =============================================================================

# Sampling limits
MAX_SAMPLE_ROWS = 500  # Max rows to analyze per table
MAX_NESTED_DEPTH = 4  # Max depth for nested JSON analysis
MAX_NESTED_ITEMS = 50  # Max items to scan in nested arrays
MAX_TEXT_PARSE_CHARS = 50000  # Max chars to try parsing as JSON/CSV

# Output limits
MAX_DISTINCT_SHOW = 8  # Max distinct values to show
MAX_QUERY_SUGGESTIONS = 5  # Max query suggestions per table
MAX_OUTLIERS_SHOW = 3  # Max outliers to mention
MAX_CORRELATION_PAIRS = 3  # Max correlation pairs to report

# Detection thresholds
DATE_PARSE_THRESHOLD = 0.6  # 60% must parse as dates to classify as temporal
JSON_PARSE_THRESHOLD = 0.5  # 50% must parse as JSON
CSV_PARSE_THRESHOLD = 0.4  # 40% must look like CSV
CORRELATION_THRESHOLD = 0.7  # r > 0.7 is "strong" correlation
OUTLIER_IQR_MULTIPLIER = 1.5  # Standard IQR outlier detection

# =============================================================================
# Semantic Type Patterns
# =============================================================================

SEMANTIC_PATTERNS = [
    # High-value patterns first (more specific)
    ("email", re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")),
    ("url", re.compile(r"^https?://[^\s]+$")),
    ("uuid", re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$")),
    ("ipv4", re.compile(r"^(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$")),
    ("ipv6", re.compile(r"^(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}$")),
    ("mac_address", re.compile(r"^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$")),
    ("hex_color", re.compile(r"^#[0-9A-Fa-f]{6}$")),
    ("iso_datetime", re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}")),
    ("iso_date", re.compile(r"^\d{4}-\d{2}-\d{2}$")),
    ("us_date", re.compile(r"^\d{1,2}/\d{1,2}/\d{2,4}$")),
    ("time_only", re.compile(r"^\d{1,2}:\d{2}(:\d{2})?(\s*[APap][Mm])?$")),
    ("phone", re.compile(r"^\+?1?[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}$")),
    ("us_zip", re.compile(r"^\d{5}(-\d{4})?$")),
    ("country_code_2", re.compile(r"^[A-Z]{2}$")),
    ("country_code_3", re.compile(r"^[A-Z]{3}$")),
    ("currency_usd", re.compile(r"^\$[\d,]+\.?\d*$")),
    ("currency_eur", re.compile(r"^€[\d,]+\.?\d*$")),
    ("currency_gbp", re.compile(r"^£[\d,]+\.?\d*$")),
    ("percentage", re.compile(r"^-?\d+\.?\d*%$")),
    ("coordinates", re.compile(r"^-?\d{1,3}\.\d+,\s*-?\d{1,3}\.\d+$")),
    ("file_path_unix", re.compile(r"^(/[^/\0]+)+/?$")),
    ("file_path_windows", re.compile(r"^[A-Za-z]:\\[^<>:\"\\|?*\0]+")),
    ("semver", re.compile(r"^\d+\.\d+\.\d+(-[a-zA-Z0-9.]+)?$")),
    ("md5", re.compile(r"^[a-fA-F0-9]{32}$")),
    ("sha1", re.compile(r"^[a-fA-F0-9]{40}$")),
    ("sha256", re.compile(r"^[a-fA-F0-9]{64}$")),
    ("base64", re.compile(r"^[A-Za-z0-9+/]{20,}={0,2}$")),
    ("slug", re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")),
    ("boolean_text", re.compile(r"^(true|false|yes|no|on|off|1|0)$", re.IGNORECASE)),
]

# Patterns for detecting structured content in text
JSON_START_RE = re.compile(r"^\s*[\[{]")
CSV_DELIMITERS = [",", "\t", "|", ";"]

# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class NumericStats:
    """Statistical profile for numeric columns."""
    count: int = 0
    min_val: Optional[float] = None
    max_val: Optional[float] = None
    mean: Optional[float] = None
    median: Optional[float] = None
    stddev: Optional[float] = None
    p25: Optional[float] = None
    p75: Optional[float] = None
    p95: Optional[float] = None
    distribution: str = "unknown"  # normal, uniform, skewed_left, skewed_right, bimodal
    outliers: list = field(default_factory=list)  # List of outlier values
    outlier_count: int = 0


@dataclass
class TemporalStats:
    """Temporal profile for date/datetime columns."""
    count: int = 0
    parse_rate: float = 0.0  # Fraction that parsed successfully
    min_date: Optional[datetime] = None
    max_date: Optional[datetime] = None
    span_description: str = ""  # "3 days", "2 months", etc.
    recency: str = ""  # "2 hours ago", "yesterday", etc.
    granularity: str = "unknown"  # second, minute, hour, day, month, year
    has_timezone: bool = False
    gaps_detected: bool = False


@dataclass
class TextStats:
    """Profile for text columns."""
    count: int = 0
    min_length: int = 0
    max_length: int = 0
    avg_length: float = 0.0
    empty_count: int = 0
    multiline_count: int = 0
    semantic_type: Optional[str] = None  # Most common detected pattern
    semantic_type_rate: float = 0.0  # Fraction matching semantic type


@dataclass
class NestedContentInfo:
    """Information about nested structured content."""
    format: str  # "json_object", "json_array", "csv", "json_lines", "xml", "markdown"
    detection_rate: float  # Fraction of values containing this format
    # JSON-specific
    json_keys: list = field(default_factory=list)  # Top-level keys
    json_paths: list = field(default_factory=list)  # Notable paths like $.data.items[]
    json_array_sizes: Optional[tuple] = None  # (min, avg, max) for arrays
    # CSV-specific
    csv_columns: list = field(default_factory=list)
    csv_delimiter: str = ","
    csv_row_count_range: Optional[tuple] = None  # (min, max) rows per cell
    # Nested-nested content
    has_deeper_nesting: bool = False
    nested_hint: str = ""  # e.g., "CSV inside $.content", "JSON inside $.data"


@dataclass
class CardinalityInfo:
    """Cardinality classification for a column."""
    distinct_count: int = 0
    total_count: int = 0
    cardinality_type: str = "unknown"  # unique, enum, low, medium, high
    is_unique: bool = False
    is_sequential: bool = False  # Looks like auto-increment
    sample_values: list = field(default_factory=list)  # Sample of distinct values
    value_distribution: dict = field(default_factory=dict)  # value -> count for enums


@dataclass
class DataQuality:
    """Data quality metrics."""
    null_count: int = 0
    null_rate: float = 0.0
    empty_string_count: int = 0
    duplicate_count: int = 0  # Duplicate values
    whitespace_issues: int = 0  # Leading/trailing whitespace
    mixed_types: bool = False
    type_distribution: dict = field(default_factory=dict)  # type_name -> count


@dataclass
class ColumnAnalysis:
    """Complete analysis for a single column."""
    name: str
    declared_type: str
    inferred_type: str  # int, float, text, datetime, json, blob

    # Core stats (populated based on type)
    numeric_stats: Optional[NumericStats] = None
    temporal_stats: Optional[TemporalStats] = None
    text_stats: Optional[TextStats] = None

    # Universal metrics
    cardinality: Optional[CardinalityInfo] = None
    quality: Optional[DataQuality] = None
    nested_content: Optional[NestedContentInfo] = None

    # For generating output
    priority: int = 0  # Higher = more interesting, show first
    summary_parts: list = field(default_factory=list)


@dataclass
class CorrelationInfo:
    """Detected correlation between columns."""
    column_a: str
    column_b: str
    correlation_type: str  # "numeric" (pearson), "categorical", "fk_candidate"
    strength: float = 0.0  # 0-1, higher = stronger
    description: str = ""


@dataclass
class QuerySuggestion:
    """A suggested query for exploring the data."""
    intent: str  # What the query helps with
    sql: str  # The actual SQL


@dataclass
class TableAnalysis:
    """Complete analysis for a table."""
    name: str
    row_count: int
    columns: list  # List of ColumnAnalysis
    correlations: list = field(default_factory=list)  # List of CorrelationInfo
    query_suggestions: list = field(default_factory=list)  # List of QuerySuggestion
    quality_summary: str = ""  # Overall data quality assessment


# =============================================================================
# Numeric Analysis
# =============================================================================


def analyze_numeric(values: list) -> Optional[NumericStats]:
    """Analyze numeric values for statistical profile."""
    # Filter to actual numbers
    nums = []
    for v in values:
        if v is None:
            continue
        if isinstance(v, bool):
            continue  # SQLite stores bools as ints, skip
        if isinstance(v, (int, float)):
            if not math.isnan(v) and not math.isinf(v):
                nums.append(float(v))

    if len(nums) < 2:
        return None

    stats = NumericStats(count=len(nums))
    stats.min_val = min(nums)
    stats.max_val = max(nums)
    stats.mean = statistics.mean(nums)
    stats.median = statistics.median(nums)

    if len(nums) >= 3:
        stats.stddev = statistics.stdev(nums)

    # Percentiles
    sorted_nums = sorted(nums)
    n = len(sorted_nums)
    stats.p25 = sorted_nums[int(n * 0.25)]
    stats.p75 = sorted_nums[int(n * 0.75)]
    stats.p95 = sorted_nums[min(int(n * 0.95), n - 1)]

    # Detect distribution shape
    stats.distribution = _detect_distribution(sorted_nums, stats.mean, stats.median, stats.stddev)

    # Detect outliers using IQR method
    iqr = stats.p75 - stats.p25
    lower_bound = stats.p25 - OUTLIER_IQR_MULTIPLIER * iqr
    upper_bound = stats.p75 + OUTLIER_IQR_MULTIPLIER * iqr

    outliers = [v for v in nums if v < lower_bound or v > upper_bound]
    stats.outlier_count = len(outliers)
    if outliers:
        # Show a few example outliers
        stats.outliers = sorted(set(outliers))[:MAX_OUTLIERS_SHOW]

    return stats


def _detect_distribution(sorted_vals: list, mean: float, median: float, stddev: Optional[float]) -> str:
    """Heuristically detect the distribution shape."""
    if not sorted_vals or len(sorted_vals) < 10:
        return "unknown"

    n = len(sorted_vals)

    # Check for uniformity - values spread evenly
    range_val = sorted_vals[-1] - sorted_vals[0]
    if range_val == 0:
        return "constant"

    # Skewness heuristic: compare mean vs median
    if stddev and stddev > 0:
        skew_indicator = (mean - median) / stddev
        if skew_indicator > 0.5:
            return "skewed_right"
        elif skew_indicator < -0.5:
            return "skewed_left"

    # Check for bimodal - look for a gap in the middle
    mid_start = int(n * 0.4)
    mid_end = int(n * 0.6)
    if mid_end > mid_start:
        mid_range = sorted_vals[mid_end] - sorted_vals[mid_start]
        lower_range = sorted_vals[mid_start] - sorted_vals[0]
        upper_range = sorted_vals[-1] - sorted_vals[mid_end]
        if mid_range > (lower_range + upper_range) * 0.5:
            return "bimodal"

    # Check for roughly uniform distribution
    expected_step = range_val / (n - 1)
    actual_steps = [sorted_vals[i+1] - sorted_vals[i] for i in range(min(20, n-1))]
    if actual_steps:
        avg_step = sum(actual_steps) / len(actual_steps)
        step_variance = sum((s - avg_step) ** 2 for s in actual_steps) / len(actual_steps)
        if step_variance < (expected_step * 0.3) ** 2:
            return "uniform"

    return "normal"


# =============================================================================
# Temporal Analysis
# =============================================================================


def analyze_temporal(values: list) -> Optional[TemporalStats]:
    """Analyze values that might be dates/datetimes."""
    if not values:
        return None

    parsed_dates = []
    has_tz = False

    for v in values:
        if v is None:
            continue
        if isinstance(v, datetime):
            parsed_dates.append(v)
            if v.tzinfo is not None:
                has_tz = True
            continue
        if not isinstance(v, str):
            continue

        # Try parsing as date
        dt = _try_parse_date(v)
        if dt:
            parsed_dates.append(dt)
            if dt.tzinfo is not None:
                has_tz = True

    non_null = [v for v in values if v is not None]
    if not non_null:
        return None

    parse_rate = len(parsed_dates) / len(non_null)

    if parse_rate < DATE_PARSE_THRESHOLD:
        return None

    stats = TemporalStats(
        count=len(parsed_dates),
        parse_rate=parse_rate,
        has_timezone=has_tz,
    )

    if parsed_dates:
        # Normalize to naive datetimes for comparison
        naive_dates = []
        for dt in parsed_dates:
            if dt.tzinfo:
                naive_dates.append(dt.replace(tzinfo=None))
            else:
                naive_dates.append(dt)

        stats.min_date = min(naive_dates)
        stats.max_date = max(naive_dates)

        # Calculate span
        span = stats.max_date - stats.min_date
        stats.span_description = _format_timedelta(span)

        # Calculate recency
        now = datetime.now()
        age = now - stats.max_date
        stats.recency = _format_recency(age)

        # Detect granularity
        stats.granularity = _detect_granularity(naive_dates)

        # Check for gaps (simplified - just check if regular interval)
        if len(naive_dates) >= 3:
            sorted_dates = sorted(naive_dates)
            intervals = [(sorted_dates[i+1] - sorted_dates[i]).total_seconds()
                        for i in range(min(10, len(sorted_dates)-1))]
            if intervals:
                avg_interval = sum(intervals) / len(intervals)
                max_interval = max(intervals)
                if max_interval > avg_interval * 3:
                    stats.gaps_detected = True

    return stats


def _try_parse_date(text: str) -> Optional[datetime]:
    """Try to parse a string as a date/datetime."""
    if not text or len(text) < 4 or len(text) > 50:
        return None

    text = text.strip()

    # Quick rejection for obviously non-dates
    if not any(c.isdigit() for c in text):
        return None

    try:
        # dateutil.parser is very flexible
        return date_parser.parse(text, fuzzy=False)
    except (ParserError, ValueError, OverflowError):
        return None
    except Exception:
        return None


def _format_timedelta(td) -> str:
    """Format a timedelta as a human-readable string."""
    total_seconds = td.total_seconds()

    if total_seconds < 60:
        return f"{int(total_seconds)} seconds"
    elif total_seconds < 3600:
        return f"{int(total_seconds / 60)} minutes"
    elif total_seconds < 86400:
        return f"{total_seconds / 3600:.1f} hours"
    elif total_seconds < 86400 * 30:
        return f"{total_seconds / 86400:.1f} days"
    elif total_seconds < 86400 * 365:
        return f"{total_seconds / (86400 * 30):.1f} months"
    else:
        return f"{total_seconds / (86400 * 365):.1f} years"


def _format_recency(td) -> str:
    """Format how recent something is."""
    total_seconds = td.total_seconds()

    if total_seconds < 0:
        return "in the future"
    elif total_seconds < 60:
        return "just now"
    elif total_seconds < 3600:
        mins = int(total_seconds / 60)
        return f"{mins} minute{'s' if mins != 1 else ''} ago"
    elif total_seconds < 86400:
        hours = int(total_seconds / 3600)
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    elif total_seconds < 86400 * 2:
        return "yesterday"
    elif total_seconds < 86400 * 7:
        days = int(total_seconds / 86400)
        return f"{days} days ago"
    elif total_seconds < 86400 * 30:
        weeks = int(total_seconds / (86400 * 7))
        return f"{weeks} week{'s' if weeks != 1 else ''} ago"
    elif total_seconds < 86400 * 365:
        months = int(total_seconds / (86400 * 30))
        return f"{months} month{'s' if months != 1 else ''} ago"
    else:
        years = total_seconds / (86400 * 365)
        return f"{years:.1f} years ago"


def _detect_granularity(dates: list) -> str:
    """Detect the time granularity of the data."""
    if len(dates) < 2:
        return "unknown"

    # Check if times are all midnight (date-only data)
    all_midnight = all(d.hour == 0 and d.minute == 0 and d.second == 0 for d in dates)
    if all_midnight:
        return "day"

    # Check if seconds are all zero
    all_zero_seconds = all(d.second == 0 for d in dates)
    if all_zero_seconds:
        return "minute"

    # Check for patterns in the seconds
    seconds = [d.second for d in dates]
    if len(set(seconds)) == 1:
        return "minute"

    return "second"


# =============================================================================
# Text & Semantic Type Analysis
# =============================================================================


def analyze_text(values: list) -> Optional[TextStats]:
    """Analyze text values for patterns and structure."""
    texts = [v for v in values if isinstance(v, str)]

    if not texts:
        return None

    stats = TextStats(count=len(texts))

    lengths = [len(t) for t in texts]
    stats.min_length = min(lengths)
    stats.max_length = max(lengths)
    stats.avg_length = sum(lengths) / len(lengths)
    stats.empty_count = sum(1 for t in texts if not t.strip())
    stats.multiline_count = sum(1 for t in texts if '\n' in t)

    # Detect semantic type
    if texts:
        semantic_type, rate = _detect_semantic_type(texts)
        if rate >= 0.5:  # At least 50% match
            stats.semantic_type = semantic_type
            stats.semantic_type_rate = rate

    return stats


def _detect_semantic_type(texts: list) -> tuple:
    """Detect the most common semantic type in a list of texts."""
    if not texts:
        return None, 0.0

    # Sample if too many
    sample = texts[:100]

    type_counts = {}
    for text in sample:
        text = text.strip()
        if not text:
            continue
        for type_name, pattern in SEMANTIC_PATTERNS:
            if pattern.match(text):
                type_counts[type_name] = type_counts.get(type_name, 0) + 1
                break  # First match wins

    if not type_counts:
        return None, 0.0

    best_type = max(type_counts, key=type_counts.get)
    rate = type_counts[best_type] / len(sample)

    return best_type, rate


# =============================================================================
# Nested Content Analysis (JSON, CSV, etc.)
# =============================================================================


def analyze_nested_content(values: list) -> Optional[NestedContentInfo]:
    """Detect and analyze nested structured content in text values."""
    texts = [v for v in values if isinstance(v, str) and len(v) > 5]

    if not texts:
        return None

    # Sample for efficiency
    sample = texts[:50]

    # Try JSON detection
    json_info = _detect_json_content(sample)
    if json_info and json_info.detection_rate >= JSON_PARSE_THRESHOLD:
        return json_info

    # Try CSV detection
    csv_info = _detect_csv_content(sample)
    if csv_info and csv_info.detection_rate >= CSV_PARSE_THRESHOLD:
        return csv_info

    # Try JSON Lines detection
    jsonl_info = _detect_jsonlines_content(sample)
    if jsonl_info and jsonl_info.detection_rate >= 0.3:
        return jsonl_info

    return None


def _detect_json_content(texts: list) -> Optional[NestedContentInfo]:
    """Detect JSON content in text values."""
    parsed_objects = []
    parsed_arrays = []

    for text in texts:
        if len(text) > MAX_TEXT_PARSE_CHARS:
            text = text[:MAX_TEXT_PARSE_CHARS]

        if not JSON_START_RE.match(text):
            continue

        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                parsed_objects.append(obj)
            elif isinstance(obj, list):
                parsed_arrays.append(obj)
        except (json.JSONDecodeError, ValueError):
            continue

    total_parsed = len(parsed_objects) + len(parsed_arrays)
    if total_parsed == 0:
        return None

    detection_rate = total_parsed / len(texts)

    info = NestedContentInfo(
        format="json_object" if len(parsed_objects) > len(parsed_arrays) else "json_array",
        detection_rate=detection_rate,
    )

    # Analyze JSON structure
    if parsed_objects:
        all_keys = {}
        for obj in parsed_objects[:10]:
            for key in obj.keys():
                all_keys[key] = all_keys.get(key, 0) + 1
        # Sort by frequency
        info.json_keys = sorted(all_keys.keys(), key=lambda k: -all_keys[k])[:8]

        # Collect paths
        paths = []
        for obj in parsed_objects[:3]:
            _collect_json_paths(obj, paths, "", 0)
        info.json_paths = paths[:MAX_NESTED_ITEMS]

        # Check for deeper nesting
        for obj in parsed_objects[:3]:
            if _has_nested_structured_content(obj):
                info.has_deeper_nesting = True
                info.nested_hint = _describe_nested_content(obj)
                break

    if parsed_arrays:
        sizes = [len(arr) for arr in parsed_arrays]
        info.json_array_sizes = (min(sizes), sum(sizes) / len(sizes), max(sizes))

        # Analyze array item structure
        items = []
        for arr in parsed_arrays[:5]:
            items.extend(arr[:10])

        if items and isinstance(items[0], dict):
            all_keys = {}
            for item in items[:20]:
                if isinstance(item, dict):
                    for key in item.keys():
                        all_keys[key] = all_keys.get(key, 0) + 1
            info.json_keys = sorted(all_keys.keys(), key=lambda k: -all_keys[k])[:8]

    return info


def _collect_json_paths(obj, paths: list, prefix: str, depth: int):
    """Recursively collect notable JSON paths."""
    if depth >= MAX_NESTED_DEPTH or len(paths) >= MAX_NESTED_ITEMS:
        return

    if isinstance(obj, dict):
        for key, val in list(obj.items())[:8]:
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(val, list) and val:
                paths.append(f"$.{path}[]")
                if isinstance(val[0], dict):
                    _collect_json_paths(val[0], paths, f"{path}[]", depth + 1)
            elif isinstance(val, dict):
                _collect_json_paths(val, paths, path, depth + 1)
    elif isinstance(obj, list) and obj:
        if isinstance(obj[0], dict):
            _collect_json_paths(obj[0], paths, prefix, depth + 1)


def _has_nested_structured_content(obj, depth: int = 0) -> bool:
    """Check if a JSON object contains nested structured content (JSON/CSV in strings)."""
    if depth >= MAX_NESTED_DEPTH:
        return False

    if isinstance(obj, str):
        if len(obj) > 20:
            if JSON_START_RE.match(obj.strip()):
                try:
                    json.loads(obj)
                    return True
                except Exception:
                    pass
            # Check for CSV-like content
            if '\n' in obj and ',' in obj:
                lines = obj.strip().split('\n')
                if len(lines) >= 2:
                    return True
    elif isinstance(obj, dict):
        for val in list(obj.values())[:10]:
            if _has_nested_structured_content(val, depth + 1):
                return True
    elif isinstance(obj, list):
        for item in obj[:5]:
            if _has_nested_structured_content(item, depth + 1):
                return True

    return False


def _describe_nested_content(obj, prefix: str = "$") -> str:
    """Describe what nested content was found."""
    if isinstance(obj, dict):
        for key, val in obj.items():
            if isinstance(val, str) and len(val) > 20:
                if JSON_START_RE.match(val.strip()):
                    try:
                        json.loads(val)
                        return f"JSON in {prefix}.{key}"
                    except Exception:
                        pass
                if '\n' in val and ',' in val:
                    return f"CSV in {prefix}.{key}"
            elif isinstance(val, (dict, list)):
                result = _describe_nested_content(val, f"{prefix}.{key}")
                if result:
                    return result
    elif isinstance(obj, list) and obj:
        return _describe_nested_content(obj[0], f"{prefix}[0]")
    return ""


def _detect_csv_content(texts: list) -> Optional[NestedContentInfo]:
    """Detect CSV content in text values."""
    csv_matches = []

    for text in texts:
        if len(text) > MAX_TEXT_PARSE_CHARS:
            text = text[:MAX_TEXT_PARSE_CHARS]

        csv_info = _try_parse_csv(text)
        if csv_info:
            csv_matches.append(csv_info)

    if not csv_matches:
        return None

    detection_rate = len(csv_matches) / len(texts)

    # Aggregate CSV info
    delimiters = [m["delimiter"] for m in csv_matches]
    most_common_delim = max(set(delimiters), key=delimiters.count)

    columns = []
    for m in csv_matches:
        if m.get("columns"):
            columns = m["columns"]
            break

    row_counts = [m["row_count"] for m in csv_matches]

    return NestedContentInfo(
        format="csv",
        detection_rate=detection_rate,
        csv_delimiter=most_common_delim,
        csv_columns=columns,
        csv_row_count_range=(min(row_counts), max(row_counts)) if row_counts else None,
    )


def _try_parse_csv(text: str) -> Optional[dict]:
    """Try to parse text as CSV and return info."""
    if not text or '\n' not in text:
        return None

    lines = text.strip().split('\n')
    if len(lines) < 2:
        return None

    # Detect delimiter
    first_line = lines[0]
    delimiter = max(CSV_DELIMITERS, key=lambda d: first_line.count(d))

    if first_line.count(delimiter) < 1:
        return None

    try:
        reader = csv.reader(io.StringIO(text), delimiter=delimiter)
        rows = []
        for row in reader:
            if row:
                rows.append(row)
            if len(rows) >= 10:
                break

        if len(rows) < 2:
            return None

        # Check column count consistency
        col_counts = [len(row) for row in rows]
        if len(set(col_counts)) > 2:  # Too inconsistent
            return None

        col_count = max(set(col_counts), key=col_counts.count)

        # Check for header
        columns = None
        if _looks_like_csv_header(rows[0], rows[1] if len(rows) > 1 else None):
            columns = [str(c).strip()[:30] for c in rows[0][:10]]

        return {
            "delimiter": delimiter,
            "col_count": col_count,
            "columns": columns,
            "row_count": len(lines),
        }
    except Exception:
        return None


def _looks_like_csv_header(first_row: list, second_row: Optional[list]) -> bool:
    """Check if the first row looks like a header."""
    if not first_row:
        return False

    # Headers typically:
    # - Are all strings
    # - Don't look like numbers
    # - Are unique

    first_strs = [str(c).strip() for c in first_row]

    # Check uniqueness
    if len(set(first_strs)) != len(first_strs):
        return False

    # Check if first row is all non-numeric and second row has numbers
    first_numeric = sum(1 for s in first_strs if _is_numeric_string(s))

    if second_row:
        second_strs = [str(c).strip() for c in second_row]
        second_numeric = sum(1 for s in second_strs if _is_numeric_string(s))
        if first_numeric == 0 and second_numeric >= len(second_row) // 2:
            return True

    if first_numeric == 0:
        return True

    return False


def _is_numeric_string(s: str) -> bool:
    """Check if string looks like a number."""
    try:
        float(s.replace(',', ''))
        return True
    except ValueError:
        return False


def _detect_jsonlines_content(texts: list) -> Optional[NestedContentInfo]:
    """Detect JSON Lines (newline-delimited JSON) content."""
    jsonl_matches = 0
    all_keys = {}

    for text in texts:
        lines = text.strip().split('\n')
        if len(lines) < 2:
            continue

        parsed_count = 0
        for line in lines[:20]:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                parsed_count += 1
                if isinstance(obj, dict):
                    for key in obj.keys():
                        all_keys[key] = all_keys.get(key, 0) + 1
            except Exception:
                pass

        if parsed_count >= len(lines) * 0.5:
            jsonl_matches += 1

    if jsonl_matches == 0:
        return None

    return NestedContentInfo(
        format="json_lines",
        detection_rate=jsonl_matches / len(texts),
        json_keys=sorted(all_keys.keys(), key=lambda k: -all_keys[k])[:8],
    )


# =============================================================================
# Cardinality Analysis
# =============================================================================


def analyze_cardinality(values: list) -> CardinalityInfo:
    """Analyze the cardinality of values."""
    non_null = [v for v in values if v is not None]

    info = CardinalityInfo(
        total_count=len(values),
    )

    if not non_null:
        return info

    # Count distinct values
    try:
        distinct_vals = list(set(non_null))
    except TypeError:
        # Unhashable types
        distinct_vals = []
        seen = []
        for v in non_null:
            if v not in seen:
                seen.append(v)
                distinct_vals.append(v)

    info.distinct_count = len(distinct_vals)

    # Classify cardinality
    ratio = info.distinct_count / len(non_null)

    if ratio == 1.0:
        info.cardinality_type = "unique"
        info.is_unique = True
    elif info.distinct_count <= 2:
        info.cardinality_type = "binary"
    elif info.distinct_count <= 10:
        info.cardinality_type = "enum"
    elif ratio < 0.05:
        info.cardinality_type = "low"
    elif ratio < 0.5:
        info.cardinality_type = "medium"
    else:
        info.cardinality_type = "high"

    # Sample values for enum/low cardinality
    if info.distinct_count <= MAX_DISTINCT_SHOW:
        info.sample_values = distinct_vals[:MAX_DISTINCT_SHOW]

        # Count distribution for enums
        if info.cardinality_type in ("binary", "enum"):
            for val in distinct_vals:
                info.value_distribution[val] = sum(1 for v in non_null if v == val)
    else:
        # Just show a sample
        info.sample_values = distinct_vals[:3]

    # Check if sequential (like auto-increment)
    if info.is_unique and all(isinstance(v, int) for v in non_null):
        sorted_vals = sorted(non_null)
        # Avoid materializing huge ranges (can explode memory for sparse large ints).
        span = sorted_vals[-1] - sorted_vals[0] + 1
        if len(sorted_vals) > 1 and span == len(sorted_vals):
            info.is_sequential = True

    return info


# =============================================================================
# Data Quality Analysis
# =============================================================================


def analyze_quality(values: list) -> DataQuality:
    """Analyze data quality issues."""
    quality = DataQuality()

    # Null analysis
    quality.null_count = sum(1 for v in values if v is None)
    quality.null_rate = quality.null_count / len(values) if values else 0.0

    # Type distribution
    for v in values:
        if v is None:
            type_name = "null"
        elif isinstance(v, bool):
            type_name = "bool"
        elif isinstance(v, int):
            type_name = "int"
        elif isinstance(v, float):
            type_name = "float"
        elif isinstance(v, str):
            type_name = "text"
        elif isinstance(v, bytes):
            type_name = "blob"
        else:
            type_name = "other"
        quality.type_distribution[type_name] = quality.type_distribution.get(type_name, 0) + 1

    # Check for mixed types (excluding nulls)
    non_null_types = {k for k, v in quality.type_distribution.items() if k != "null" and v > 0}
    quality.mixed_types = len(non_null_types) > 1

    # String-specific quality
    texts = [v for v in values if isinstance(v, str)]
    quality.empty_string_count = sum(1 for t in texts if not t.strip())
    quality.whitespace_issues = sum(1 for t in texts if t != t.strip())

    # Duplicate count
    try:
        non_null = [v for v in values if v is not None]
        quality.duplicate_count = len(non_null) - len(set(non_null))
    except TypeError:
        pass  # Unhashable

    return quality


# =============================================================================
# Correlation Detection
# =============================================================================


def detect_correlations(columns: list, row_data: list) -> list:
    """Detect correlations between columns."""
    correlations = []

    if len(columns) < 2 or len(row_data) < 10:
        return correlations

    # Extract column values
    col_values = {}
    for col in columns:
        idx = col.get("index", 0)
        col_values[col["name"]] = [row[idx] if idx < len(row) else None for row in row_data]

    # Find numeric columns
    numeric_cols = []
    for col in columns:
        vals = col_values.get(col["name"], [])
        nums = [v for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if len(nums) >= len(vals) * 0.8:
            numeric_cols.append(col["name"])

    # Calculate correlations between numeric columns
    from itertools import combinations
    for col_a, col_b in combinations(numeric_cols, 2):
        vals_a = col_values[col_a]
        vals_b = col_values[col_b]

        r = _pearson_correlation(vals_a, vals_b)
        if r is not None and abs(r) >= CORRELATION_THRESHOLD:
            correlations.append(CorrelationInfo(
                column_a=col_a,
                column_b=col_b,
                correlation_type="numeric",
                strength=abs(r),
                description=f"r={r:.2f} {'positive' if r > 0 else 'negative'}",
            ))

        if len(correlations) >= MAX_CORRELATION_PAIRS:
            break

    # Detect FK candidates (unique col matching non-unique col with _id suffix)
    for col in columns:
        name = col["name"]
        if name.endswith("_id") or name.endswith("Id"):
            vals = col_values.get(name, [])
            non_null = [v for v in vals if v is not None]
            if non_null:
                distinct = len(set(non_null))
                if distinct < len(non_null) * 0.9:  # Not unique, likely FK
                    correlations.append(CorrelationInfo(
                        column_a=name,
                        column_b="",
                        correlation_type="fk_candidate",
                        strength=0.8,
                        description=f"likely foreign key ({distinct} distinct values)",
                    ))

    return correlations[:MAX_CORRELATION_PAIRS]


def _pearson_correlation(x_vals: list, y_vals: list) -> Optional[float]:
    """Calculate Pearson correlation coefficient."""
    pairs = [(x, y) for x, y in zip(x_vals, y_vals)
             if isinstance(x, (int, float)) and isinstance(y, (int, float))
             and not isinstance(x, bool) and not isinstance(y, bool)]

    if len(pairs) < 10:
        return None

    x = [p[0] for p in pairs]
    y = [p[1] for p in pairs]

    n = len(x)
    mean_x = sum(x) / n
    mean_y = sum(y) / n

    numerator = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
    denom_x = math.sqrt(sum((xi - mean_x) ** 2 for xi in x))
    denom_y = math.sqrt(sum((yi - mean_y) ** 2 for yi in y))

    if denom_x == 0 or denom_y == 0:
        return None

    return numerator / (denom_x * denom_y)


# =============================================================================
# Query Suggestions
# =============================================================================


def generate_query_suggestions(table_name: str, columns: list, row_count: int) -> list:
    """Generate helpful query suggestions based on the data."""
    suggestions = []

    # Find enum columns for GROUP BY
    enum_cols = [c for c in columns if c.get("cardinality_type") in ("binary", "enum", "low")]
    numeric_cols = [c for c in columns if c.get("inferred_type") in ("int", "float")]
    date_cols = [c for c in columns if c.get("inferred_type") == "datetime"]

    # Distribution by enum
    for col in enum_cols[:2]:
        suggestions.append(QuerySuggestion(
            intent=f"Count by {col['name']}",
            sql=f"SELECT {col['name']}, COUNT(*) as cnt FROM {table_name} GROUP BY {col['name']} ORDER BY cnt DESC",
        ))

    # Stats for numeric columns
    for col in numeric_cols[:2]:
        suggestions.append(QuerySuggestion(
            intent=f"Stats for {col['name']}",
            sql=f"SELECT MIN({col['name']}), AVG({col['name']}), MAX({col['name']}) FROM {table_name}",
        ))

    # Time-based analysis
    for col in date_cols[:1]:
        suggestions.append(QuerySuggestion(
            intent=f"Recent records by {col['name']}",
            sql=f"SELECT * FROM {table_name} ORDER BY {col['name']} DESC LIMIT 20",
        ))

    # Enum + numeric combination (pivot-like)
    if enum_cols and numeric_cols:
        suggestions.append(QuerySuggestion(
            intent=f"Stats by {enum_cols[0]['name']}",
            sql=f"SELECT {enum_cols[0]['name']}, AVG({numeric_cols[0]['name']}), COUNT(*) FROM {table_name} GROUP BY {enum_cols[0]['name']}",
        ))

    # Nested JSON extraction
    json_cols = [c for c in columns if c.get("nested_format") in ("json_object", "json_array")]
    for col in json_cols[:1]:
        paths = col.get("json_paths", [])
        if paths:
            # Find an array path
            array_paths = [p for p in paths if p.endswith("[]")]
            if array_paths:
                path = array_paths[0].rstrip("[]")
                suggestions.append(QuerySuggestion(
                    intent=f"Extract array from {col['name']}",
                    sql=f"SELECT j.value FROM {table_name}, json_each({col['name']}, '{path}') j LIMIT 50",
                ))

    return suggestions[:MAX_QUERY_SUGGESTIONS]


# =============================================================================
# Main Analysis Entry Point
# =============================================================================


def analyze_column(
    name: str,
    declared_type: str,
    values: list,
    row_count: int,
) -> ColumnAnalysis:
    """Perform comprehensive analysis on a single column."""

    analysis = ColumnAnalysis(
        name=name,
        declared_type=declared_type,
        inferred_type="unknown",
    )

    # Quality analysis (always)
    analysis.quality = analyze_quality(values)

    # Cardinality analysis (always)
    analysis.cardinality = analyze_cardinality(values)

    non_null = [v for v in values if v is not None]

    if not non_null:
        analysis.inferred_type = "null"
        analysis.priority = 0
        return analysis

    # Determine primary type
    type_dist = analysis.quality.type_distribution
    non_null_types = {k: v for k, v in type_dist.items() if k != "null"}

    if not non_null_types:
        analysis.inferred_type = "null"
        return analysis

    primary_type = max(non_null_types, key=non_null_types.get)

    # Type-specific analysis
    if primary_type in ("int", "float"):
        analysis.inferred_type = primary_type
        analysis.numeric_stats = analyze_numeric(values)

        # But also check if it might be temporal (epoch timestamps)
        if primary_type == "int":
            sample_nums = [v for v in non_null if isinstance(v, int)][:5]
            if sample_nums and all(1000000000 < n < 2000000000 for n in sample_nums):
                # Looks like Unix timestamps
                analysis.inferred_type = "epoch_timestamp"
                analysis.priority = 60

        if analysis.numeric_stats:
            analysis.priority = max(analysis.priority, 40)

    elif primary_type == "text":
        analysis.inferred_type = "text"
        analysis.text_stats = analyze_text(values)

        # Check for temporal
        temporal_stats = analyze_temporal(values)
        if temporal_stats and temporal_stats.parse_rate >= DATE_PARSE_THRESHOLD:
            analysis.inferred_type = "datetime"
            analysis.temporal_stats = temporal_stats
            analysis.priority = 70
        else:
            # Check for nested content
            nested = analyze_nested_content(values)
            if nested:
                analysis.nested_content = nested
                analysis.inferred_type = nested.format
                analysis.priority = 80
            elif analysis.text_stats and analysis.text_stats.semantic_type:
                analysis.priority = 50

    elif primary_type == "blob":
        analysis.inferred_type = "blob"
        analysis.priority = 20

    # Boost priority for interesting cardinality
    if analysis.cardinality:
        if analysis.cardinality.cardinality_type == "enum":
            analysis.priority = max(analysis.priority, 60)
        elif analysis.cardinality.is_unique:
            analysis.priority = max(analysis.priority, 30)

    # Build summary parts
    analysis.summary_parts = _build_summary_parts(analysis)

    return analysis


def _build_summary_parts(analysis: ColumnAnalysis) -> list:
    """Build human-readable summary parts for a column."""
    parts = []

    # Type indicator
    type_str = analysis.inferred_type.upper()
    if analysis.inferred_type != analysis.declared_type.lower():
        type_str += f" (declared {analysis.declared_type})"
    parts.append(type_str)

    # Numeric stats
    if analysis.numeric_stats:
        ns = analysis.numeric_stats
        if ns.mean is not None:
            stats_str = f"μ={ns.mean:.2f}"
            if ns.stddev:
                stats_str += f" σ={ns.stddev:.2f}"
            stats_str += f" [{ns.min_val:.2f}-{ns.max_val:.2f}]"
            parts.append(stats_str)

        if ns.distribution and ns.distribution != "unknown":
            parts.append(ns.distribution)

        if ns.outlier_count > 0:
            parts.append(f"{ns.outlier_count} outliers")

    # Temporal stats
    if analysis.temporal_stats:
        ts = analysis.temporal_stats
        if ts.min_date and ts.max_date:
            parts.append(f"range: {ts.min_date.date()} to {ts.max_date.date()}")
        if ts.span_description:
            parts.append(f"span: {ts.span_description}")
        if ts.recency:
            parts.append(f"latest: {ts.recency}")
        if ts.granularity != "unknown":
            parts.append(f"granularity: {ts.granularity}")

    # Text stats
    if analysis.text_stats:
        ts = analysis.text_stats
        if ts.semantic_type:
            parts.append(f"semantic: {ts.semantic_type} ({ts.semantic_type_rate:.0%})")
        if ts.min_length != ts.max_length:
            parts.append(f"len: {ts.min_length}-{ts.max_length}")
        if ts.multiline_count > 0:
            parts.append(f"{ts.multiline_count} multiline")

    # Nested content
    if analysis.nested_content:
        nc = analysis.nested_content
        parts.append(f"contains: {nc.format}")
        if nc.json_keys:
            parts.append(f"keys: [{', '.join(nc.json_keys[:5])}]")
        if nc.json_paths:
            parts.append(f"paths: [{', '.join(nc.json_paths[:3])}]")
        if nc.csv_columns:
            parts.append(f"csv cols: [{', '.join(nc.csv_columns[:5])}]")
        if nc.has_deeper_nesting:
            parts.append(f"nested: {nc.nested_hint}")

    # Cardinality
    if analysis.cardinality:
        card = analysis.cardinality
        if card.cardinality_type == "unique":
            parts.append("UNIQUE")
            if card.is_sequential:
                parts.append("sequential")
        elif card.cardinality_type in ("binary", "enum"):
            if card.sample_values:
                vals_str = ", ".join(repr(v)[:20] for v in card.sample_values[:5])
                parts.append(f"values: [{vals_str}]")
            if card.value_distribution and len(card.value_distribution) <= 5:
                dist_str = ", ".join(f"{k}: {v}" for k, v in
                                    sorted(card.value_distribution.items(), key=lambda x: -x[1])[:5])
                parts.append(f"dist: {dist_str}")
        elif card.cardinality_type == "low":
            parts.append(f"{card.distinct_count} distinct values")

    # Quality issues
    if analysis.quality:
        q = analysis.quality
        if q.null_rate > 0.05:
            parts.append(f"nulls: {q.null_rate:.0%}")
        if q.mixed_types:
            parts.append("MIXED TYPES")
        if q.empty_string_count > 0:
            parts.append(f"{q.empty_string_count} empty strings")

    return parts


def analyze_table(
    cursor,
    table_name: str,
    row_count: int,
    max_rows: int = MAX_SAMPLE_ROWS,
) -> TableAnalysis:
    """Perform comprehensive analysis on a table."""

    analysis = TableAnalysis(
        name=table_name,
        row_count=row_count,
        columns=[],
    )

    if row_count == 0:
        return analysis

    # Get column info
    try:
        cursor.execute(f'PRAGMA table_info("{table_name}");')
        columns_info = [(row[1], row[2] or "") for row in cursor.fetchall()]
    except Exception as e:
        logger.warning(f"Failed to get column info for {table_name}: {e}")
        return analysis

    if not columns_info:
        return analysis

    # Fetch sample rows
    try:
        limit = min(max_rows, row_count)
        cursor.execute(f'SELECT * FROM "{table_name}" LIMIT {limit};')
        rows = [tuple(row) for row in cursor.fetchall()]
    except Exception as e:
        logger.warning(f"Failed to fetch rows from {table_name}: {e}")
        return analysis

    # Analyze each column
    for idx, (col_name, col_type) in enumerate(columns_info):
        values = [row[idx] if idx < len(row) else None for row in rows]

        col_analysis = analyze_column(col_name, col_type, values, row_count)
        col_analysis_dict = {
            "name": col_name,
            "index": idx,
            "declared_type": col_type,
            "inferred_type": col_analysis.inferred_type,
            "priority": col_analysis.priority,
            "summary_parts": col_analysis.summary_parts,
            "cardinality_type": col_analysis.cardinality.cardinality_type if col_analysis.cardinality else None,
            "nested_format": col_analysis.nested_content.format if col_analysis.nested_content else None,
            "json_paths": col_analysis.nested_content.json_paths if col_analysis.nested_content else None,
        }

        # Add the full analysis object for later use
        col_analysis_dict["_analysis"] = col_analysis

        analysis.columns.append(col_analysis_dict)

    # Detect correlations
    columns_for_corr = [{"name": c["name"], "index": c["index"]} for c in analysis.columns]
    analysis.correlations = detect_correlations(columns_for_corr, rows)

    # Generate query suggestions
    analysis.query_suggestions = generate_query_suggestions(table_name, analysis.columns, row_count)

    # Overall quality summary
    quality_issues = []
    for col in analysis.columns:
        col_analysis = col.get("_analysis")
        if col_analysis and col_analysis.quality:
            if col_analysis.quality.null_rate > 0.2:
                quality_issues.append(f"{col['name']} has {col_analysis.quality.null_rate:.0%} nulls")
            if col_analysis.quality.mixed_types:
                quality_issues.append(f"{col['name']} has mixed types")

    if quality_issues:
        analysis.quality_summary = "; ".join(quality_issues[:3])
    else:
        analysis.quality_summary = "Clean data - no major quality issues"

    return analysis


def format_table_analysis(analysis: TableAnalysis) -> list:
    """Format table analysis as lines for the schema prompt."""
    lines = []

    if not analysis.columns:
        return lines

    # Sort columns by priority (highest first)
    sorted_cols = sorted(analysis.columns, key=lambda c: -c.get("priority", 0))

    # Column details
    lines.append("  COLUMNS:")
    for col in sorted_cols[:12]:  # Limit to 12 most interesting columns
        col_analysis = col.get("_analysis")
        if not col_analysis:
            continue

        # Build column line
        name = col["name"]
        declared = col["declared_type"]
        inferred = col["inferred_type"]

        type_str = inferred.upper()
        if declared and declared.upper() != inferred.upper():
            type_str = f"{declared} → {inferred}"

        # Compact summary
        parts = col.get("summary_parts", [])
        if parts:
            # Skip the type part since we already show it
            summary_parts = [p for p in parts[1:] if p]
            summary = " | ".join(summary_parts[:4])
            lines.append(f"    {name} {type_str}: {summary}")
        else:
            lines.append(f"    {name} {type_str}")

    # Correlations
    if analysis.correlations:
        lines.append("  CORRELATIONS:")
        for corr in analysis.correlations:
            if corr.correlation_type == "numeric":
                lines.append(f"    {corr.column_a} ↔ {corr.column_b}: {corr.description}")
            elif corr.correlation_type == "fk_candidate":
                lines.append(f"    {corr.column_a}: {corr.description}")

    # Quality summary
    if analysis.quality_summary:
        lines.append(f"  QUALITY: {analysis.quality_summary}")

    # Query suggestions
    if analysis.query_suggestions:
        lines.append("  QUERIES:")
        for qs in analysis.query_suggestions[:3]:
            lines.append(f"    → {qs.intent}: {qs.sql}")

    return lines
