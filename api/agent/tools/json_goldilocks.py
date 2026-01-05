import base64
import html as html_lib
import json
import math
import re
from collections import Counter, OrderedDict
from functools import lru_cache
from html.parser import HTMLParser
from typing import Any, Optional
from urllib.parse import unquote

from .text_focus import barbell_focus


class GoldilocksConfig:
    """Configurable limits for focused JSON extraction."""

    MAX_ITEMS = 10
    MAX_STR_LEN = 300
    MAX_TOTAL_CHARS = 8000
    MAX_DEPTH = 8

    HEAD_WEIGHT = 0.60
    TAIL_WEIGHT = 0.25

    MIN_JSON_DETECT_LEN = 10
    MIN_HTML_TAGS_FOR_DETECTION = 2
    MIN_MARKDOWN_INDICATORS = 2
    ENTROPY_THRESHOLD_FOR_NOISE = 4.5

    BARBELL_TRIM_RATIO = 0.15
    MAX_SCHEMA_INFERENCE_ITEMS = 20
    MAX_BASE64_DECODE_BYTES = 20000


class Patterns:
    """Compiled regex patterns for content detection and redaction."""

    UUID = re.compile(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    )
    MONGO_ID = re.compile(r"^[0-9a-fA-F]{24}$")
    CUID = re.compile(r"^c[a-z0-9]{24}$")
    ULID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
    SNOWFLAKE = re.compile(r"^\d{17,19}$")

    AWS_KEY = re.compile(r"AKIA[0-9A-Z]{16}")
    GITHUB_TOKEN = re.compile(r"gh[pousr]_[A-Za-z0-9_]{36,}")
    SLACK_TOKEN = re.compile(r"xox[baprs]-[0-9A-Za-z-]+")
    STRIPE_KEY = re.compile(r"sk_(live|test)_[0-9a-zA-Z]{24,}")
    PRIVATE_KEY_BLOCK = re.compile(r"-----BEGIN [A-Z ]+ PRIVATE KEY-----")

    URL = re.compile(
        r"https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+[/\w\-.~:/?#\[\]@!$&'()*+,;=%]*"
    )
    EMAIL = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")

    ISO_DATETIME = re.compile(
        r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?"
    )
    UNIX_TIMESTAMP = re.compile(r"^1[0-9]{9}$")
    UNIX_TIMESTAMP_MS = re.compile(r"^1[0-9]{12}$")

    STACK_TRACE_PYTHON = re.compile(r"Traceback \(most recent call last\):|File \".*\", line \d+")
    STACK_TRACE_JAVA = re.compile(r"at [a-zA-Z0-9$.]+\([A-Za-z0-9]+\.java:\d+\)")
    STACK_TRACE_JS = re.compile(r"at .+\(.+:\d+:\d+\)|at async .+")
    STACK_TRACE_GO = re.compile(r"goroutine \d+ \[.+\]:")

    SQL_KEYWORDS = re.compile(
        r"\b(SELECT|INSERT|UPDATE|DELETE|FROM|WHERE|JOIN|LEFT|RIGHT|INNER|OUTER|"
        r"GROUP BY|ORDER BY|HAVING|UNION|CREATE|ALTER|DROP|INDEX|TABLE|VIEW)\b",
        re.IGNORECASE,
    )

    HTML_TAG = re.compile(
        r"<(?:html|head|body|div|span|p|a|ul|ol|li|table|tr|td|th|form|input|"
        r"script|style|meta|link|img|br)[^>]*>",
        re.IGNORECASE,
    )
    XML_DECLARATION = re.compile(r"<\?xml[^>]+\?>")
    MARKDOWN_HEADER = re.compile(r"^#{1,6}\s+.+$", re.MULTILINE)
    MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
    MARKDOWN_CODE_BLOCK = re.compile(r"```[\s\S]*?```")
    MARKDOWN_LIST = re.compile(r"^\s*[-*+]\s+.+$", re.MULTILINE)

    BASE64 = re.compile(r"^[A-Za-z0-9+/]{20,}={0,2}$")
    BASE64_URL = re.compile(r"^[A-Za-z0-9_-]{20,}={0,2}$")
    JWT = re.compile(r"^eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")

    LOG_LEVEL = re.compile(r"\b(DEBUG|INFO|WARN(?:ING)?|ERROR|FATAL|TRACE|CRITICAL)\b", re.IGNORECASE)
    LOG_TIMESTAMP = re.compile(r"^\[?\d{4}[-/]\d{2}[-/]\d{2}[T ]\d{2}:\d{2}:\d{2}")

    REPEATED_CHARS = re.compile(r"(.)\1{20,}")
    NULL_BYTES = re.compile(r"\x00")


CONTAINER_KEYS = frozenset({
    "data", "results", "items", "records", "entries", "list", "values",
    "content", "body", "payload", "response", "objects", "elements",
    "collection", "entities", "resources",
    "hits", "nodes", "edges", "rows", "documents", "matches", "docs",
    "posts", "users", "messages", "events", "files", "comments",
    "products", "orders", "transactions", "articles", "issues",
    "commits", "branches", "pulls", "reviews", "notifications",
    "channels", "members", "teams", "projects", "tasks", "tickets",
    "contacts", "leads", "accounts", "opportunities", "campaigns",
    "instances", "services", "clusters", "pods",
    "metrics", "traces", "spans", "logs", "alerts", "incidents",
    "invoices", "payments", "subscriptions", "customers", "charges",
    "repositories", "gists", "organizations", "workflows", "runs",
    "deployments", "releases", "tags", "labels", "milestones",
    "conversations", "threads", "replies", "attachments",
    "activities", "feeds", "stories", "media", "assets",
    "searchresults", "search_results", "queryresults", "query_results",
})

