import csv
import io
from typing import Any, Dict

from api.agent.core.link_references import LinkReferenceResolutionError, link_reference_error_response, resolve_link_references
from api.models import PersistentAgent
from api.agent.tools.file_export_helpers import resolve_export_target, write_agent_export
from .sqlite_query_runner import run_sqlite_select

MAX_EXPORT_ROWS = 5000


def get_create_csv_tool() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "create_csv",
            "description": (
                "Create a CSV file and store it in the agent filespace. "
                "Provide exactly one content source: raw CSV text, or a SQLite SELECT query for data already in SQLite. "
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

    if bool(csv_text) == bool(query):
        return {"status": "error", "message": "Provide exactly one of csv_text or query."}

    path, overwrite, error = resolve_export_target(params, agent_id=agent.id)
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
        table = ([columns] if include_headers and columns else []) + [[row.get(col) for col in columns or []] for row in rows]
    else:
        if not isinstance(csv_text, str) or not csv_text.strip():
            return {"status": "error", "message": "Missing required parameter: csv_text"}
        table = csv.reader(io.StringIO(csv_text))
    try:
        table = [[resolve_link_references(value, agent) if isinstance(value, str) else value for value in row] for row in table]
    except LinkReferenceResolutionError as exc:
        return link_reference_error_response(exc)
    output = io.StringIO()
    csv.writer(output, lineterminator="\n").writerows(table)

    return write_agent_export(
        agent=agent,
        content_bytes=output.getvalue().encode("utf-8"),
        extension=".csv",
        mime_type="text/csv",
        path=path,
        overwrite=overwrite,
        size_label="CSV",
        include_message=True,
        inline=lambda var_ref, _signed_url: f"[Download]({var_ref})",
        inline_html=lambda var_ref, _signed_url: f"<a href='{var_ref}'>Download</a>",
    )
