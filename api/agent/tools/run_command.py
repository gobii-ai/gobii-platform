from typing import Any, Dict, Optional

from api.agent.tools.sqlite_state import agent_sqlite_db, get_sqlite_db_path
from api.models import PersistentAgent
from api.services.agent_sqlite_coordination import AgentSQLiteBusy, agent_sqlite_busy_result
from api.services.sandbox_compute import SandboxComputeService, SandboxComputeUnavailable, track_sandbox_unavailable


def get_run_command_tool() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": (
                "Run a non-interactive shell command inside the agent's sandboxed workspace. "
                "Use for one-shot commands that should complete and return stdout/stderr. "
                "Sandbox proxy env vars and sandbox env_var secrets are already present in the command environment. "
                "For ad hoc Python with third-party deps, prefer `uv run --no-project ...`. "
                "The command runs in a shell where the workspace root is /workspace. "
                "Use $GOBII_SCRATCH_DIR for temporary working files that should not persist or sync to filespace. "
                "Clone repositories under $GOBII_REPO_WORKDIR (for example, `git clone <url> $GOBII_REPO_WORKDIR/repo-name`). "
                "Gobii filespace paths like /tools/foo.py or /reports/foo.txt are for Gobii tool arguments, not shell paths. "
                "Inside command strings, use relative paths from the workspace root such as tools/foo.py or reports/foo.txt, "
                "or absolute shell paths like /workspace/tools/foo.py. Do not run /tools/foo.py or /reports/foo.txt directly. "
                "The shared agent SQLite database is available at $GOBII_AGENT_SQLITE_PATH and may be queried or updated "
                "with sqlite3 or another SQLite client."
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
        track_sandbox_unavailable(agent, request_source="run_command")
        return {"status": "error", "message": str(exc)}

    current_db_path = get_sqlite_db_path()
    if current_db_path:
        return service.run_command(
            agent,
            command,
            cwd=cwd,
            env=env,
            timeout=timeout,
            interactive=False,
            local_sqlite_db_path=current_db_path,
        )

    try:
        with agent_sqlite_db(str(agent.id)) as db_path:
            return service.run_command(
                agent,
                command,
                cwd=cwd,
                env=env,
                timeout=timeout,
                interactive=False,
                local_sqlite_db_path=db_path,
            )
    except AgentSQLiteBusy as exc:
        return agent_sqlite_busy_result(exc)
