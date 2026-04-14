import mimetypes
import os
from typing import Any, Dict

from api.models import PersistentAgent
from api.agent.files.filespace_service import write_bytes_to_dir
from api.agent.files.attachment_helpers import build_signed_filespace_download_url
from api.agent.tools.attachment_guidance import build_attachment_result_message
from api.agent.tools.file_export_helpers import resolve_export_target
from api.agent.tools.agent_variables import set_agent_variable
from api.agent.tools.sqlite_query_runner import run_sqlite_select
from api.services.system_settings import get_max_file_size

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


def _coerce_query_scalar(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Query returned binary data that is not valid UTF-8 text.") from exc
    return str(value)


def get_create_file_tool() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "create_file",
            "description": (
                "Create a file from raw content or a SQLite query result and store it in the agent filespace. "
                "Provide exactly one of content or query. "
                "For raw exports, provide content. For query exports, provide a SQLite SELECT/WITH query that "
                "returns exactly one row and one column; that single value becomes the file content. "
                "Recommended path: /exports/your-file.extension "
                "Provide a MIME type that matches the text content. "
                "Use create_csv for CSV/tabular exports and create_pdf for PDFs. "
                "Returns `file`, `inline`, `inline_html`, and `attach` with variable placeholders."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "Raw text content to write to the file."},
                    "query": {
                        "type": "string",
                        "description": (
                            "SQLite SELECT or WITH query to export instead of raw content. "
                            "The query must return exactly one row and one column; that single value is written directly."
                        ),
                    },
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
                "required": ["file_path", "mime_type"],
                "oneOf": [
                    {"required": ["content"]},
                    {"required": ["query"]},
                ],
            },
        },
    }


def execute_create_file(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    content = params.get("content")
    query = params.get("query")

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

    has_content = isinstance(content, str) and bool(content.strip())
    has_query = isinstance(query, str) and bool(query.strip())
    if has_content and has_query:
        return {"status": "error", "message": "Use content OR query, not both."}
    if not has_content and not has_query:
        return {"status": "error", "message": "Provide exactly one of content or query."}

    if has_query:
        rows, columns, err = run_sqlite_select(query)
        if err:
            return {"status": "error", "message": err}
        if len(rows) != 1 or len(columns or []) != 1:
            return {
                "status": "error",
                "message": (
                    "Query must return exactly 1 row and 1 column for create_file query exports."
                ),
            }
        try:
            content_to_write = _coerce_query_scalar(rows[0].get(columns[0]))
        except ValueError as exc:
            return {"status": "error", "message": str(exc)}
    else:
        content_to_write = content

    content_bytes = content_to_write.encode("utf-8")
    max_size = get_max_file_size()
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
        "message": build_attachment_result_message(var_ref),
        "file": var_ref,
        "inline": f"[Download]({var_ref})",
        "inline_html": f"<a href='{var_ref}'>Download</a>",
        "attach": var_ref,
    }
