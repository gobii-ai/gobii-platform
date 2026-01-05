import base64
import logging
import re
from html.parser import HTMLParser
from typing import Any, Dict
from urllib.parse import unquote_to_bytes

from django.conf import settings

from api.models import PersistentAgent
from api.agent.files.filespace_service import write_bytes_to_dir
from api.agent.files.attachment_helpers import build_signed_filespace_download_url
from api.agent.tools.file_export_helpers import resolve_export_target
from api.agent.tools.agent_variables import set_agent_variable, substitute_variables_as_data_uris

logger = logging.getLogger(__name__)

EXTENSION = ".pdf"
MIME_TYPE = "application/pdf"

# Comprehensive PDF styling for publication-quality output
DEFAULT_PRINT_CSS = """
/* ==========================================================================
   PAGE SETUP & RUNNING HEADERS/FOOTERS
   ========================================================================== */
@page {
    size: Letter;
    margin: 25mm 20mm 30mm 20mm;

    @top-center {
        content: string(doc-title);
        font-size: 9pt;
        color: #666;
        font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    }

    @bottom-center {
        content: counter(page);
        font-size: 9pt;
        color: #666;
        font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    }
}

/* Named page for cover - no headers/footers */
@page cover {
    @top-center { content: none; }
    @bottom-center { content: none; }
}

/* First page - no page number */
@page :first {
    @bottom-center { content: none; }
}

/* ==========================================================================
   STRING SET FOR RUNNING HEADERS
   ========================================================================== */
.doc-title, h1:first-of-type {
    string-set: doc-title content();
}

/* ==========================================================================
   BASE TYPOGRAPHY - Modern Minimal
   ========================================================================== */
html, body {
    font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
                 "Helvetica Neue", Arial, sans-serif;
    font-size: 11pt;
    line-height: 1.6;
    color: #1a1a1a;
}

h1, h2, h3, h4, h5, h6 {
    font-weight: 600;
    line-height: 1.3;
    margin-top: 1.5em;
    margin-bottom: 0.5em;
    color: #111;
    break-after: avoid;
    break-inside: avoid;
}

h1 { font-size: 24pt; margin-top: 0; }
h2 { font-size: 18pt; }
h3 { font-size: 14pt; }
h4 { font-size: 12pt; }
h5, h6 { font-size: 11pt; }

p { margin: 0 0 1em 0; }

/* ==========================================================================
   CRITICAL PAGE BREAK HANDLING
   ========================================================================== */

/* Keep headings with following content */
h1 + *, h2 + *, h3 + *, h4 + *, h5 + *, h6 + * {
    break-before: avoid;
}

/* Orphan/widow control */
p, li, dd, dt {
    orphans: 3;
    widows: 3;
}

/* Figures, images stay together */
figure, img {
    break-inside: avoid;
}

/* ==========================================================================
   TABLES - Repeating headers across pages
   ========================================================================== */
table {
    break-inside: auto;
    border-collapse: collapse;
    width: 100%;
    margin: 1em 0;
    font-size: 10pt;
}

thead {
    display: table-header-group;
}

tfoot {
    display: table-footer-group;
}

tbody {
    display: table-row-group;
}

tr {
    break-inside: avoid;
}

th, td {
    padding: 10px 12px;
    text-align: left;
    border-bottom: 1px solid #e9ecef;
}

th {
    font-weight: 600;
    background: #f8f9fa;
    color: #495057;
    text-transform: uppercase;
    font-size: 9pt;
    letter-spacing: 0.5px;
}

tbody tr:last-child td {
    border-bottom: none;
}

tbody tr:nth-child(even) {
    background: #fafbfc;
}

/* ==========================================================================
   CODE BLOCKS
   ========================================================================== */
pre, code {
    font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas,
                 "Liberation Mono", monospace;
    font-size: 9.5pt;
}

pre {
    break-inside: avoid;
    overflow-wrap: break-word;
    white-space: pre-wrap;
    background: #f8f9fa;
    border: 1px solid #e9ecef;
    border-radius: 4px;
    padding: 1em;
    margin: 1em 0;
}

/* For very long code blocks, allow breaking */
pre.allow-break {
    break-inside: auto;
}

/* ==========================================================================
   BLOCKQUOTES
   ========================================================================== */
blockquote {
    margin: 1.5em 0;
    padding: 1em 1.5em;
    border-left: 4px solid #4C78A8;
    background: #f8f9fa;
    color: #495057;
    font-style: italic;
    break-inside: avoid;
}

blockquote p:last-child {
    margin-bottom: 0;
}

/* ==========================================================================
   LISTS
   ========================================================================== */
ul, ol {
    margin: 1em 0;
    padding-left: 1.5em;
}

li {
    margin-bottom: 0.5em;
}

li > ul, li > ol {
    margin-top: 0.5em;
    margin-bottom: 0;
}

/* ==========================================================================
   UTILITY CLASSES
   ========================================================================== */

/* Force page break after element */
.page-break {
    break-after: always;
}

/* Force page break before element */
.page-break-before {
    break-before: always;
}

/* Keep element together (no internal breaks) */
.no-break {
    break-inside: avoid;
}

/* Logical section - prefer breaking before, not inside */
.section {
    break-inside: avoid-page;
    break-before: auto;
}

.section > h1:first-child,
.section > h2:first-child,
.section > h3:first-child,
.section > h4:first-child {
    break-after: avoid;
}

/* ==========================================================================
   COVER PAGE
   ========================================================================== */
.cover-page {
    page: cover;
    break-after: always;
    display: flex;
    flex-direction: column;
    justify-content: center;
    align-items: center;
    min-height: 100vh;
    text-align: center;
}

.cover-page h1 {
    font-size: 36pt;
    font-weight: 700;
    margin-bottom: 0.5em;
    color: #111;
}

.cover-page .subtitle {
    font-size: 16pt;
    color: #666;
    margin-bottom: 2em;
}

.cover-page .meta {
    font-size: 11pt;
    color: #888;
}

/* ==========================================================================
   LINKS (show URL in print)
   ========================================================================== */
a {
    color: #4C78A8;
    text-decoration: none;
}

a[href^="http"]:after {
    content: " (" attr(href) ")";
    font-size: 0.8em;
    color: #888;
}

a[href^="#"]:after,
a.no-url:after {
    content: none;
}

/* ==========================================================================
   HORIZONTAL RULES
   ========================================================================== */
hr {
    border: none;
    border-top: 1px solid #e9ecef;
    margin: 2em 0;
}
"""