PRIORITY_KEYS = frozenset({
    "id", "uuid", "uid", "guid", "_id", "key", "ref", "pk", "sk",
    "objectid", "object_id", "recordid", "record_id",
    "name", "title", "label", "display_name", "displayname", "full_name",
    "fullname", "username", "login", "handle", "slug", "alias", "nickname",
    "firstname", "first_name", "lastname", "last_name",
    "description", "summary", "excerpt", "abstract", "bio", "about",
    "overview", "synopsis", "brief",
    "status", "state", "phase", "condition", "health", "result",
    "success", "ok", "done", "completed", "active", "enabled", "valid",
    "outcome", "conclusion",
    "error", "errors", "message", "msg", "reason", "detail", "details",
    "code", "error_code", "errorcode", "status_code", "statuscode",
    "warnings", "warn", "info", "exception", "fault",
    "type", "kind", "category", "class", "group", "tag", "tags",
    "__typename", "_type", "resourcetype", "resource_type",
    "url", "href", "link", "uri", "src", "source", "target",
    "html_url", "web_url", "api_url", "download_url", "redirect_url",
    "permalink", "canonical_url", "self",
    "created_at", "updated_at", "timestamp", "date", "time",
    "created", "modified", "published", "started_at", "ended_at",
    "createdat", "updatedat", "publishedat", "deletedat",
    "expires", "expires_at", "expiresat",
    "total", "count", "size", "length", "limit", "offset",
    "page", "per_page", "page_size", "total_pages", "total_count",
    "next", "previous", "prev", "first", "last", "cursor",
    "has_more", "hasmore", "has_next", "has_previous", "hasnextpage",
    "nextcursor", "next_cursor", "nextpagetoken", "next_page_token",
    "parent", "parent_id", "owner", "owner_id", "author", "author_id",
    "user", "user_id", "account", "account_id", "org", "organization",
    "creator", "assignee", "reporter",
    "value", "amount", "price", "cost", "score", "rating", "rank",
    "percent", "percentage", "ratio", "rate", "priority", "weight",
    "meta", "metadata", "pagination", "page_info", "pageinfo",
    "version", "api_version", "schema", "format",
    "enabled", "disabled", "visible", "hidden", "public", "private",
    "verified", "approved", "blocked", "suspended", "archived",
})

BLOAT_KEYS = frozenset({
    "html", "body_html", "content_html", "rendered", "rendered_body",
    "raw", "raw_content", "raw_body", "source_code", "rawcontent",
    "html_content", "htmlcontent", "innerhtml", "outerhtml",
    "base64", "bytes", "binary", "blob", "data_uri", "encoded",
    "attachment", "attachments", "file_content", "file_data",
    "thumbnail_data", "image_data", "imagedata", "binarydata",
    "debug", "trace", "stack", "stacktrace", "stack_trace", "backtrace",
    "logs", "log", "changelog", "history", "audit", "audit_log",
    "debug_info", "debuginfo", "internaldebug",
    "headers", "request_headers", "response_headers", "cookies",
    "request", "request_body", "response_body", "rawrequest", "rawresponse",
    "internal", "private", "system", "cache", "cached",
    "_links", "_embedded", "_meta", "_internal", "_private",
    "__v", "__version", "_rev", "_etag",
    "css", "style", "styles", "theme", "formatting", "stylesheet",
    "avatar", "avatar_url", "icon", "thumbnail", "image_data",
    "gravatar", "profile_image", "banner", "cover",
    "signature", "checksum", "hash", "etag", "fingerprint", "digest",
    "permissions", "acl", "capabilities", "features", "flags", "scopes",
    "settings", "preferences", "config", "configuration",
    "serialized", "pickled", "marshalled", "encoded_data",
})

MESSY_CONTENT_KEYS = frozenset({
    "body", "content", "text", "message", "description", "summary",
    "html", "body_html", "content_html", "raw", "rendered",
    "markdown", "md", "readme", "notes", "comment", "reply",
    "bio", "about", "excerpt", "abstract", "article", "post",
    "email_body", "email_content", "template", "snippet",
    "log", "output", "stdout", "stderr", "trace", "stacktrace",
    "query", "sql", "code", "script", "config", "configuration",
    "payload", "request_body", "response_body", "data",
    "diff", "patch", "changelog",
})

JSON_STRING_KEYS = frozenset({
    "json", "data", "payload", "body", "content", "config",
    "configuration", "settings", "options", "params", "parameters",
    "attributes", "properties", "metadata", "meta", "extra",
    "context", "state", "snapshot", "serialized", "encoded",
    "request_body", "response_body", "message", "event_data",
    "custom_fields", "custom_data", "additional_info", "extras",
})


class MLStripper(HTMLParser):
    """Fast HTML tag stripper."""

    def __init__(self) -> None:
        super().__init__()
        self.strict = False
        self.convert_charrefs = True
        self.fed: list[str] = []

    def handle_data(self, d: str) -> None:
        self.fed.append(d)

    def get_data(self) -> str:
        return "".join(self.fed)


