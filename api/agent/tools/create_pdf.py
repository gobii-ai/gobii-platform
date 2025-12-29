import logging
import re
from typing import Any, Dict

import pdfkit

from api.models import PersistentAgent
from .filespace_writer import write_bytes_to_exports

logger = logging.getLogger(__name__)

DEFAULT_FILENAME = "export.pdf"
EXTENSION = ".pdf"
MIME_TYPE = "application/pdf"

ASSET_TAG_RE = re.compile(
    r"<\s*(img|script|link|iframe|video|audio|source|object|embed)\b[^>]*"
    r"(?:src|href)\s*=\s*['\"]\s*(?P<url>[^'\"]+)",
    re.IGNORECASE,
)
SRCSET_RE = re.compile(r"\bsrcset\s*=\s*['\"](?P<value>[^'\"]+)", re.IGNORECASE)
CSS_URL_RE = re.compile(r"url\(\s*['\"]?\s*(?P<url>[^)\"'\s]+)", re.IGNORECASE)
CSS_IMPORT_RE = re.compile(r"@import\s+(?:url\()?['\"]?\s*(?P<url>[^'\"\)\s]+)", re.IGNORECASE)


def _is_allowed_asset_url(url: str) -> bool:
    url = url.strip()
    if not url:
        return True
    if url.startswith("#"):
        return True
    return url.lower().startswith("data:")


def _contains_blocked_asset_references(html: str) -> bool:
    for match in ASSET_TAG_RE.finditer(html):
        if not _is_allowed_asset_url(match.group("url")):
            return True

    for match in SRCSET_RE.finditer(html):
        for candidate in match.group("value").split(","):
            url = candidate.strip().split(" ")[0]
            if url and not _is_allowed_asset_url(url):
                return True

    for match in CSS_URL_RE.finditer(html):
        if not _is_allowed_asset_url(match.group("url")):
            return True

    for match in CSS_IMPORT_RE.finditer(html):
        if not _is_allowed_asset_url(match.group("url")):
            return True

    return False


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