CSS_URL_RE = re.compile(r"url\(\s*['\"]?\s*(?P<url>[^)\"'\s]+)", re.IGNORECASE)
CSS_IMPORT_RE = re.compile(r"@import\s+(?:url\()?['\"]?\s*(?P<url>[^'\"\)\s]+)", re.IGNORECASE)
META_REFRESH_URL_RE = re.compile(r"url\s*=\s*(?P<url>[^;]+)", re.IGNORECASE)
DATA_URL_RE = re.compile(r"^data:(?P<meta>[^,]*?),(?P<data>.*)$", re.IGNORECASE | re.DOTALL)
SVG_URL_ATTR_RE = re.compile(r"(?:href|xlink:href)\s*=\s*['\"](?P<url>[^'\"]+)['\"]", re.IGNORECASE)
URL_ATTRS = {"src", "href", "data", "poster", "action", "formaction", "xlink:href", "background"}


def _secure_url_fetcher(url, timeout=10, ssl_context=None):
    """
    Security layer: Only allow data: URIs, block all external/local resources.
    This is a second layer of defense after the HTML scanner.
    """
    if url.startswith('data:'):
        from weasyprint import default_url_fetcher
        return default_url_fetcher(url, timeout, ssl_context)
    # Block everything else - external URLs, file://, etc.
    raise ValueError(f"External resources not allowed: {url}")


def _is_allowed_asset_url(url: str) -> bool:
    url = url.strip()
    if not url:
        return True
    if url.startswith("#"):
        return True
    if not url.lower().startswith("data:"):
        return False
    return _is_allowed_data_url(url)


def _is_allowed_data_url(url: str) -> bool:
    parsed = _parse_data_url(url)
    if not parsed:
        return False
    media_type, payload = parsed
    if media_type == "image/svg+xml":
        return not _svg_contains_blocked_urls(payload)
    if media_type.startswith("image/"):
        return True
    return False


