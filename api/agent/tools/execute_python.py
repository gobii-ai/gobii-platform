import base64
from typing import Any, Dict

from api.models import PersistentAgent
from api.services.compute_control import run_command
from api.services.sandbox_access import has_sandbox_access
from api.services.sandbox_k8s import SandboxK8sError


def get_execute_python_tool() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "execute_python",
            "description": (
                "Execute Python code inside the agent's sandbox compute pod. "
                "Only available to agents whose owners have sandbox access enabled."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Python source code to execute.",
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Optional working directory inside the sandbox (default: /workspace).",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Optional timeout in seconds (max 120).",
                    },
                },
                "required": ["code"],
            },
        },
    }


def _build_python_command(code: str) -> str:
    encoded = base64.b64encode(code.encode("utf-8")).decode("ascii")
    return (
        "python -c 'import base64; "
        "exec(compile(base64.b64decode(\"{}\"), \"sandbox\", \"exec\"))'"
    ).format(encoded)


def execute_execute_python(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    if not has_sandbox_access(agent.user):
        return {
            "status": "error",
            "message": "Sandbox access is not enabled for this account.",
        }

    code = params.get("code")
    if not isinstance(code, str) or not code.strip():
        return {"status": "error", "message": "Missing required parameter: code"}

    cwd = params.get("cwd")
    if cwd is not None and not isinstance(cwd, str):
        return {"status": "error", "message": "cwd must be a string"}

    timeout = params.get("timeout")
    if timeout is not None and not isinstance(timeout, int):
        return {"status": "error", "message": "timeout must be an integer"}

    command = _build_python_command(code)

    try:
        result = run_command(
            agent,
            command=command,
            cwd=cwd or None,
            timeout_seconds=timeout,
        )
    except SandboxK8sError as exc:
        return {"status": "error", "message": str(exc)}

    return {
        "status": "ok",
        "stdout": result.get("stdout", ""),
        "stderr": result.get("stderr", ""),
        "exit_code": result.get("exit_code", 0),
        "duration_seconds": result.get("duration_seconds"),
    }