def strip_html(text: str) -> str:
    """Strip HTML tags and decode entities."""
    try:
        stripper = MLStripper()
        stripper.feed(text)
        result = stripper.get_data()
        result = html_lib.unescape(result)
        result = re.sub(r"\s+", " ", result).strip()
        return result
    except Exception:
        text = re.sub(r"<[^>]+>", " ", text)
        text = html_lib.unescape(text)
        return re.sub(r"\s+", " ", text).strip()


def calculate_entropy(text: str) -> float:
    """Calculate Shannon entropy of a string."""
    if not text:
        return 0.0
    freq = Counter(text)
    length = len(text)
    entropy = 0.0
    for count in freq.values():
        p = count / length
        entropy -= p * math.log2(p)
    return entropy


def is_likely_noise(text: str) -> bool:
    if len(text) < 50:
        return False

    entropy = calculate_entropy(text[:500])
    if entropy > GoldilocksConfig.ENTROPY_THRESHOLD_FOR_NOISE:
        if not (Patterns.JWT.match(text) or Patterns.BASE64.match(text[:100])):
            return True

    if Patterns.REPEATED_CHARS.search(text):
        return True
    if Patterns.NULL_BYTES.search(text):
        return True

    printable_ratio = sum(1 for c in text[:200] if c.isprintable()) / min(len(text), 200)
    return printable_ratio < 0.8


def detect_content_type(text: str) -> str:
    if not text or len(text) < 5:
        return "text"

    s_stripped = text.strip()

    if len(s_stripped) >= GoldilocksConfig.MIN_JSON_DETECT_LEN:
        if s_stripped.startswith("{") and s_stripped.endswith("}"):
            try:
                json.loads(s_stripped)
                return "json"
            except Exception:
                pass
        if s_stripped.startswith("[") and s_stripped.endswith("]"):
            try:
                json.loads(s_stripped)
                return "json"
            except Exception:
                pass

    if Patterns.JWT.match(s_stripped):
        return "jwt"

    if len(s_stripped) > 50 and Patterns.BASE64.match(s_stripped.replace("\n", "")):
        return "base64"

    if any([
        Patterns.STACK_TRACE_PYTHON.search(s_stripped[:500]),
        Patterns.STACK_TRACE_JAVA.search(s_stripped[:500]),
        Patterns.STACK_TRACE_JS.search(s_stripped[:500]),
        Patterns.STACK_TRACE_GO.search(s_stripped[:500]),
    ]):
        return "stack_trace"

    html_tag_count = len(Patterns.HTML_TAG.findall(s_stripped[:2000]))
    if html_tag_count >= GoldilocksConfig.MIN_HTML_TAGS_FOR_DETECTION:
        return "html"
    if Patterns.XML_DECLARATION.match(s_stripped):
        return "xml"
    if "</" in s_stripped and s_stripped.count("<") > 3:
        return "html"

    if s_stripped.startswith("<") and s_stripped.endswith(">") and "</" in s_stripped:
        return "xml"

    sql_keyword_count = len(Patterns.SQL_KEYWORDS.findall(s_stripped[:1000]))
    if sql_keyword_count >= 3:
        return "sql"

    if Patterns.LOG_TIMESTAMP.match(s_stripped):
        return "log"
    log_level_count = len(Patterns.LOG_LEVEL.findall(s_stripped[:1000]))
    if log_level_count >= 3:
        return "log"

    md_indicators = 0
    if Patterns.MARKDOWN_HEADER.search(s_stripped[:1000]):
        md_indicators += 1
    if Patterns.MARKDOWN_LINK.search(s_stripped[:1000]):
        md_indicators += 1
    if Patterns.MARKDOWN_CODE_BLOCK.search(s_stripped[:2000]):
        md_indicators += 1
    if Patterns.MARKDOWN_LIST.search(s_stripped[:1000]):
        md_indicators += 1
    if md_indicators >= GoldilocksConfig.MIN_MARKDOWN_INDICATORS:
        return "markdown"

    lines = s_stripped.split("\n")[:5]
    if len(lines) >= 2:
        for delim in [",", "\t", "|", ";"]:
            counts = [line.count(delim) for line in lines if line.strip()]
            if counts and counts[0] >= 2 and len(set(counts)) <= 2:
                return "csv"

    if "%" in s_stripped and ("=" in s_stripped or "&" in s_stripped):
        decoded = unquote(s_stripped)
        if decoded != s_stripped and len(decoded) < len(s_stripped) * 0.9:
            return "url_encoded"

    if is_likely_noise(s_stripped):
        return "noise"

    return "text"


