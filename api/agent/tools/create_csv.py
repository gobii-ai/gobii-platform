from typing import Any, Dict

from django.conf import settings

from api.models import PersistentAgent
from api.agent.files.filespace_service import write_bytes_to_dir
from api.agent.files.attachment_helpers import build_signed_filespace_download_url
from api.agent.tools.file_export_helpers import resolve_export_target
from api.agent.tools.agent_variables import set_agent_variable

EXTENSION = ".csv"
MIME_TYPE = "text/csv"


def get_create_csv_tool() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "create_csv",
            "description": (
                "Create a CSV file from provided CSV text and store it in the agent filespace. "
                "Recommended path: /exports/your-file.csv. Provide the full CSV content, including headers if needed. "
                "Returns `file`, `inline`, `inline_html`, and `attach` with variable placeholders."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "csv_text": {"type": "string", "description": "CSV content to write to the file."},
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
                },
                "required": ["csv_text", "file_path"],
            },
        },
    }


def execute_create_csv(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    csv_text = params.get("csv_text")
    if not isinstance(csv_text, str) or not csv_text.strip():
        return {"status": "error", "message": "Missing required parameter: csv_text"}

    path, overwrite, error = resolve_export_target(params)
    if error:
        return error

    content_bytes = csv_text.encode("utf-8")
    max_size = getattr(settings, "MAX_FILE_SIZE", None)
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

    var_ref = f"«{file_path}»"
    return {
        "status": "ok",
        "file": var_ref,
        "inline": f"[Download]({var_ref})",
        "inline_html": f"<a href='{var_ref}'>Download</a>",
        "attach": var_ref,
    }
