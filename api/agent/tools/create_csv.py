import csv
import io
from typing import Any, Dict

from api.models import PersistentAgent
from api.agent.files.filespace_service import write_bytes_to_dir
from api.agent.files.attachment_helpers import build_signed_filespace_download_url
from api.agent.tools.file_export_helpers import resolve_export_target
from api.agent.tools.agent_variables import set_agent_variable
from api.services.system_settings import get_max_file_size
from .sqlite_query_runner import run_sqlite_select

EXTENSION = ".csv"
MIME_TYPE = "text/csv"
MAX_EXPORT_ROWS = 5000


def get_create_csv_tool() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "create_csv",
            "description": (
                "Create a CSV file and store it in the agent filespace. "
                "Provide either raw CSV text or a SQLite SELECT query to export query results. "
                "Recommended path: /exports/your-file.csv. Returns `file`, `inline`, `inline_html`, and `attach`."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "csv_text": {
                        "type": "string",
                        "description": "CSV content to write to the file (use instead of query).",
                    },
                    "query": {
                        "type": "string",
                        "description": "SQLite SELECT to export. Optional; mutually exclusive with csv_text.",
                    },
                    "file_path": {
                        "type": "string",
                        "description": (
                            "Required filespace path (recommended: /exports/report.csv). "
                            "Use overwrite=true to replace an existing file at that path."
                        ),
                    },
                    "overwrite": {
                        "type": "boolean",
                        "description": "When true, overwrites the existing file at that path.",
                    },
                    "include_headers": {
                        "type": "boolean",
                        "description": "Include column headers in query exports (default: true).",
                    },
                },
                "required": ["file_path"],
            },
        },
    }


def execute_create_csv(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    csv_text = params.get("csv_text")
    query = params.get("query")

    if not csv_text and not query:
        return {"status": "error", "message": "Provide either csv_text or query."}
    if csv_text and query:
        return {"status": "error", "message": "Use csv_text OR query, not both."}

    path, overwrite, error = resolve_export_target(params)
    if error:
        return error

    if query:
        include_headers = bool(params.get("include_headers", True))
        rows, columns, err = run_sqlite_select(query)
        if err:
            return {"status": "error", "message": err}
        if len(rows) > MAX_EXPORT_ROWS:
            return {
                "status": "error",
                "message": f"Result has {len(rows)} rows; capped at {MAX_EXPORT_ROWS}. Add LIMIT to your query.",
            }
        output = io.StringIO()
        writer = csv.writer(output)
        if include_headers and columns:
            writer.writerow(columns)
        for row in rows:
            writer.writerow([row.get(col) for col in columns or []])
        csv_text_to_write = output.getvalue()
    else:
        if not isinstance(csv_text, str) or not csv_text.strip():
            return {"status": "error", "message": "Missing required parameter: csv_text"}
        csv_text_to_write = csv_text

    content_bytes = csv_text_to_write.encode("utf-8")
    max_size = get_max_file_size()
    if max_size and len(content_bytes) > max_size:
        return {
            "status": "error",
            "message": (
                f"CSV exceeds maximum allowed size ({len(content_bytes)} bytes > {max_size} bytes)."
            ),
        }
    result = write_bytes_to_dir(
        agent=agent,
        content_bytes=content_bytes,
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

    var_ref = f"$[{file_path}]"
    return {
        "status": "ok",
        "file": var_ref,
        "inline": f"[Download]({var_ref})",
        "inline_html": f"<a href='{var_ref}'>Download</a>",
        "attach": var_ref,
    }
