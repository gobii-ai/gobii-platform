from typing import Any, Dict


def get_python_exec_tool() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "python_exec",
            "description": (
                "Execute Python code inside the agent's sandboxed compute session. "
                "Use for quick scripts, data transforms, and calculations. "
                "Sandbox proxy env vars and sandbox env_var secrets are already available via os.environ. "
                "The shared agent SQLite database path is available in os.environ['GOBII_AGENT_SQLITE_PATH'] "
                "and may be queried or updated with Python's sqlite3 module. "
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
