import mimetypes
import os
from typing import Any, Dict

from django.conf import settings

from api.models import PersistentAgent
from api.agent.files.filespace_service import write_bytes_to_dir
from api.agent.files.attachment_helpers import build_signed_filespace_download_url
from api.agent.tools.file_export_helpers import resolve_export_target
from api.agent.tools.agent_variables import set_agent_variable

DISALLOWED_EXPORT_HINTS = {
    "csv": "Use create_csv to write CSV files.",
    "pdf": "Use create_pdf to generate PDFs from HTML.",
}


def _normalize_mime_type(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    return cleaned


def _mime_type_base(mime_type: str) -> str:
    return mime_type.split(";", 1)[0].strip().lower()


def _infer_extension(file_path: str, mime_type: str) -> str:
    extension = os.path.splitext(file_path)[1].lower()
    if extension:
        return extension
    guessed = mimetypes.guess_extension(mime_type) or ""
    return guessed.lower()


def _blocked_export_hint(file_path: str, mime_type: str) -> str | None:
    extension = os.path.splitext(file_path)[1].lower()
    if extension in (".csv", ".pdf"):
        return DISALLOWED_EXPORT_HINTS.get(extension.lstrip("."))
    if "csv" in mime_type:
        return DISALLOWED_EXPORT_HINTS["csv"]
    if "pdf" in mime_type:
        return DISALLOWED_EXPORT_HINTS["pdf"]
    return None


def get_create_file_tool() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "create_file",
            "description": (
                "Create a file from raw content and store it in the agent filespace. "
                "Useful for plain text, markup, json, etc. "
                "Recommended path: /exports/your-file.extension "
                "Provide a MIME type that matches the text content. "
                "Use create_csv for CSV files and create_pdf for PDFs. "
                "Returns `file`, `inline`, `inline_html`, and `attach` with variable placeholders."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "Raw text content to write to the file."},
                    "mime_type": {
                        "type": "string",
                        "description": "MIME type for the content (e.g. text/plain, application/json).",
                    },
                    "file_path": {
                        "type": "string",
                        "description": (
                            "Required filespace path (recommended: /exports/report.txt). "
                            "If no extension is provided, one may be inferred from mime_type. "
                            "Use overwrite=true to replace an existing file at that path."
                        ),
                    },
                    "overwrite": {
                        "type": "boolean",
                        "description": "When true, overwrites the existing file at that path.",
                    },
                },
                "required": ["content", "file_path", "mime_type"],
            },
        },
    }


def execute_create_file(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    content = params.get("content")
    if not isinstance(content, str) or not content.strip():
        return {"status": "error", "message": "Missing required parameter: content"}

    mime_type_raw = _normalize_mime_type(params.get("mime_type"))
    if mime_type_raw is None:
        return {"status": "error", "message": "Missing required parameter: mime_type"}

    path, overwrite, error = resolve_export_target(params)
    if error:
        return error

    mime_type = _mime_type_base(mime_type_raw)
    hint = _blocked_export_hint(path, mime_type)
    if hint:
        return {"status": "error", "message": hint}

    content_bytes = content.encode("utf-8")
    max_size = getattr(settings, "MAX_FILE_SIZE", None)
    if max_size and len(content_bytes) > max_size:
        return {
            "status": "error",
            "message": (
                "File content exceeds maximum allowed size "
                f"({len(content_bytes)} bytes > {max_size} bytes)."
            ),
        }

    extension = _infer_extension(path, mime_type)
    result = write_bytes_to_dir(
        agent=agent,
        content_bytes=content_bytes,
        extension=extension,
        mime_type=mime_type,
        path=path,
        overwrite=overwrite,
    )
    if result.get("status") != "ok":
        return result

    file_path = result.get("path")
    node_id = result.get("node_id")
    signed_url = build_signed_filespace_download_url(
        agent_id=str(agent.id),
        node_id=node_id,
    )
    set_agent_variable(file_path, signed_url)

    var_ref = f"$[{file_path}]"
    return {
        "status": "ok",
        "file": var_ref,
        "inline": f"[Download]({var_ref})",
        "inline_html": f"<a href='{var_ref}'>Download</a>",
        "attach": var_ref,
    }
