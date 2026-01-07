"""SQLite guardrails for agent-managed databases."""

import csv
import logging
import math
import re
import sqlite3
import time
from typing import Optional

from ..core.csv_utils import (
    build_csv_sample,
    detect_csv_dialect,
    normalize_csv_text,
    read_csv_rows,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Safe custom functions for text analysis (no I/O, pure computation)
# ---------------------------------------------------------------------------

def _regexp(pattern: str, string: Optional[str]) -> bool:
    """REGEXP function for pattern matching in queries."""
    if string is None or pattern is None:
        return False
    try:
        return bool(re.search(pattern, string))
    except re.error:
        return False


def _regexp_extract(string: Optional[str], pattern: str, group: int = 0) -> Optional[str]:
    """Extract first regex match from string.

    Usage: regexp_extract(column, 'pattern') or regexp_extract(column, '(group)', 1)
    """
    if string is None or pattern is None:
        return None
    try:
        match = re.search(pattern, string)
        return match.group(group) if match else None
    except (re.error, IndexError):
        return None


def _word_count(string: Optional[str]) -> int:
    """Count words in a string."""
    if not string:
        return 0
    return len(string.split())


def _char_count(string: Optional[str]) -> int:
    """Count characters in a string."""
    return len(string) if string else 0


def _regexp_find_all(string: Optional[str], pattern: str, separator: str = "|") -> Optional[str]:
    r"""Find all regex matches, return as separator-delimited string.

    Usage: regexp_find_all(column, '\$[\d,]+', '|')
    Returns: "$8,941|$9,199|$10,500" or NULL if no matches
    """
    if string is None or pattern is None:
        return None
    try:
        matches = re.findall(pattern, string)
        if not matches:
            return None
        # Dedupe while preserving order, limit to 20 matches
        seen = set()
        unique = []
        for m in matches:
            if m not in seen:
                seen.add(m)
                unique.append(m)
                if len(unique) >= 20:
                    break
        return separator.join(unique)
    except re.error:
        return None


def _grep_context(string: Optional[str], pattern: str, context_chars: int = 100) -> Optional[str]:
    """Find pattern and return match with surrounding context.

    Usage: grep_context(column, 'Price', 50)
    Returns: "...t price is $8,941.04 for the RTX..." or NULL if not found
    """
    if string is None or pattern is None:
        return None
    try:
        match = re.search(pattern, string, re.IGNORECASE)
        if not match:
            return None
        start = max(0, match.start() - context_chars)
        end = min(len(string), match.end() + context_chars)
        snippet = string[start:end]
        # Add ellipsis indicators
        prefix = "..." if start > 0 else ""
        suffix = "..." if end < len(string) else ""
        return f"{prefix}{snippet}{suffix}"
    except re.error:
        return None


def _grep_context_all(string: Optional[str], pattern: str, context_chars: int = 50, max_matches: int = 10) -> Optional[str]:
    r"""Find all pattern matches with surrounding context, as JSON array.

    Usage: SELECT ctx.value FROM json_each(grep_context_all(col, 'pattern', 60, 10)) AS ctx
    Returns: JSON array of context snippets, usable with json_each()
    """
    import json as json_module
    if string is None or pattern is None:
        return None
    try:
        results = []
        for i, match in enumerate(re.finditer(pattern, string)):
            if i >= max_matches:
                break
            start = max(0, match.start() - context_chars)
            end = min(len(string), match.end() + context_chars)
            snippet = string[start:end].replace('\n', ' ')
            prefix = "..." if start > 0 else ""
            suffix = "..." if end < len(string) else ""
            results.append(f"{prefix}{snippet}{suffix}")
        return json_module.dumps(results) if results else None
    except re.error:
        return None


def _split_sections(string: Optional[str], delimiter: str = "\n\n") -> Optional[str]:
    r"""Split text into sections by delimiter, as JSON array for json_each.

    Usage: SELECT s.value FROM json_each(split_sections(col, '\n\n')) AS s
    Great for processing markdown by paragraph or section.
    """
    import json as json_module
    if string is None:
        return None
    sections = [s.strip() for s in string.split(delimiter) if s.strip()]
    return json_module.dumps(sections) if sections else None


def _substr_range(string: Optional[str], start: int, end: int) -> Optional[str]:
    """Extract substring by start and end position (0-indexed, exclusive end).

    Usage: substr_range(col, 0, 3000) for first 3000 chars
           substr_range(col, 3000, 6000) for next 3000 chars
    Useful for batched processing of very large text.
    """
    if string is None:
        return None
    return string[start:end]


def _json_length(json_str: Optional[str]) -> Optional[int]:
    """Return length of JSON array or object (alias for json_array_length).

    Agents often hallucinate 'json_length' instead of 'json_array_length'.
    This provides a forgiving alias that handles both arrays and objects.
    """
    import json as json_module
    if json_str is None:
        return None
    try:
        data = json_module.loads(json_str)
        if isinstance(data, (list, dict)):
            return len(data)
        return None
    except (ValueError, TypeError):
        return None


def _coerce_csv_header_flag(value: object) -> int:
    if value is None:
        return 1
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, int):
        return 1 if value else 0
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"0", "false", "no"}:
            return 0
        if lowered in {"1", "true", "yes"}:
            return 1
        try:
            return 1 if int(lowered) != 0 else 0
        except ValueError:
            return 1
    return 1


