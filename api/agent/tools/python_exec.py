from typing import Any, Dict


def get_python_exec_tool() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "python_exec",
            "description": (
                "Execute Python code inside the agent's sandboxed compute session. "
                "Use for quick scripts, data transforms, and calculations. "
                "Supports a timeout (default 30s, max 120s)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Python source code to execute.",
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "Optional timeout in seconds (max 120).",
                    },
                },
                "required": ["code"],
            },
        },
    }
