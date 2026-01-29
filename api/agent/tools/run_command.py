from typing import Any, Dict, Optional

from api.models import PersistentAgent
from api.services.sandbox_compute import SandboxComputeService, SandboxComputeUnavailable


def get_run_command_tool() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": (
                "Run a non-interactive shell command inside the agent's sandboxed workspace. "
                "Use for one-shot commands that should complete and return stdout/stderr. "
                "The workspace root is /workspace; filespace paths like /reports/foo.txt map to "
                "/workspace/reports/foo.txt. Avoid using /workspace in paths."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to execute (non-interactive).",
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Optional working directory relative to the workspace root (e.g., /reports).",
                    },
                    "env": {
                        "type": "object",
                        "description": "Optional environment variables for the command.",
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "Optional timeout in seconds.",
                    },
                },
                "required": ["command"],
            },
        },
    }


def execute_run_command(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    command = params.get("command")
    if not isinstance(command, str) or not command.strip():
        return {"status": "error", "message": "Missing required parameter: command"}

    cwd = params.get("cwd")
    if not isinstance(cwd, str) or not cwd.strip():
        cwd = None

    env = params.get("env")
    if not isinstance(env, dict):
        env = None

    timeout: Optional[int] = None
    timeout_raw = params.get("timeout_seconds")
    if isinstance(timeout_raw, int) and timeout_raw > 0:
        timeout = timeout_raw
    elif isinstance(timeout_raw, str) and timeout_raw.strip():
        try:
            parsed = int(timeout_raw)
        except ValueError:
            parsed = 0
        if parsed > 0:
            timeout = parsed

    try:
        service = SandboxComputeService()
    except SandboxComputeUnavailable as exc:
        return {"status": "error", "message": str(exc)}

    return service.run_command(
        agent,
        command,
        cwd=cwd,
        env=env,
        timeout=timeout,
        interactive=False,
    )
