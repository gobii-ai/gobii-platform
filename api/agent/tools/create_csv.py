from typing import Any, Dict

from api.models import PersistentAgent
from .filespace_writer import write_bytes_to_exports

DEFAULT_FILENAME = "export.csv"
EXTENSION = ".csv"
MIME_TYPE = "text/csv"


def get_create_csv_tool() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "create_csv",
            "description": (
                "Create a CSV file from provided CSV text and store it in the agent filespace "
                "under /exports. Provide the full CSV content, including headers if needed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "csv_text": {"type": "string", "description": "CSV content to write to the file."},
                    "filename": {"type": "string", "description": "Optional output filename (defaults to export.csv)."},
                },
                "required": ["csv_text"],
            },
        },
    }


def execute_create_csv(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    csv_text = params.get("csv_text")
    if not isinstance(csv_text, str) or not csv_text.strip():
        return {"status": "error", "message": "Missing required parameter: csv_text"}

    filename = params.get("filename")
    if filename is not None and not isinstance(filename, str):
        return {"status": "error", "message": "filename must be a string when provided"}

    content_bytes = csv_text.encode("utf-8")
    return write_bytes_to_exports(
        agent=agent,
        content_bytes=content_bytes,
        filename=filename,
        fallback_name=DEFAULT_FILENAME,
        extension=EXTENSION,
        mime_type=MIME_TYPE,
    )