def _looks_like_json(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < GoldilocksConfig.MIN_JSON_DETECT_LEN:
        return False
    if stripped.startswith("{") and stripped.endswith("}"):
        return True
    if stripped.startswith("[") and stripped.endswith("]"):
        return True
    return False


def detect_secret(key: str, value: str) -> Optional[str]:
    if not isinstance(value, str) or len(value) < 10:
        return None

    key_lower = key.lower()
    secret_key_patterns = [
        "password", "passwd", "pwd", "secret", "token", "apikey", "api_key",
        "access_key", "private_key", "auth", "credential", "bearer",
    ]
    if any(p in key_lower for p in secret_key_patterns):
        return "secret_by_key"

    if Patterns.AWS_KEY.search(value):
        return "aws_key"
    if Patterns.GITHUB_TOKEN.match(value):
        return "github_token"
    if Patterns.SLACK_TOKEN.match(value):
        return "slack_token"
    if Patterns.STRIPE_KEY.match(value):
        return "stripe_key"
    if Patterns.PRIVATE_KEY_BLOCK.search(value):
        return "private_key"

    return None


def process_html_content(text: str, max_len: int) -> str:
    stripped = strip_html(text)
    if len(stripped) <= max_len:
        return stripped
    return barbell_focus(stripped, target_bytes=max_len) or stripped[:max_len]


def process_json_string(text: str, max_len: int) -> Any:
    try:
        parsed = json.loads(text)
        processed = json_goldilocks(
            parsed,
            max_total_chars=max_len,
            max_str_len=max_len // 4,
        )
        return {"[EMBEDDED_JSON]": processed}
    except Exception:
        return barbell_focus(text, target_bytes=max_len) or text[:max_len]


def process_stack_trace(text: str, max_len: int) -> str:
    lines = text.strip().split("\n")
    if len(lines) <= 15 or len(text) <= max_len:
        return text if len(text) <= max_len else text[:max_len]

    head = lines[:7]
    tail = lines[-4:]
    omitted = len(lines) - len(head) - len(tail)
    summary = f"  ... ({omitted} frames omitted) ..."
    result = "\n".join(head + [summary] + tail)
    if len(result) <= max_len:
        return result

    context_lines = [lines[0]] + tail
    context = "\n".join(context_lines)
    combined = f"{context}\n{summary}"
    if len(combined) <= max_len:
        return combined

    available = max_len - len(summary) - 1
    if available <= 0:
        return summary[:max_len]
    trimmed = context[:available].rstrip()
    return f"{trimmed}\n{summary}"


def process_log_content(text: str, max_len: int) -> str:
    lines = text.strip().split("\n")
    if len(lines) <= 20 or len(text) <= max_len:
        return text if len(text) <= max_len else barbell_focus(text, target_bytes=max_len) or text[:max_len]

    error_lines = []
    other_lines = []
    for i, line in enumerate(lines):
        level_match = Patterns.LOG_LEVEL.search(line)
        if level_match:
            level = level_match.group(1).upper()
            if level in ("ERROR", "FATAL", "CRITICAL", "WARN", "WARNING"):
                error_lines.append((i, line))
            else:
                other_lines.append((i, line))
        else:
            other_lines.append((i, line))

    result_lines = [line for _, line in error_lines[:10]]
    for i, line in other_lines[:5]:
        if line not in result_lines:
            result_lines.append(f"[{i}] {line}")
    for i, line in other_lines[-3:]:
        if line not in result_lines:
            result_lines.append(f"[{i}] {line}")

    summary = f"[Total: {len(lines)} log lines, showing {len(result_lines)}]"
    result = "\n".join(result_lines)
    combined = f"{result}\n\n{summary}"
    if len(combined) > max_len:
        available = max_len - len(summary) - 2
        if available <= 0:
            return summary[:max_len]
        trimmed = result[:available].rstrip()
        return f"{trimmed}\n\n{summary}"
    return combined


def process_csv_content(text: str, max_len: int) -> str:
    lines = text.strip().split("\n")
    if len(lines) <= 10 or len(text) <= max_len:
        return text if len(text) <= max_len else text[:max_len] + "..."

    result_lines = lines[:6]
    result_lines.append(f"... ({len(lines) - 8} rows omitted) ...")
    result_lines.extend(lines[-2:])

    result = "\n".join(result_lines)
    if len(result) > max_len:
        return result[:max_len - 20] + "\n... (truncated)"
    return result


def process_sql_content(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text

    clauses = ["SELECT", "FROM", "WHERE", "JOIN", "GROUP BY", "ORDER BY", "LIMIT"]
    result = text[:max_len * 2 // 3]
    for clause in reversed(clauses):
        idx = result.upper().rfind(clause)
        if idx > max_len // 2:
            result = result[:idx + len(clause) + 50]
            break

    return result[:max_len - 20] + "\n... (query truncated)"


def process_jwt(text: str, max_len: int) -> str:
    try:
        parts = text.split(".")
        if len(parts) != 3:
            return text[:max_len]

        header_b64 = parts[0] + "=" * (-len(parts[0]) % 4)
        header = json.loads(base64.urlsafe_b64decode(header_b64))

        payload_summary = {}
        try:
            payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
            payload = json.loads(base64.urlsafe_b64decode(payload_b64))
            for key in ["iss", "aud", "exp", "iat", "nbf", "sub", "type", "scope"]:
                if key in payload:
                    payload_summary[key] = payload[key]
        except Exception:
            payload_summary["decode"] = "failed"

        return f"[JWT header={json.dumps(header)}, payload_summary={json.dumps(payload_summary)}]"
    except Exception as exc:
        return f"[JWT: {len(text)} chars, decode_error={str(exc)[:50]}]"


def process_base64(text: str, max_len: int) -> str:
    if len(text) > GoldilocksConfig.MAX_BASE64_DECODE_BYTES:
        return f"[BASE64: {len(text)} chars]"

    try:
        decoded = base64.b64decode(text)
        if decoded[:4] == b"%PDF":
            return f"[BASE64_PDF: {len(decoded)} bytes]"
        if decoded[:8] == b"\x89PNG\r\n\x1a\n":
            return f"[BASE64_PNG: {len(decoded)} bytes]"
        if decoded[:2] == b"\xff\xd8":
            return f"[BASE64_JPEG: {len(decoded)} bytes]"
        if decoded[:4] == b"GIF8":
            return f"[BASE64_GIF: {len(decoded)} bytes]"
        if decoded[:4] == b"PK\x03\x04":
            return f"[BASE64_ZIP: {len(decoded)} bytes]"
        if decoded[:1] in (b"{", b"["):
            try:
                json_content = json.loads(decoded.decode("utf-8"))
                snippet = json.dumps(json_content, ensure_ascii=False, default=str)[:max_len]
                return f"[BASE64_JSON: {snippet}]"
            except Exception:
                pass
        if all(b < 128 for b in decoded[:100]):
            try:
                text_decoded = decoded.decode("utf-8")
                return f"[BASE64_TEXT: {text_decoded[:max_len]}]"
            except Exception:
                pass
        return f"[BASE64: {len(decoded)} bytes]"
    except Exception:
        return f"[BASE64: {len(text)} chars, decode_error]"


def process_url_encoded(text: str, max_len: int) -> str:
    try:
        decoded = unquote(text)
        if "=" in decoded:
            pairs = []
            for part in decoded.split("&"):
                if "=" in part:
                    key, _, value = part.partition("=")
                    snippet = value[:50]
                    if len(value) > 50:
                        snippet += "..."
                    pairs.append(f"{key}={snippet}")
            result = "\n".join(pairs[:20])
            if len(pairs) > 20:
                result += f"\n... ({len(pairs) - 20} more params)"
            return f"[URL_ENCODED]:\n{result}"
        return barbell_focus(decoded, target_bytes=max_len) or decoded[:max_len]
    except Exception:
        return text[:max_len]


def process_xml_content(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    tag_pattern = re.compile(r"<(\w+)[^>]*>")
    tags = tag_pattern.findall(text[:2000])
    unique_tags = list(dict.fromkeys(tags))[:15]
    result = text[:max_len - 100]
    result += f"\n\n[XML: {len(text)} chars, elements: {', '.join(unique_tags[:10])}]"
    return result


def process_messy_string(text: str, max_len: int = 300, key_hint: Optional[str] = None) -> Any:
    if not text:
        return text

    if len(text) <= max_len:
        if _looks_like_json(text):
            parsed = process_json_string(text, max_len)
            return parsed
        if key_hint:
            secret_type = detect_secret(key_hint, text)
            if secret_type:
                return f"[REDACTED_{secret_type.upper()}]"
            key_lower = key_hint.lower()
            if key_lower in JSON_STRING_KEYS and text.strip().startswith(("{", "[")):
                return process_json_string(text, max_len)
        content_type = detect_content_type(text)
        processor = {
            "html": lambda: process_html_content(text, max_len),
            "xml": lambda: process_xml_content(text, max_len),
            "markdown": lambda: barbell_focus(text, target_bytes=max_len),
            "csv": lambda: process_csv_content(text, max_len),
            "log": lambda: process_log_content(text, max_len),
            "stack_trace": lambda: process_stack_trace(text, max_len),
            "sql": lambda: process_sql_content(text, max_len),
            "jwt": lambda: process_jwt(text, max_len),
            "base64": lambda: process_base64(text, max_len),
            "url_encoded": lambda: process_url_encoded(text, max_len),
        }.get(content_type)
        if processor:
            result = processor()
            if result is not None:
                return result
        return text

    content_type = detect_content_type(text)

    if key_hint:
        key_lower = key_hint.lower()
        secret_type = detect_secret(key_hint, text)
        if secret_type:
            return f"[REDACTED_{secret_type.upper()}: {len(text)} chars]"
        if key_lower in JSON_STRING_KEYS and content_type == "text":
            if text.strip().startswith(("{", "[")):
                content_type = "json"

    processors = {
        "json": lambda: process_json_string(text, max_len),
        "html": lambda: process_html_content(text, max_len),
        "xml": lambda: process_xml_content(text, max_len),
        "markdown": lambda: barbell_focus(text, target_bytes=max_len),
        "csv": lambda: process_csv_content(text, max_len),
        "log": lambda: process_log_content(text, max_len),
        "stack_trace": lambda: process_stack_trace(text, max_len),
        "sql": lambda: process_sql_content(text, max_len),
        "jwt": lambda: process_jwt(text, max_len),
        "base64": lambda: process_base64(text, max_len),
        "url_encoded": lambda: process_url_encoded(text, max_len),
        "noise": lambda: f"[NOISE_DATA: {len(text)} chars, entropy={calculate_entropy(text[:500]):.2f}]",
        "text": lambda: barbell_focus(text, target_bytes=max_len),
    }

    processor = processors.get(content_type, processors["text"])
    try:
        result = processor()
        return result if result is not None else text[:max_len]
    except Exception:
        return barbell_focus(text, target_bytes=max_len) or text[:max_len]


@lru_cache(maxsize=4096)
def score_key(key: str) -> int:
    if not key:
        return 0

    k = key.lower().strip("_")
    if k in PRIORITY_KEYS:
        return 100
    if k in CONTAINER_KEYS:
        return 90
    if k in BLOAT_KEYS:
        return -50

    score = 0
    if re.match(r".*(_id|Id|_uuid|_key|_ref)$", key):
        score += 80
    if re.match(r"^(id|uuid|guid|pk|sk)$", k):
        score += 90
    if re.match(r"^(is_|has_|can_|should_|will_|did_)", k):
        score += 60
    if re.match(r".*(enabled|disabled|active|visible|valid)$", k):
        score += 55
    if re.match(r".*(name|title|label|heading).*", k):
        score += 70
    if re.match(r".*(url|href|link|uri|endpoint).*", k):
        score += 65
    if re.match(r".*(created|updated|modified|timestamp|date|time).*", k):
        score += 55
    if re.match(r".*(count|total|sum|num|quantity|amount).*", k):
        score += 50
    if re.match(r".*(status|state|phase|result|outcome).*", k):
        score += 65
    if re.match(r".*(html|raw|blob|base64|encoded|binary).*", k):
        score -= 40
    if re.match(r".*(debug|trace|internal|private|cache).*", k):
        score -= 35
    if re.match(r"^(_|__|@|$)", key):
        score -= 25

    return score


def score_value_importance(value: Any, key: Optional[str] = None) -> float:
    if value is None:
        return 0.1

    if isinstance(value, bool):
        return 0.7
    if isinstance(value, (int, float)):
        return 0.6
    if isinstance(value, str):
        if not value:
            return 0.1
        if is_likely_noise(value):
            return 0.1
        if len(value) < 50:
            return 0.8
        if len(value) < 200:
            return 0.6
        content_type = detect_content_type(value)
        if content_type in ("noise", "base64"):
            return 0.2
        if content_type in ("html", "log"):
            return 0.3
        return 0.4
    if isinstance(value, list):
        return 0.7 if value else 0.2
    if isinstance(value, dict):
        return 0.7 if value else 0.2
    return 0.5


def find_all_container_candidates(obj: dict, path: str = "") -> list[tuple[str, list, int]]:
    candidates = []
    if not isinstance(obj, dict):
        return candidates

    for key, value in obj.items():
        current_path = f"{path}.{key}" if path else key
        k_lower = key.lower()

        if isinstance(value, list):
            score = 0
            if k_lower in CONTAINER_KEYS:
                score += 50
            elif any(container in k_lower for container in ["data", "item", "result", "record"]):
                score += 30

            if value:
                score += min(len(value), 20)
                if isinstance(value[0], dict):
                    score += 20
                    if any(k in value[0] for k in ["id", "name", "title", "uuid", "_id"]):
                        score += 15

            candidates.append((current_path, value, score))
        elif isinstance(value, dict):
            candidates.extend(find_all_container_candidates(value, current_path))
            for subkey, subvalue in value.items():
                if isinstance(subvalue, list) and subkey.lower() in CONTAINER_KEYS:
                    nested_path = f"{current_path}.{subkey}"
                    score = 40
                    if subvalue and isinstance(subvalue[0], dict):
                        score += 25
                    candidates.append((nested_path, subvalue, score))

    return candidates


def find_best_container(obj: dict) -> tuple[Optional[str], Optional[list]]:
    candidates = find_all_container_candidates(obj)
    if not candidates:
        return None, None
    candidates.sort(key=lambda x: -x[2])
    best_path, best_array, best_score = candidates[0]
    if best_score < 10:
        return None, None
    return best_path, best_array


def set_nested_value(obj: dict, path: str, value: Any) -> dict:
    parts = path.split(".")
    current = obj
    for part in parts[:-1]:
        if part not in current:
            current[part] = {}
        current = current[part]
    current[parts[-1]] = value
    return obj


def infer_array_schema(arr: list, sample_size: Optional[int] = None) -> dict:
    sample_size = sample_size or GoldilocksConfig.MAX_SCHEMA_INFERENCE_ITEMS
    if not arr or not isinstance(arr[0], dict):
        return {}
    sample = arr[:sample_size]
    field_info: dict[str, dict[str, Any]] = {}

    for item in sample:
        if not isinstance(item, dict):
            continue
        for key, value in item.items():
            if key not in field_info:
                field_info[key] = {
                    "count": 0,
                    "types": Counter(),
                    "non_null": 0,
                    "avg_length": 0.0,
                }
            info = field_info[key]
            info["count"] += 1
            info["types"][type(value).__name__] += 1
            if value is not None:
                info["non_null"] += 1
                if isinstance(value, str):
                    info["avg_length"] = (
                        (info["avg_length"] * (info["non_null"] - 1) + len(value)) / info["non_null"]
                    )
    return field_info


def rank_fields_by_importance(schema: dict) -> list[str]:
    if not schema:
        return []
    field_scores = []
    for field, info in schema.items():
        score = score_key(field)
        if info["count"] > 0:
            presence_ratio = info["non_null"] / info["count"]
            score += presence_ratio * 20
        dominant_type = info["types"].most_common(1)[0][0] if info["types"] else "NoneType"
        if dominant_type in ("int", "float", "bool"):
            score += 10
        elif dominant_type == "str":
            if info["avg_length"] < 50:
                score += 15
            elif info["avg_length"] < 200:
                score += 5
            else:
                score -= 10
        field_scores.append((field, score))
    field_scores.sort(key=lambda x: -x[1])
    return [f for f, _ in field_scores]


def process_array_goldilocks(
    arr: list,
    max_items: int,
    max_str_len: int,
    max_total_chars: int,
    max_depth: int,
    current_depth: int,
    char_budget: int,
) -> list:
    if not arr:
        return []

    if len(arr) <= max_items:
        indices = list(range(len(arr)))
    else:
        head_count = max(1, int(max_items * GoldilocksConfig.HEAD_WEIGHT))
        tail_count = max(1, int(max_items * GoldilocksConfig.TAIL_WEIGHT))
        mid_count = max_items - head_count - tail_count

        head_indices = list(range(min(head_count, len(arr))))
        tail_start = max(head_count, len(arr) - tail_count)
        tail_indices = list(range(tail_start, len(arr)))

        mid_indices = []
        if mid_count > 0:
            mid_start = head_count
            mid_end = tail_start
            mid_range = mid_end - mid_start
            if mid_range > 0:
                step = max(1, mid_range // (mid_count + 1))
                for i in range(mid_count):
                    idx = mid_start + step * (i + 1)
                    if idx < mid_end:
                        mid_indices.append(idx)

        indices = head_indices + mid_indices + tail_indices

    item_budget = max(char_budget // max(len(indices), 1), 50)
    result = []
    last_idx = -1

    for idx in indices:
        if idx > last_idx + 1 and last_idx >= 0:
            gap = idx - last_idx - 1
            result.append(f"[...{gap} items omitted...]")

        item = arr[idx]
        processed = process_value_goldilocks(
            item,
            key_hint=None,
            max_items=max(3, max_items // 2),
            max_str_len=max_str_len // 2 if current_depth > 2 else max_str_len,
            max_total_chars=item_budget,
            max_depth=max_depth,
            current_depth=current_depth + 1,
            char_budget=item_budget,
        )
        result.append(processed)
        last_idx = idx

    if len(arr) > len(indices):
        shown = len([x for x in result if not isinstance(x, str) or not x.startswith("[...")])
        result.insert(0, f"[ARRAY_TOTAL: {len(arr)} items, showing {shown}]")

    return result


def process_object_goldilocks(
    obj: dict,
    max_items: int,
    max_str_len: int,
    max_total_chars: int,
    max_depth: int,
    current_depth: int,
    char_budget: int,
) -> dict:
    if not obj:
        return {}

    result: OrderedDict[str, Any] = OrderedDict()
    remaining_budget = char_budget
    bloat_candidates: list[tuple[str, Any, int]] = []

    container_path, container_data = find_best_container(obj)
    container_root = container_path.split(".")[0] if container_path else None

    critical_keys = set()

    for key, val in obj.items():
        k_lower = key.lower()
        if k_lower in ("error", "errors", "message", "code", "status_code", "statuscode", "ok", "success"):
            if isinstance(val, (str, int, bool, type(None))):
                result[key] = val
                remaining_budget -= len(str(val))
                critical_keys.add(key)
            elif isinstance(val, dict):
                processed = process_value_goldilocks(
                    val,
                    max_items=10,
                    max_str_len=500,
                    max_total_chars=min(500, remaining_budget // 2),
                    max_depth=max_depth,
                    current_depth=current_depth + 1,
                    char_budget=min(500, remaining_budget // 2),
                )
                result[key] = processed
                remaining_budget -= len(str(processed))
                critical_keys.add(key)
        elif k_lower in (
            "total", "count", "page", "per_page", "limit", "offset",
            "has_more", "hasmore", "next", "previous", "cursor",
            "next_cursor", "nextcursor", "total_count", "totalcount",
            "total_pages", "totalpages", "page_info", "pageinfo",
        ):
            result[key] = val
            remaining_budget -= len(str(val))
            critical_keys.add(key)
        elif k_lower in ("meta", "metadata", "pagination", "paging", "page_info", "pageinfo"):
            if isinstance(val, dict):
                limited = {k: v for k, v in list(val.items())[:10]}
                result[key] = limited
                remaining_budget -= len(str(limited))
                critical_keys.add(key)

    if container_path and container_data is not None:
        container_budget = int(remaining_budget * 0.7)
        if isinstance(container_data, list) and container_data and isinstance(container_data[0], dict):
            schema = infer_array_schema(container_data)
            _ = rank_fields_by_importance(schema)
        processed_container = process_array_goldilocks(
            container_data,
            max_items=max_items,
            max_str_len=max_str_len,
            max_total_chars=container_budget,
            max_depth=max_depth,
            current_depth=current_depth + 1,
            char_budget=container_budget,
        )
        set_nested_value(result, container_path, processed_container)
        remaining_budget -= container_budget

    remaining_keys = [
        k for k in obj.keys()
        if k not in critical_keys and (not container_root or k != container_root)
    ]
    sorted_keys = sorted(remaining_keys, key=lambda k: -score_key(k))

    stop_threshold = min(100, max(25, char_budget // 4))

    for idx, key in enumerate(sorted_keys):
        if remaining_budget <= stop_threshold and idx > 0:
            omitted = len(sorted_keys) - idx
            if omitted > 0:
                result["[FIELDS_OMITTED]"] = omitted
            break

        val = obj[key]
        looks_like_json_string = isinstance(val, str) and _looks_like_json(val)
        key_score = score_key(key)
        if key_score < -30 and not looks_like_json_string:
            bloat_candidates.append((key, val, key_score))
            continue

        effective_key_score = key_score
        if looks_like_json_string and effective_key_score < 70:
            effective_key_score = 70

        if effective_key_score >= 70:
            field_budget = min(remaining_budget // 3, 1000)
        elif effective_key_score >= 30:
            field_budget = min(remaining_budget // 5, 500)
        else:
            field_budget = min(remaining_budget // 8, 200)

        processed = process_value_goldilocks(
            val,
            key_hint=key,
            max_items=max(3, max_items // 2),
            max_str_len=max_str_len // 2 if effective_key_score < 50 else max_str_len,
            max_total_chars=field_budget,
            max_depth=max_depth,
            current_depth=current_depth + 1,
            char_budget=field_budget,
        )
        result[key] = processed
        remaining_budget -= len(str(processed))
        if remaining_budget <= stop_threshold and idx < len(sorted_keys) - 1:
            omitted = len(sorted_keys) - (idx + 1)
            if omitted > 0:
                result["[FIELDS_OMITTED]"] = omitted
            break

    if not result and bloat_candidates:
        key, val, key_score = max(
            bloat_candidates,
            key=lambda item: (item[2], score_value_importance(item[1], item[0]), len(str(item[1]))),
        )
        fallback_budget = min(remaining_budget, max(max_str_len, 200))
        result[key] = process_value_goldilocks(
            val,
            key_hint=key,
            max_items=max(3, max_items // 2),
            max_str_len=max_str_len // 2 if key_score < 0 else max_str_len,
            max_total_chars=fallback_budget,
            max_depth=max_depth,
            current_depth=current_depth + 1,
            char_budget=fallback_budget,
        )

    return dict(result)


def process_value_goldilocks(
    value: Any,
    key_hint: Optional[str] = None,
    max_items: Optional[int] = None,
    max_str_len: Optional[int] = None,
    max_total_chars: Optional[int] = None,
    max_depth: Optional[int] = None,
    current_depth: int = 0,
    char_budget: Optional[int] = None,
) -> Any:
    max_items = max_items or GoldilocksConfig.MAX_ITEMS
    max_str_len = max_str_len or GoldilocksConfig.MAX_STR_LEN
    max_total_chars = max_total_chars or GoldilocksConfig.MAX_TOTAL_CHARS
    max_depth = max_depth or GoldilocksConfig.MAX_DEPTH
    char_budget = char_budget or max_total_chars

    if current_depth > max_depth:
        return "[MAX_DEPTH_REACHED]"
    if char_budget <= 0:
        return "[BUDGET_EXHAUSTED]"

    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return process_messy_string(value, max_len=min(max_str_len, char_budget), key_hint=key_hint)
    if isinstance(value, list):
        return process_array_goldilocks(
            value, max_items, max_str_len, max_total_chars,
            max_depth, current_depth, char_budget
        )
    if isinstance(value, dict):
        return process_object_goldilocks(
            value, max_items, max_str_len, max_total_chars,
            max_depth, current_depth, char_budget
        )
    return str(value)[:max_str_len]


def json_goldilocks(
    data: Any,
    *,
    max_items: Optional[int] = None,
    max_str_len: Optional[int] = None,
    max_total_chars: Optional[int] = None,
    max_depth: Optional[int] = None,
) -> Any:
    max_items = max_items or GoldilocksConfig.MAX_ITEMS
    max_str_len = max_str_len or GoldilocksConfig.MAX_STR_LEN
    max_total_chars = max_total_chars or GoldilocksConfig.MAX_TOTAL_CHARS
    max_depth = max_depth or GoldilocksConfig.MAX_DEPTH

    result = process_value_goldilocks(
        data,
        max_items=max_items,
        max_str_len=max_str_len,
        max_total_chars=max_total_chars,
        max_depth=max_depth,
        current_depth=0,
        char_budget=max_total_chars,
    )

    result_str = json.dumps(result, default=str, ensure_ascii=False)
    if len(result_str) > max_total_chars * 1.5:
        result = json_goldilocks(
            data,
            max_items=max(3, max_items // 2),
            max_str_len=max(100, max_str_len // 2),
            max_total_chars=max_total_chars,
            max_depth=max(3, max_depth - 2),
        )

    return result


def goldilocks_summary(data: Any, *, max_bytes: int = GoldilocksConfig.MAX_TOTAL_CHARS) -> str:
    max_bytes = max(max_bytes, 200)
    result = json_goldilocks(data, max_total_chars=max_bytes, max_str_len=max_bytes // 4)
    rendered = json.dumps(result, indent=2, ensure_ascii=False, default=str)
    if len(rendered.encode("utf-8")) <= max_bytes:
        return rendered

    result = json_goldilocks(
        data,
        max_items=max(3, GoldilocksConfig.MAX_ITEMS // 2),
        max_str_len=max(100, GoldilocksConfig.MAX_STR_LEN // 2),
        max_total_chars=max_bytes,
        max_depth=max(3, GoldilocksConfig.MAX_DEPTH - 2),
    )
    rendered = json.dumps(result, indent=2, ensure_ascii=False, default=str)
    return _truncate_bytes(rendered, max_bytes)


def _truncate_bytes(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


__all__ = [
    "json_goldilocks",
    "goldilocks_summary",
    "process_messy_string",
    "detect_content_type",
    "GoldilocksConfig",
]