def _sanitize_csv_header(value: str, index: int) -> str:
    header = str(value).strip()
    if not header:
        return f"col_{index}"
    return header


def _dedupe_headers(headers: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    deduped: list[str] = []
    for index, raw in enumerate(headers):
        base = _sanitize_csv_header(raw, index)
        count = seen.get(base, 0)
        header = base
        if count:
            header = f"{base}_{count + 1}"
        seen[base] = count + 1
        deduped.append(header)
    return deduped


def _csv_parse(text: Optional[str], has_header: int = 1) -> Optional[str]:
    """Parse CSV text into JSON array using Python's robust csv module.

    Usage:
      csv_parse(text)           -- assumes first row is header, returns [{col: val}, ...]
      csv_parse(text, 1)        -- same as above (has_header=1)
      csv_parse(text, 0)        -- no header, returns [[val1, val2, ...], ...]

    With header (has_header=1): Returns JSON array of objects
      Input: "name,age\\nAlice,30\\nBob,25"
      Output: [{"name":"Alice","age":"30"},{"name":"Bob","age":"25"}]

    Without header (has_header=0): Returns JSON array of arrays
      Input: "Alice,30\\nBob,25"
      Output: [["Alice","30"],["Bob","25"]]

    Handles: quoted fields, embedded commas, embedded newlines, escaped quotes.
    Also handles: BOM, Excel sep= prefix, delimiter detection (comma/tab/semicolon/pipe/caret).
    Safe: Pure computation, no I/O. Limited to 10000 rows, 100 columns.
    """
    import json as json_module

    if text is None:
        return None

    has_header = _coerce_csv_header_flag(has_header)
    text, explicit_delimiter = normalize_csv_text(text)
    if not text.strip():
        return "[]"

    # Normalize line endings
    text = text.replace('\r\n', '\n').replace('\r', '\n')

    try:
        max_field_size = max(1024, min(len(text), 5_000_000))
        try:
            csv.field_size_limit(max(csv.field_size_limit(), max_field_size))
        except Exception:
            pass

        sample_text, sample_lines = build_csv_sample(text)
        dialect = detect_csv_dialect(
            sample_text,
            sample_lines,
            explicit_delimiter=explicit_delimiter,
        )
        max_rows = 10000
        max_cols = 100
        row_limit = max_rows + (1 if has_header else 0) + 10
        raw_rows = read_csv_rows(text, dialect, max_rows=row_limit)
        rows = []
        headers = None
        row_index = 0
        for row in raw_rows:
            if row_index >= max_rows + (1 if has_header else 0):
                break
            row = row[:max_cols]
            if not row or not any(cell.strip() for cell in row):
                continue

            if has_header and headers is None:
                headers = _dedupe_headers(row)
                row_index += 1
                continue

            if has_header and headers:
                if len(row) > len(headers):
                    extra = min(len(row), max_cols) - len(headers)
                    if extra > 0:
                        start = len(headers)
                        new_headers = [f"col_{idx}" for idx in range(start, start + extra)]
                        headers.extend(new_headers)
                        for existing in rows:
                            for new_header in new_headers:
                                existing[new_header] = ""
                while len(row) < len(headers):
                    row.append("")
                rows.append(dict(zip(headers, row)))
            else:
                rows.append(row)
            row_index += 1

        return json_module.dumps(rows) if rows else "[]"
    except Exception:
        return None


def _csv_column(text: Optional[str], column: int, has_header: int = 1) -> Optional[str]:
    """Extract a single column from CSV as JSON array.

    Usage:
      csv_column(text, 0)       -- first column (0-indexed), skip header
      csv_column(text, 2)       -- third column
      csv_column(text, 0, 0)    -- first column, no header row

    Returns: JSON array of values, e.g., ["Alice","Bob","Carol"]
    Useful for: Getting all values in a column for aggregation or filtering.
    """
    import json as json_module

    if text is None or column < 0:
        return None

    has_header = _coerce_csv_header_flag(has_header)
    text, explicit_delimiter = normalize_csv_text(text)
    if not text.strip():
        return "[]"

    text = text.replace('\r\n', '\n').replace('\r', '\n')

    try:
        max_field_size = max(1024, min(len(text), 5_000_000))
        try:
            csv.field_size_limit(max(csv.field_size_limit(), max_field_size))
        except Exception:
            pass

        sample_text, sample_lines = build_csv_sample(text)
        dialect = detect_csv_dialect(
            sample_text,
            sample_lines,
            explicit_delimiter=explicit_delimiter,
        )
        values = []
        max_rows = 10000
        row_limit = max_rows + (1 if has_header else 0) + 10
        raw_rows = read_csv_rows(text, dialect, max_rows=row_limit)
        row_index = 0

        for row in raw_rows:
            if len(values) >= max_rows:
                break
            if not row or not any(cell.strip() for cell in row):
                continue
            if has_header and row_index == 0:
                row_index += 1
                continue
            if column < len(row):
                values.append(row[column])
            row_index += 1

        return json_module.dumps(values) if values else "[]"
    except Exception:
        return None


def _csv_headers(text: Optional[str]) -> Optional[str]:
    """Extract column names from CSV header row as JSON array.

    Usage:
      csv_headers(text)  -- returns ["col1", "col2", ...]

    Returns: JSON array of column names from the first row.
    Useful for: Discovering column names before writing extraction queries.

    Example:
      SELECT csv_headers(result_text) FROM __tool_results WHERE result_id='abc123'
      → ["SepalLength","SepalWidth","PetalLength","PetalWidth","Name"]

    Then use these exact names in your extraction:
      SELECT r.value->>'$.SepalLength' FROM ... json_each(csv_parse(...)) r
    """
    import json as json_module

    if text is None:
        return None

    text, explicit_delimiter = normalize_csv_text(text)
    if not text.strip():
        return "[]"

    text = text.replace('\r\n', '\n').replace('\r', '\n')

    try:
        max_field_size = max(1024, min(len(text), 5_000_000))
        try:
            csv.field_size_limit(max(csv.field_size_limit(), max_field_size))
        except Exception:
            pass

        sample_text, sample_lines = build_csv_sample(text)
        dialect = detect_csv_dialect(
            sample_text,
            sample_lines,
            explicit_delimiter=explicit_delimiter,
        )
        raw_rows = read_csv_rows(text, dialect, max_rows=2)

        for row in raw_rows:
            if row and any(cell.strip() for cell in row):
                headers = _dedupe_headers(row)
                return json_module.dumps(headers)

        return "[]"
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Real-world data cleaning functions
# ---------------------------------------------------------------------------

def _html_to_text(html: Optional[str]) -> Optional[str]:
    """Strip HTML tags and decode entities to get plain text.

    Usage: html_to_text('<p>Hello &amp; <b>world</b></p>') → 'Hello & world'

    Handles: tags, entities (&amp; &#39; etc), scripts/styles removal.
    Perfect for: Scraped web content that has HTML mixed in.
    """
    import html as html_module

    if html is None:
        return None

    text = html
    # Remove script and style contents entirely
    text = re.sub(r'<script[^>]*>.*?</script>', ' ', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style[^>]*>.*?</style>', ' ', text, flags=re.DOTALL | re.IGNORECASE)
    # Replace block elements with newlines
    text = re.sub(r'<(?:p|div|br|hr|li|tr|h[1-6])[^>]*>', '\n', text, flags=re.IGNORECASE)
    # Remove all other tags
    text = re.sub(r'<[^>]+>', ' ', text)
    # Decode HTML entities
    text = html_module.unescape(text)
    # Normalize whitespace
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n[ \t]+', '\n', text)
    text = re.sub(r'[ \t]+\n', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _clean_text(text: Optional[str]) -> Optional[str]:
    """Normalize messy text: whitespace, unicode, common artifacts.

    Usage: clean_text('  Hello   world\\n\\n\\nFoo  ') → 'Hello world\\n\\nFoo'

    Handles: excessive whitespace, unicode normalization, zero-width chars,
             smart quotes → straight quotes, common mojibake patterns.
    """
    import unicodedata

    if text is None:
        return None

    # Unicode normalize (NFC)
    text = unicodedata.normalize('NFC', text)
    # Remove zero-width characters
    text = re.sub(r'[\u200b\u200c\u200d\ufeff]', '', text)
    # Smart quotes to straight quotes
    text = text.replace('"', '"').replace('"', '"').replace(''', "'").replace(''', "'")
    # Normalize dashes
    text = text.replace('–', '-').replace('—', '-')
    # Normalize ellipsis
    text = text.replace('…', '...')
    # Normalize whitespace (preserve intentional newlines)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' ?\n ?', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _parse_number(text: Optional[str]) -> Optional[float]:
    """Parse numbers from messy real-world formats.

    Usage:
      parse_number('$1,234.56')     → 1234.56
      parse_number('€1.234,56')     → 1234.56  (European)
      parse_number('1.2M')          → 1200000.0
      parse_number('1.5B')          → 1500000000.0
      parse_number('1,200')         → 1200.0
      parse_number('-$50.00')       → -50.0
      parse_number('(100)')         → -100.0  (accounting negative)

    Returns: Float or NULL if unparseable.
    """
    if text is None:
        return None

    text = text.strip()
    if not text:
        return None

    # Check for accounting-style negatives: (100)
    is_negative = False
    if text.startswith('(') and text.endswith(')'):
        is_negative = True
        text = text[1:-1]

    # Check for leading minus or negative
    if text.startswith('-'):
        is_negative = True
        text = text[1:]

    # Strip currency symbols and whitespace
    text = re.sub(r'^[£$€¥₹₽\s]+', '', text)
    text = re.sub(r'[£$€¥₹₽\s]+$', '', text)

    # Handle multiplier suffixes
    multiplier = 1.0
    suffix_match = re.search(r'([KkMmBbTt])\s*$', text)
    if suffix_match:
        suffix = suffix_match.group(1).upper()
        multipliers = {'K': 1e3, 'M': 1e6, 'B': 1e9, 'T': 1e12}
        multiplier = multipliers.get(suffix, 1.0)
        text = text[:suffix_match.start()]

    # Detect European vs US format
    # European: 1.234,56 (dot for thousands, comma for decimal)
    # US: 1,234.56 (comma for thousands, dot for decimal)
    text = text.replace(' ', '')  # Remove thousand separators (space)

    comma_pos = text.rfind(',')
    dot_pos = text.rfind('.')
    chars_after_comma = len(text) - comma_pos - 1 if comma_pos >= 0 else 0

    # European format detection:
    # 1) Comma after dot with 1-2 digits: "1.234,56"
    # 2) Comma with exactly 2 digits at end, no dot: "899,00" (price format)
    is_european = comma_pos >= 0 and chars_after_comma <= 2 and (
        dot_pos >= 0 and comma_pos > dot_pos or  # Has dot before comma
        (dot_pos < 0 and chars_after_comma == 2)  # No dot, exactly 2 decimal places
    )

    if is_european:
        # 1.234,56 → 1234.56 or 899,00 → 899.00
        text = text.replace('.', '').replace(',', '.')
    else:
        # US format or no decimal: just remove commas
        text = text.replace(',', '')

    try:
        result = float(text) * multiplier
        return -result if is_negative else result
    except (ValueError, TypeError):
        return None


def _parse_date(text: Optional[str], output_format: str = '%Y-%m-%d') -> Optional[str]:
    """Parse dates from various formats into standardized output.

    Usage:
      parse_date('Jan 5, 2024')      → '2024-01-05'
      parse_date('5/1/24')           → '2024-01-05'  (US format assumed)
      parse_date('2024-01-05')       → '2024-01-05'
      parse_date('January 5th 2024') → '2024-01-05'
      parse_date('5 Jan 2024')       → '2024-01-05'

    Second arg changes output format:
      parse_date('Jan 5, 2024', '%Y-%m-%d %H:%M:%S') → '2024-01-05 00:00:00'

    Returns: Formatted date string or NULL if unparseable.
    """
    from datetime import datetime

    if text is None:
        return None

    text = text.strip()
    if not text:
        return None

    # Remove ordinal suffixes (1st, 2nd, 3rd, 4th, etc.)
    text = re.sub(r'(\d+)(st|nd|rd|th)\b', r'\1', text, flags=re.IGNORECASE)

    # Common formats to try (order matters - more specific first)
    formats = [
        '%Y-%m-%d %H:%M:%S',  # ISO with time
        '%Y-%m-%dT%H:%M:%S',  # ISO T separator
        '%Y-%m-%dT%H:%M:%SZ', # ISO with Z
        '%Y-%m-%d',           # ISO date
        '%Y/%m/%d',           # ISO with slashes
        '%d/%m/%Y %H:%M:%S',  # European with time
        '%d/%m/%Y',           # European
        '%m/%d/%Y %H:%M:%S',  # US with time
        '%m/%d/%Y',           # US
        '%m/%d/%y',           # US short year
        '%d-%m-%Y',           # European with dashes
        '%B %d, %Y',          # January 5, 2024
        '%b %d, %Y',          # Jan 5, 2024
        '%d %B %Y',           # 5 January 2024
        '%d %b %Y',           # 5 Jan 2024
        '%B %d %Y',           # January 5 2024
        '%b %d %Y',           # Jan 5 2024
        '%d %B, %Y',          # 5 January, 2024
        '%d %b, %Y',          # 5 Jan, 2024
        '%Y%m%d',             # 20240105
    ]

    for fmt in formats:
        try:
            dt = datetime.strptime(text, fmt)
            return dt.strftime(output_format)
        except ValueError:
            continue

    return None


def _url_extract(url: Optional[str], part: str = 'domain') -> Optional[str]:
    """Extract parts from a URL.

    Usage:
      url_extract('https://sub.example.com/path?q=1', 'domain')  → 'example.com'
      url_extract('https://sub.example.com/path?q=1', 'host')    → 'sub.example.com'
      url_extract('https://sub.example.com/path?q=1', 'path')    → '/path'
      url_extract('https://sub.example.com/path?q=1', 'query')   → 'q=1'
      url_extract('https://sub.example.com/path?q=1', 'scheme')  → 'https'

    Parts: domain, host, path, query, scheme, port
    Returns: Extracted part or NULL.
    """
    from urllib.parse import urlparse

    if url is None:
        return None

    try:
        parsed = urlparse(url)
    except Exception:
        return None

    part = part.lower()

    if part == 'scheme':
        return parsed.scheme or None
    elif part == 'host':
        return parsed.netloc.split(':')[0] or None
    elif part == 'domain':
        # Extract registrable domain (strip subdomains)
        host = parsed.netloc.split(':')[0]
        if not host:
            return None
        parts = host.split('.')
        # Handle common TLDs
        if len(parts) >= 2:
            # Check for two-part TLDs like co.uk, com.au
            two_part_tlds = {'co.uk', 'com.au', 'co.nz', 'co.jp', 'com.br', 'co.in'}
            if len(parts) >= 3 and f'{parts[-2]}.{parts[-1]}' in two_part_tlds:
                return '.'.join(parts[-3:])
            return '.'.join(parts[-2:])
        return host
    elif part == 'path':
        return parsed.path or None
    elif part == 'query':
        return parsed.query or None
    elif part == 'port':
        if ':' in parsed.netloc:
            return parsed.netloc.split(':')[1]
        return None

    return None


def _extract_json(text: Optional[str]) -> Optional[str]:
    """Extract and validate JSON from text that may have surrounding content.

    Usage:
      extract_json('Result: {"a": 1} end')  → '{"a": 1}'
      extract_json('Data: [1, 2, 3]!')      → '[1, 2, 3]'

    Finds the first valid JSON object or array in the text.
    Returns: Valid JSON string or NULL if none found.
    """
    import json as json_module

    if text is None:
        return None

    # Try to find JSON object or array
    for start_char, end_char in [('{', '}'), ('[', ']')]:
        start = text.find(start_char)
        if start == -1:
            continue

        # Find matching end by counting braces
        depth = 0
        in_string = False
        escape = False

        for i in range(start, len(text)):
            char = text[i]

            if escape:
                escape = False
                continue

            if char == '\\' and in_string:
                escape = True
                continue

            if char == '"' and not escape:
                in_string = not in_string
                continue

            if in_string:
                continue

            if char == start_char:
                depth += 1
            elif char == end_char:
                depth -= 1
                if depth == 0:
                    candidate = text[start:i+1]
                    try:
                        json_module.loads(candidate)
                        return candidate
                    except json_module.JSONDecodeError:
                        break

    return None


def _extract_emails(text: Optional[str]) -> Optional[str]:
    """Extract all email addresses from text as JSON array.

    Usage: extract_emails('Contact us at foo@bar.com or support@example.org')
           → '["foo@bar.com", "support@example.org"]'

    Returns: JSON array of unique emails, or NULL if none found.
    """
    import json as json_module

    if text is None:
        return None

    # RFC 5322 inspired but practical pattern
    pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
    emails = re.findall(pattern, text)

    if not emails:
        return None

    # Dedupe while preserving order
    seen = set()
    unique = []
    for email in emails:
        lower = email.lower()
        if lower not in seen:
            seen.add(lower)
            unique.append(email)

    return json_module.dumps(unique)


def _extract_urls(text: Optional[str]) -> Optional[str]:
    """Extract all URLs from text as JSON array.

    Usage: extract_urls('Visit https://example.com or http://foo.bar/path')
           → '["https://example.com", "http://foo.bar/path"]'

    Returns: JSON array of unique URLs, or NULL if none found.
    """
    import json as json_module

    if text is None:
        return None

    # Match http/https URLs
    pattern = r'https?://[^\s<>"\')\]}>]+'
    urls = re.findall(pattern, text)

    if not urls:
        return None

    # Clean trailing punctuation that's likely not part of URL
    cleaned = []
    for url in urls:
        # Strip trailing punctuation that's probably sentence-ending
        url = re.sub(r'[.,;:!?]+$', '', url)
        cleaned.append(url)

    # Dedupe while preserving order
    seen = set()
    unique = []
    for url in cleaned:
        if url not in seen:
            seen.add(url)
            unique.append(url)

    return json_module.dumps(unique)


# ---------------------------------------------------------------------------
# Common LLM hallucinations - aliases for functions from other databases
# ---------------------------------------------------------------------------

def _now() -> str:
    """NOW() - MySQL/PostgreSQL style, returns current datetime."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _curdate() -> str:
    """CURDATE() - MySQL style, returns current date."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _len(s: Optional[str]) -> Optional[int]:
    """LEN(s) - SQL Server style alias for LENGTH()."""
    return len(s) if s is not None else None


def _nvl(val: Optional[str], default: str) -> str:
    """NVL(val, default) - Oracle style alias for IFNULL()."""
    return val if val is not None else default


def _left(s: Optional[str], n: int) -> Optional[str]:
    """LEFT(s, n) - SQL Server/MySQL style, returns leftmost n chars."""
    if s is None:
        return None
    return s[:n]


def _right(s: Optional[str], n: int) -> Optional[str]:
    """RIGHT(s, n) - SQL Server/MySQL style, returns rightmost n chars."""
    if s is None:
        return None
    return s[-n:] if n > 0 else ""


def _reverse(s: Optional[str]) -> Optional[str]:
    """REVERSE(s) - reverses string."""
    return s[::-1] if s is not None else None


def _lpad(s: Optional[str], length: int, pad: str = " ") -> Optional[str]:
    """LPAD(s, length, pad) - left-pad string to length."""
    if s is None:
        return None
    if len(pad) == 0:
        return s
    return (pad * ((length - len(s)) // len(pad) + 1) + s)[-length:] if len(s) < length else s


def _rpad(s: Optional[str], length: int, pad: str = " ") -> Optional[str]:
    """RPAD(s, length, pad) - right-pad string to length."""
    if s is None:
        return None
    if len(pad) == 0:
        return s
    return (s + pad * ((length - len(s)) // len(pad) + 1))[:length] if len(s) < length else s


def _split_part(s: Optional[str], delimiter: str, part: int) -> Optional[str]:
    """SPLIT_PART(s, delimiter, part) - PostgreSQL style, 1-indexed."""
    if s is None:
        return None
    parts = s.split(delimiter)
    if part < 1 or part > len(parts):
        return ""
    return parts[part - 1]


class _CorrAggregate:
    """Aggregate for Pearson correlation (CORR)."""

    def __init__(self) -> None:
        self.count = 0
        self.mean_x = 0.0
        self.mean_y = 0.0
        self.cov = 0.0
        self.m2_x = 0.0
        self.m2_y = 0.0

    def step(self, x: Optional[float], y: Optional[float]) -> None:
        if x is None or y is None:
            return
        try:
            x_val = float(x)
            y_val = float(y)
        except (TypeError, ValueError):
            return
        if not math.isfinite(x_val) or not math.isfinite(y_val):
            return
        self.count += 1
        dx = x_val - self.mean_x
        self.mean_x += dx / self.count
        dy = y_val - self.mean_y
        self.mean_y += dy / self.count
        self.cov += dx * (y_val - self.mean_y)
        self.m2_x += dx * (x_val - self.mean_x)
        self.m2_y += dy * (y_val - self.mean_y)

    def finalize(self) -> Optional[float]:
        if self.count < 2:
            return None
        denom = self.m2_x * self.m2_y
        if denom <= 0.0:
            return None
        return self.cov / math.sqrt(denom)


class _StddevSampAggregate:
    """Aggregate for sample standard deviation (STDDEV/STDDEV_SAMP).

    Uses Welford's online algorithm for numerical stability.
    Returns sqrt(sum((x - mean)^2) / (N-1)) - sample std dev.
    """

    def __init__(self) -> None:
        self.count = 0
        self.mean = 0.0
        self.m2 = 0.0  # Sum of squared differences from mean

    def step(self, x: Optional[float]) -> None:
        if x is None:
            return
        try:
            x_val = float(x)
        except (TypeError, ValueError):
            return
        if not math.isfinite(x_val):
            return
        self.count += 1
        delta = x_val - self.mean
        self.mean += delta / self.count
        delta2 = x_val - self.mean
        self.m2 += delta * delta2

    def finalize(self) -> Optional[float]:
        if self.count < 2:
            return None
        variance = self.m2 / (self.count - 1)
        return math.sqrt(variance)


class _StddevPopAggregate:
    """Aggregate for population standard deviation (STDDEV_POP).

    Returns sqrt(sum((x - mean)^2) / N) - population std dev.
    """

    def __init__(self) -> None:
        self.count = 0
        self.mean = 0.0
        self.m2 = 0.0

    def step(self, x: Optional[float]) -> None:
        if x is None:
            return
        try:
            x_val = float(x)
        except (TypeError, ValueError):
            return
        if not math.isfinite(x_val):
            return
        self.count += 1
        delta = x_val - self.mean
        self.mean += delta / self.count
        delta2 = x_val - self.mean
        self.m2 += delta * delta2

    def finalize(self) -> Optional[float]:
        if self.count < 1:
            return None
        variance = self.m2 / self.count
        return math.sqrt(variance)


class _VarianceSampAggregate:
    """Aggregate for sample variance (VARIANCE/VAR_SAMP).

    Returns sum((x - mean)^2) / (N-1).
    """

    def __init__(self) -> None:
        self.count = 0
        self.mean = 0.0
        self.m2 = 0.0

    def step(self, x: Optional[float]) -> None:
        if x is None:
            return
        try:
            x_val = float(x)
        except (TypeError, ValueError):
            return
        if not math.isfinite(x_val):
            return
        self.count += 1
        delta = x_val - self.mean
        self.mean += delta / self.count
        delta2 = x_val - self.mean
        self.m2 += delta * delta2

    def finalize(self) -> Optional[float]:
        if self.count < 2:
            return None
        return self.m2 / (self.count - 1)


class _VariancePopAggregate:
    """Aggregate for population variance (VAR_POP).

    Returns sum((x - mean)^2) / N.
    """

    def __init__(self) -> None:
        self.count = 0
        self.mean = 0.0
        self.m2 = 0.0

    def step(self, x: Optional[float]) -> None:
        if x is None:
            return
        try:
            x_val = float(x)
        except (TypeError, ValueError):
            return
        if not math.isfinite(x_val):
            return
        self.count += 1
        delta = x_val - self.mean
        self.mean += delta / self.count
        delta2 = x_val - self.mean
        self.m2 += delta * delta2

    def finalize(self) -> Optional[float]:
        if self.count < 1:
            return None
        return self.m2 / self.count

_BLOCKED_ACTIONS = {
    sqlite3.SQLITE_ATTACH,
    sqlite3.SQLITE_DETACH,
}

_BLOCKED_FUNCTIONS = {
    "load_extension",
    "readfile",
    "writefile",
    "edit",
    "fts3_tokenizer",
}

_BLOCKED_PRAGMAS = {
    "database_list",
    "key",
    "rekey",
    "temp_store",
    "temp_store_directory",
}

_VACUUM_PATTERN = re.compile(
    r"^\s*(?:EXPLAIN\s+(?:QUERY\s+PLAN\s+)?)?VACUUM\b",
    re.IGNORECASE,
)


def _deny_action(action_code: int, param1: Optional[str], param2: Optional[str]) -> int:
    action_name = str(action_code)
    logger.warning(
        "Blocked SQLite action=%s param1=%s param2=%s",
        action_name,
        param1,
        param2,
    )
    return sqlite3.SQLITE_DENY


def _sqlite_authorizer(
    action_code: int,
    param1: Optional[str],
    param2: Optional[str],
    _db_name: Optional[str],
    _trigger_name: Optional[str],
) -> int:
    if action_code in _BLOCKED_ACTIONS:
        return _deny_action(action_code, param1, param2)

    if action_code == sqlite3.SQLITE_FUNCTION:
        func = (param2 or param1 or "").lower()
        if func in _BLOCKED_FUNCTIONS:
            return _deny_action(action_code, param1, param2)

    if action_code == sqlite3.SQLITE_PRAGMA:
        pragma = (param1 or "").lower()
        if pragma in _BLOCKED_PRAGMAS:
            return _deny_action(action_code, param1, param2)

    return sqlite3.SQLITE_OK


def _strip_comments_and_literals(sql: str) -> str:
    """Remove comments and quoted literals for safer keyword checks."""
    result: list[str] = []
    i = 0
    length = len(sql)

    while i < length:
        ch = sql[i]

        if ch == "-" and i + 1 < length and sql[i + 1] == "-":
            i += 2
            while i < length and sql[i] != "\n":
                i += 1
            continue

        if ch == "/" and i + 1 < length and sql[i + 1] == "*":
            i += 2
            while i + 1 < length and not (sql[i] == "*" and sql[i + 1] == "/"):
                i += 1
            i = i + 2 if i + 1 < length else length
            continue

        if ch in {"'", '"'}:
            quote = ch
            result.append(" ")
            i += 1
            while i < length:
                curr = sql[i]
                if curr == quote:
                    if i + 1 < length and sql[i + 1] == quote:
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue

        result.append(ch)
        i += 1

    return "".join(result)


def get_blocked_statement_reason(sql: str) -> Optional[str]:
    """Return a message if the statement should be blocked."""
    stripped = _strip_comments_and_literals(sql or "")
    if _VACUUM_PATTERN.match(stripped):
        return "VACUUM statements are disabled for safety."
    return None


_QUERY_STARTS: dict[int, float] = {}
_QUERY_TIMEOUTS: dict[int, float] = {}


def start_query_timer(conn: sqlite3.Connection) -> None:
    _QUERY_STARTS[id(conn)] = time.monotonic()


def stop_query_timer(conn: sqlite3.Connection) -> None:
    _QUERY_STARTS.pop(id(conn), None)


def clear_guarded_connection(conn: sqlite3.Connection) -> None:
    conn_id = id(conn)
    _QUERY_STARTS.pop(conn_id, None)
    _QUERY_TIMEOUTS.pop(conn_id, None)


def _make_progress_handler(conn_id: int):

    def handler() -> int:
        start = _QUERY_STARTS.get(conn_id)
        timeout = _QUERY_TIMEOUTS.get(conn_id)
        if start is None or timeout is None:
            return 0
        if time.monotonic() - start > timeout:
            return 1
        return 0

    return handler


def _register_safe_functions(conn: sqlite3.Connection) -> None:
    """Register safe custom functions for text analysis."""
    conn.create_function("REGEXP", 2, _regexp)
    conn.create_function("regexp_extract", 2, _regexp_extract)
    conn.create_function("regexp_extract", 3, _regexp_extract)  # With group arg
    conn.create_function("regexp_find_all", 2, _regexp_find_all)
    conn.create_function("regexp_find_all", 3, _regexp_find_all)  # With separator
    conn.create_function("grep_context", 2, _grep_context)
    conn.create_function("grep_context", 3, _grep_context)  # With context_chars
    conn.create_function("grep_context_all", 2, _grep_context_all)
    conn.create_function("grep_context_all", 3, _grep_context_all)
    conn.create_function("grep_context_all", 4, _grep_context_all)  # With max_matches
    conn.create_function("split_sections", 1, _split_sections)
    conn.create_function("split_sections", 2, _split_sections)  # With delimiter
    conn.create_function("substr_range", 3, _substr_range)
    conn.create_function("word_count", 1, _word_count)
    conn.create_function("char_count", 1, _char_count)
    conn.create_function("json_length", 1, _json_length)  # Alias for json_array_length
    # CSV parsing (uses Python's csv module for robustness)
    conn.create_function("csv_parse", 1, _csv_parse)  # With header
    conn.create_function("csv_parse", 2, _csv_parse)  # With has_header arg
    conn.create_function("csv_column", 2, _csv_column)  # Extract column, with header
    conn.create_function("csv_column", 3, _csv_column)  # With has_header arg
    conn.create_function("csv_headers", 1, _csv_headers)  # Get column names
    # Real-world data cleaning
    conn.create_function("html_to_text", 1, _html_to_text)
    conn.create_function("clean_text", 1, _clean_text)
    conn.create_function("parse_number", 1, _parse_number)
    conn.create_function("parse_date", 1, _parse_date)
    conn.create_function("parse_date", 2, _parse_date)  # With output format
    conn.create_function("url_extract", 1, _url_extract)  # Default: domain
    conn.create_function("url_extract", 2, _url_extract)  # With part arg
    conn.create_function("extract_json", 1, _extract_json)
    conn.create_function("extract_emails", 1, _extract_emails)
    conn.create_function("extract_urls", 1, _extract_urls)
    # Common LLM hallucinations from other databases
    conn.create_function("NOW", 0, _now)  # MySQL/PostgreSQL
    conn.create_function("CURDATE", 0, _curdate)  # MySQL
    conn.create_function("GETDATE", 0, _now)  # SQL Server
    conn.create_function("LEN", 1, _len)  # SQL Server (alias for LENGTH)
    conn.create_function("NVL", 2, _nvl)  # Oracle (alias for IFNULL)
    conn.create_function("LEFT", 2, _left)  # SQL Server/MySQL
    conn.create_function("RIGHT", 2, _right)  # SQL Server/MySQL
    conn.create_function("REVERSE", 1, _reverse)  # Common across DBs
    conn.create_function("LPAD", 2, _lpad)  # Oracle/MySQL
    conn.create_function("LPAD", 3, _lpad)  # With pad char
    conn.create_function("RPAD", 2, _rpad)  # Oracle/MySQL
    conn.create_function("RPAD", 3, _rpad)  # With pad char
    conn.create_function("SPLIT_PART", 3, _split_part)  # PostgreSQL
    conn.create_aggregate("CORR", 2, _CorrAggregate)  # PostgreSQL
    # Statistical aggregates (common across MySQL, PostgreSQL, SQL Server)
    conn.create_aggregate("STDDEV", 1, _StddevSampAggregate)  # Sample std dev (default)
    conn.create_aggregate("STDEV", 1, _StddevSampAggregate)  # Alias
    conn.create_aggregate("STDDEV_SAMP", 1, _StddevSampAggregate)  # Explicit sample
    conn.create_aggregate("STDDEV_POP", 1, _StddevPopAggregate)  # Population std dev
    conn.create_aggregate("VARIANCE", 1, _VarianceSampAggregate)  # Sample variance
    conn.create_aggregate("VAR_SAMP", 1, _VarianceSampAggregate)  # Explicit sample
    conn.create_aggregate("VAR_POP", 1, _VariancePopAggregate)  # Population variance


def open_guarded_sqlite_connection(
    db_path: str,
    *,
    timeout_seconds: float = 30.0,
    allow_attach: bool = False,
) -> sqlite3.Connection:
    """Open a SQLite connection with guardrails against host file access.

    allow_attach should only be used for internal maintenance where VACUUM is required.
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA temp_store = MEMORY;")
    except Exception:
        logger.debug("Failed to set SQLite temp_store=MEMORY", exc_info=True)
    try:
        conn.enable_load_extension(False)
    except Exception:
        logger.debug("Failed to disable SQLite load_extension", exc_info=True)
    # Register safe analysis functions
    _register_safe_functions(conn)
    if hasattr(conn, "setlimit") and hasattr(sqlite3, "SQLITE_LIMIT_ATTACHED"):
        try:
            if not allow_attach:
                conn.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
        except Exception:
            logger.debug("Failed to set SQLite attached DB limit", exc_info=True)
    def authorizer(
        action_code: int,
        param1: Optional[str],
        param2: Optional[str],
        db_name: Optional[str],
        trigger_name: Optional[str],
    ) -> int:
        if allow_attach and action_code in {sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH}:
            return sqlite3.SQLITE_OK
        return _sqlite_authorizer(action_code, param1, param2, db_name, trigger_name)

    try:
        conn.set_authorizer(authorizer)
    except Exception as exc:
        conn.close()
        raise RuntimeError("Failed to enable SQLite guardrails") from exc
    conn_id = id(conn)
    _QUERY_TIMEOUTS[conn_id] = timeout_seconds
    conn.set_progress_handler(_make_progress_handler(conn_id), 10000)
    return conn
