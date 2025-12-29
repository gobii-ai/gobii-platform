import logging
import re
from html.parser import HTMLParser
from typing import Any, Dict

import pdfkit

from django.conf import settings

from api.models import PersistentAgent
from api.agent.files.filespace_service import write_bytes_to_exports

logger = logging.getLogger(__name__)

DEFAULT_FILENAME = "export.pdf"
EXTENSION = ".pdf"
MIME_TYPE = "application/pdf"

CSS_URL_RE = re.compile(r"url\(\s*['\"]?\s*(?P<url>[^)\"'\s]+)", re.IGNORECASE)
CSS_IMPORT_RE = re.compile(r"@import\s+(?:url\()?['\"]?\s*(?P<url>[^'\"\)\s]+)", re.IGNORECASE)
META_REFRESH_URL_RE = re.compile(r"url\s*=\s*(?P<url>[^;]+)", re.IGNORECASE)
URL_ATTRS = {"src", "href", "data", "poster", "action", "formaction", "xlink:href", "background"}


def _is_allowed_asset_url(url: str) -> bool:
    url = url.strip()
    if not url:
        return True
    if url.startswith("#"):
        return True
    return url.lower().startswith("data:")


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


def get_create_pdf_tool() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "create_pdf",
            "description": (
                "Create a PDF from provided HTML and store it in the agent filespace under /exports. "
                "The HTML must be self-contained; external or local asset references are not allowed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "html": {"type": "string", "description": "HTML string to convert into a PDF."},
                    "filename": {"type": "string", "description": "Optional output filename (defaults to export.pdf)."},
                },
                "required": ["html"],
            },
        },
    }


def execute_create_pdf(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    html = params.get("html")
    if not isinstance(html, str) or not html.strip():
        return {"status": "error", "message": "Missing required parameter: html"}

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
            "message": "HTML contains external or local asset references. Only inline data assets are allowed.",
        }

    filename = params.get("filename")
    if filename is not None and not isinstance(filename, str):
        return {"status": "error", "message": "filename must be a string when provided"}

    options = {
        "disable-local-file-access": "",
        "disable-javascript": "",
        "encoding": "utf-8",
        "quiet": "",
    }

    try:
        pdf_bytes = pdfkit.from_string(html, False, options=options)
    except OSError as exc:
        logger.exception("wkhtmltopdf is required to generate PDFs: %s", exc)
        return {
            "status": "error",
            "message": "PDF generation requires wkhtmltopdf to be installed on the server.",
        }
    except Exception:
        logger.exception("Failed to generate PDF for agent %s", agent.id)
        return {"status": "error", "message": "Failed to generate the PDF from the provided HTML."}

    if not pdf_bytes:
        return {"status": "error", "message": "PDF generation returned empty output."}

    return write_bytes_to_exports(
        agent=agent,
        content_bytes=pdf_bytes,
        filename=filename,
        fallback_name=DEFAULT_FILENAME,
        extension=EXTENSION,
        mime_type=MIME_TYPE,
    )