def _parse_data_url(url: str) -> tuple[str, bytes] | None:
    match = DATA_URL_RE.match(url)
    if not match:
        return None
    media_type, is_base64 = _parse_data_url_meta(match.group("meta"))
    data = match.group("data")
    try:
        if is_base64:
            data = re.sub(r"\s+", "", data)
            if data:
                data += "=" * (-len(data) % 4)
            payload = base64.b64decode(data, validate=True)
        else:
            payload = unquote_to_bytes(data)
    except Exception:
        return None
    return media_type, payload


def _parse_data_url_meta(meta: str) -> tuple[str, bool]:
    media_type = ""
    is_base64 = False
    if meta:
        parts = [part.strip() for part in meta.split(";") if part.strip()]
        if parts:
            if "/" in parts[0]:
                media_type = parts[0].lower()
                parts = parts[1:]
            for part in parts:
                if part.lower() == "base64":
                    is_base64 = True
    if not media_type:
        media_type = "text/plain"
    return media_type, is_base64


def _svg_contains_blocked_urls(payload: bytes) -> bool:
    try:
        text = payload.decode("utf-8", errors="replace")
    except Exception:
        return True
    if _css_contains_blocked_urls(text):
        return True
    for match in SVG_URL_ATTR_RE.finditer(text):
        url = match.group("url").strip()
        if url and not _is_allowed_asset_url(url):
            return True
    return False


def _srcset_contains_blocked_urls(value: str) -> bool:
    length = len(value)
    idx = 0
    while idx < length:
        while idx < length and value[idx] in " \t\r\n,":
            idx += 1
        if idx >= length:
            break
        url, idx = _consume_srcset_url(value, idx)
        if url and not _is_allowed_asset_url(url):
            return True
        while idx < length and value[idx] != ",":
            idx += 1
        if idx < length and value[idx] == ",":
            idx += 1
    return False


def _consume_srcset_url(value: str, start: int) -> tuple[str, int]:
    if value[start:start + 5].lower() == "data:":
        idx = start + 5
        while idx < len(value) and value[idx] not in " \t\r\n":
            idx += 1
        return value[start:idx], idx
    idx = start
    while idx < len(value) and value[idx] not in " \t\r\n,":
        idx += 1
    return value[start:idx], idx


def _css_contains_blocked_urls(text: str) -> bool:
    for match in CSS_URL_RE.finditer(text):
        if not _is_allowed_asset_url(match.group("url")):
            return True
    for match in CSS_IMPORT_RE.finditer(text):
        if not _is_allowed_asset_url(match.group("url")):
            return True
    return False


def _meta_refresh_contains_blocked_url(attrs: list[tuple[str, str | None]]) -> bool:
    attr_map = {key.lower(): value for key, value in attrs if value is not None}
    if attr_map.get("http-equiv", "").lower() != "refresh":
        return False
    content = attr_map.get("content", "")
    match = META_REFRESH_URL_RE.search(content)
    if not match:
        return False
    url = match.group("url").strip().strip("'\"")
    return bool(url) and not _is_allowed_asset_url(url)


