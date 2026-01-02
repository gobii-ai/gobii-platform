import base64
import logging
import re
from html.parser import HTMLParser
from typing import Any, Dict
from urllib.parse import unquote_to_bytes

import pdfkit

from django.conf import settings

from api.models import PersistentAgent
from api.agent.files.filespace_service import write_bytes_to_dir
from api.agent.tools.file_export_helpers import resolve_export_target

logger = logging.getLogger(__name__)

EXTENSION = ".pdf"
MIME_TYPE = "application/pdf"

PDFKIT_OPTIONS = {
    "disable-local-file-access": "",
    "disable-javascript": "",
    "encoding": "utf-8",
    "quiet": "",
}

CSS_URL_RE = re.compile(r"url\(\s*['\"]?\s*(?P<url>[^)\"'\s]+)", re.IGNORECASE)
CSS_IMPORT_RE = re.compile(r"@import\s+(?:url\()?['\"]?\s*(?P<url>[^'\"\)\s]+)", re.IGNORECASE)
META_REFRESH_URL_RE = re.compile(r"url\s*=\s*(?P<url>[^;]+)", re.IGNORECASE)
DATA_URL_RE = re.compile(r"^data:(?P<meta>[^,]*?),(?P<data>.*)$", re.IGNORECASE | re.DOTALL)
SVG_URL_ATTR_RE = re.compile(r"(?:href|xlink:href)\s*=\s*['\"](?P<url>[^'\"]+)['\"]", re.IGNORECASE)
URL_ATTRS = {"src", "href", "data", "poster", "action", "formaction", "xlink:href", "background"}


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


def get_create_pdf_tool() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "create_pdf",
            "description": (
                "Create a PDF from provided HTML and store it in the agent filespace. "
                "Recommended path: /exports/your-file.pdf. The HTML must be self-contained; "
                "external or local asset references are not allowed."
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

    path, overwrite, error = resolve_export_target(params)
    if error:
        return error

    try:
        pdf_bytes = pdfkit.from_string(html, False, options=PDFKIT_OPTIONS)
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

    return write_bytes_to_dir(
        agent=agent,
        content_bytes=pdf_bytes,
        extension=EXTENSION,
        mime_type=MIME_TYPE,
        path=path,
        overwrite=overwrite,
    )
