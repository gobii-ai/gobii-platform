from typing import Any, Dict


def get_python_exec_tool() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "python_exec",
            "description": (
                "Execute a Python script file inside the agent's sandboxed compute session. "
                "Use for script-based data transforms and calculations. "
                "Supports a timeout (default 30s, max 120s)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Workspace-relative path to the Python script file to execute.",
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "Optional timeout in seconds (max 120).",
                    },
                },
                "required": ["file_path"],
            },
        },
    }
