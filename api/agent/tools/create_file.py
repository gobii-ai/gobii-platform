import mimetypes
import os
from typing import Any, Dict

from api.agent.core.link_references import (
    LinkReferenceResolutionError, document_mime_supports_link_references, link_reference_error_response,
    resolve_link_reference_params, resolve_link_references,
)
from api.models import PersistentAgent
from api.agent.tools.file_export_helpers import resolve_export_target, write_agent_export
from api.agent.tools.sqlite_query_runner import run_sqlite_select

DISALLOWED_EXPORT_HINTS = {
    "csv": "Use create_csv to write CSV files.",
    "pdf": "Use create_pdf to generate PDFs from HTML.",
}


def _normalize_mime_type(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _mime_type_base(mime_type: str) -> str:
    return mime_type.split(";", 1)[0].strip().lower()


def _infer_extension(file_path: str, mime_type: str) -> str:
    return (os.path.splitext(file_path)[1] or mimetypes.guess_extension(mime_type) or "").lower()


def _blocked_export_hint(file_path: str, mime_type: str) -> str | None:
    extension = os.path.splitext(file_path)[1].lower()
    if extension in (".csv", ".pdf"):
        return DISALLOWED_EXPORT_HINTS.get(extension.lstrip("."))
    kind = next((name for name in DISALLOWED_EXPORT_HINTS if name in mime_type), None)
    return DISALLOWED_EXPORT_HINTS.get(kind)


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
                "Use `file_path`, `mime_type`, and exactly one of `content` or `query`; do not use `path`, `filename`, or `text`. "
                "For raw exports, provide content. For query exports, provide a SQLite SELECT/WITH query that "
                "returns exactly one row and one column; that single value becomes the file content. "
                "Recommended path: /exports/your-file.extension. For custom tool source files, use "
                "`file_path='/tools/my_tool.py'`, `mime_type='text/x-python'`, and `content=<python source>`. "
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

    path, overwrite, error = resolve_export_target(params, agent_id=agent.id)
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

    try:
        if document_mime_supports_link_references(mime_type):
            content_to_write = resolve_link_references(content_to_write, agent)
        else:
            resolve_link_reference_params({"content": content_to_write, "mime_type": mime_type}, agent, tool_name="create_file")
    except LinkReferenceResolutionError as exc:
        return link_reference_error_response(exc)

    extension = _infer_extension(path, mime_type)
    result = write_agent_export(
        agent=agent,
        content_bytes=content_to_write.encode("utf-8"),
        extension=extension,
        mime_type=mime_type,
        path=path,
        overwrite=overwrite,
        size_label="File content",
        include_message=True,
        inline=lambda var_ref, _signed_url: f"[Download]({var_ref})",
        inline_html=lambda var_ref, _signed_url: f"<a href='{var_ref}'>Download</a>",
    )
    return result