class _AssetScanParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.blocked = False
        self._in_style = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._inspect_attrs(tag, attrs)
        if tag.lower() == "style":
            self._in_style = True

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._inspect_attrs(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "style":
            self._in_style = False

    def handle_data(self, data: str) -> None:
        if self._in_style and _css_contains_blocked_urls(data):
            self.blocked = True

    def _inspect_attrs(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "meta" and _meta_refresh_contains_blocked_url(attrs):
            self.blocked = True
            return
        for key, value in attrs:
            if not value:
                continue
            key = key.lower()
            if key in URL_ATTRS and not _is_allowed_asset_url(value):
                self.blocked = True
                return
            if key == "srcset" and _srcset_contains_blocked_urls(value):
                self.blocked = True
                return
            if key == "style" and _css_contains_blocked_urls(value):
                self.blocked = True
                return


def _contains_blocked_asset_references(html: str) -> bool:
    parser = _AssetScanParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        logger.exception("Failed to parse HTML for asset scanning.")
        return True
    return parser.blocked


def _inject_print_css(html: str) -> str:
    """Inject default print CSS for clean page breaks."""
    style_tag = f"<style>{DEFAULT_PRINT_CSS}</style>"

    # Try to inject into <head>
    head_match = re.search(r"<head[^>]*>", html, re.IGNORECASE)
    if head_match:
        insert_pos = head_match.end()
        return html[:insert_pos] + style_tag + html[insert_pos:]

    # No <head>, try after <html>
    html_match = re.search(r"<html[^>]*>", html, re.IGNORECASE)
    if html_match:
        insert_pos = html_match.end()
        return html[:insert_pos] + f"<head>{style_tag}</head>" + html[insert_pos:]

    # No structure at all, wrap it
    return f"<html><head>{style_tag}</head><body>{html}</body></html>"


def get_create_pdf_tool() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "create_pdf",
            "description": (
                "Create a publication-quality PDF from HTML. "
                "Recommended path: /exports/your-file.pdf. HTML must be self-contained. "
                "\n\nUtility classes:\n"
                "- .page-break / .page-break-before: force page breaks\n"
                "- .no-break: keep element together\n"
                "- .section: logical section (prefers breaking before, not inside)\n"
                "- .cover-page: title page (no header/footer)\n"
                "- .doc-title: set running header text\n"
                "\nTables with <thead> repeat headers across pages. "
                "Returns `file`, `inline`, `inline_html`, and `attach` with variable placeholders."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "html": {"type": "string", "description": "HTML string to convert into a PDF."},
                    "file_path": {
                        "type": "string",
                        "description": (
                            "Required filespace path (recommended: /exports/report.pdf). "
                            "Use overwrite=true to replace an existing file at that path."
                        ),
                    },
                    "overwrite": {
                        "type": "boolean",
                        "description": "When true, overwrites the existing file at that path.",
                    },
                },
                "required": ["html", "file_path"],
            },
        },
    }


def execute_create_pdf(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    html = params.get("html")
    if not isinstance(html, str) or not html.strip():
        return {"status": "error", "message": "Missing required parameter: html"}

    # Substitute «path» variables with data URIs (PDF needs embedded content, not URLs)
    html = substitute_variables_as_data_uris(html, agent)

    max_size = getattr(settings, "MAX_FILE_SIZE", None)
    if max_size:
        html_bytes = html.encode("utf-8")
        if len(html_bytes) > max_size:
            return {
                "status": "error",
                "message": (
                    f"HTML exceeds maximum allowed size ({len(html_bytes)} bytes > {max_size} bytes)."
                ),
            }

    if _contains_blocked_asset_references(html):
        return {
            "status": "error",
            "message": (
                "HTML contains external or local asset references. "
                "Use «/path» variables (from create_chart/create_csv/create_pdf) for embeds."
            ),
        }

    path, overwrite, error = resolve_export_target(params)
    if error:
        return error

    # Inject default print CSS for clean page breaks
    html = _inject_print_css(html)

    try:
        from weasyprint import HTML
        pdf_bytes = HTML(string=html, url_fetcher=_secure_url_fetcher).write_pdf()
    except ValueError as exc:
        # Raised by _secure_url_fetcher for blocked resources
        logger.warning("Blocked resource access during PDF generation: %s", exc)
        return {
            "status": "error",
            "message": "HTML references external resources that are not allowed.",
        }
    except Exception:
        logger.exception("Failed to generate PDF for agent %s", agent.id)
        return {"status": "error", "message": "Failed to generate the PDF from the provided HTML."}

    if not pdf_bytes:
        return {"status": "error", "message": "PDF generation returned empty output."}

    result = write_bytes_to_dir(
        agent=agent,
        content_bytes=pdf_bytes,
        extension=EXTENSION,
        mime_type=MIME_TYPE,
        path=path,
        overwrite=overwrite,
    )
    if result.get("status") != "ok":
        return result

    # Set variable using path as name (unique, human-readable)
    file_path = result.get("path")
    node_id = result.get("node_id")
    signed_url = build_signed_filespace_download_url(
        agent_id=str(agent.id),
        node_id=node_id,
    )
    set_agent_variable(file_path, signed_url)

    var_ref = f"«{file_path}»"
    return {
        "status": "ok",
        "file": var_ref,
        "inline": f"[Download]({var_ref})",
        "inline_html": f"<a href='{var_ref}'>Download</a>",
        "attach": var_ref,
    }
