import hashlib
import json
import logging
import subprocess
import sys
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Dict, Optional

import requests
from django.conf import settings
from django.utils import timezone

from api.models import AgentComputeSession, ComputeSnapshot
from api.services.sandbox_filespace_sync import apply_filespace_push, build_filespace_pull_manifest

logger = logging.getLogger(__name__)


def sandbox_compute_enabled() -> bool:
    return bool(getattr(settings, "SANDBOX_COMPUTE_ENABLED", False))


def _idle_ttl_seconds() -> int:
    return int(getattr(settings, "SANDBOX_COMPUTE_IDLE_TTL_SECONDS", 60 * 60))


def _stdio_max_bytes() -> int:
    return int(getattr(settings, "SANDBOX_COMPUTE_STDIO_MAX_BYTES", 1024 * 1024))


def _python_default_timeout() -> int:
    return int(getattr(settings, "SANDBOX_COMPUTE_PYTHON_DEFAULT_TIMEOUT_SECONDS", 30))


def _python_max_timeout() -> int:
    return int(getattr(settings, "SANDBOX_COMPUTE_PYTHON_MAX_TIMEOUT_SECONDS", 120))


class SandboxComputeUnavailable(RuntimeError):
    pass


@dataclass
class SandboxSessionUpdate:
    state: Optional[str] = None
    pod_name: Optional[str] = None
    namespace: Optional[str] = None
    workspace_snapshot_id: Optional[str] = None


class SandboxComputeBackend:
    def deploy_or_resume(self, agent, session: AgentComputeSession) -> SandboxSessionUpdate:
        raise NotImplementedError

    def run_command(
        self,
        agent,
        session: AgentComputeSession,
        command: str,
        *,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        timeout: Optional[int] = None,
        interactive: bool = False,
    ) -> Dict[str, Any]:
        raise NotImplementedError

    def mcp_request(
        self,
        agent,
        session: AgentComputeSession,
        server_config_id: str,
        tool_name: str,
        params: Dict[str, Any],
        *,
        full_tool_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        raise NotImplementedError

    def tool_request(
        self,
        agent,
        session: AgentComputeSession,
        tool_name: str,
        params: Dict[str, Any],
    ) -> Dict[str, Any]:
        raise NotImplementedError

    def sync_filespace(
        self,
        agent,
        session: AgentComputeSession,
        *,
        direction: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        raise NotImplementedError

    def terminate(self, agent, session: AgentComputeSession, *, reason: str) -> SandboxSessionUpdate:
        raise NotImplementedError

    def discover_mcp_tools(self, server_config_id: str, *, reason: str) -> Dict[str, Any]:
        raise NotImplementedError


class LocalSandboxBackend(SandboxComputeBackend):
    def deploy_or_resume(self, agent, session: AgentComputeSession) -> SandboxSessionUpdate:
        return SandboxSessionUpdate(state=AgentComputeSession.State.RUNNING)

    def run_command(
        self,
        agent,
        session: AgentComputeSession,
        command: str,
        *,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        timeout: Optional[int] = None,
        interactive: bool = False,
    ) -> Dict[str, Any]:
        if not command:
            return {"status": "error", "message": "Command is required."}

        timeout_value = timeout or getattr(settings, "SANDBOX_COMPUTE_RUN_COMMAND_TIMEOUT_SECONDS", 120)
        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=cwd or None,
                env=env or None,
                capture_output=True,
                text=True,
                timeout=timeout_value,
            )
        except subprocess.TimeoutExpired:
            return {"status": "error", "message": "Command timed out."}
        except OSError as exc:
            return {"status": "error", "message": f"Command failed to start: {exc}"}

        stdout, stderr = _truncate_streams(result.stdout or "", result.stderr or "", _stdio_max_bytes())
        payload = {
            "status": "ok" if result.returncode == 0 else "error",
            "exit_code": result.returncode,
            "stdout": stdout,
            "stderr": stderr,
        }
        if result.returncode != 0:
            payload["message"] = "Command exited with non-zero status."
        return payload

    def mcp_request(
        self,
        agent,
        session: AgentComputeSession,
        server_config_id: str,
        tool_name: str,
        params: Dict[str, Any],
        *,
        full_tool_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        from api.agent.tools.mcp_manager import execute_mcp_tool, get_mcp_manager

        if full_tool_name:
            return execute_mcp_tool(agent, full_tool_name, params, force_local=True)

        manager = get_mcp_manager()
        for tool in manager.get_tools_for_agent(agent):
            if tool.config_id == server_config_id and tool.tool_name == tool_name:
                return execute_mcp_tool(agent, tool.full_name, params, force_local=True)
        return {"status": "error", "message": "MCP tool not available."}

    def tool_request(
        self,
        agent,
        session: AgentComputeSession,
        tool_name: str,
        params: Dict[str, Any],
    ) -> Dict[str, Any]:
        if tool_name == "python_exec":
            return _execute_python_exec(params)

        local_exec = _local_tool_executors().get(tool_name)
        if not local_exec:
            return {"status": "error", "message": f"Sandbox tool '{tool_name}' is not available."}
        return local_exec(agent, params)

    def sync_filespace(
        self,
        agent,
        session: AgentComputeSession,
        *,
        direction: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload = payload or {}
        if direction == "push":
            changes = payload.get("changes") or []
            sync_timestamp = payload.get("sync_timestamp")
            return apply_filespace_push(agent, changes, sync_timestamp=sync_timestamp)
        if direction == "pull":
            since = payload.get("since")
            return build_filespace_pull_manifest(agent, since=since)
        return {"status": "error", "message": "Invalid sync direction."}

    def terminate(self, agent, session: AgentComputeSession, *, reason: str) -> SandboxSessionUpdate:
        return SandboxSessionUpdate(state=AgentComputeSession.State.STOPPED)

    def discover_mcp_tools(self, server_config_id: str, *, reason: str) -> Dict[str, Any]:
        from api.agent.tools.mcp_manager import get_mcp_manager

        manager = get_mcp_manager()
        ok = manager.discover_tools_for_server(server_config_id)
        return {"status": "ok" if ok else "error", "reason": reason}


class HttpSandboxBackend(SandboxComputeBackend):
    def __init__(self, base_url: str, token: str):
        if not base_url:
            raise SandboxComputeUnavailable("SANDBOX_COMPUTE_API_URL is required.")
        self.base_url = base_url.rstrip("/")
        self.token = token

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=30)
            response.raise_for_status()
        except requests.RequestException as exc:
            return {"status": "error", "message": str(exc)}
        try:
            return response.json()
        except ValueError:
            return {"status": "error", "message": "Invalid JSON response from sandbox API."}

    def deploy_or_resume(self, agent, session: AgentComputeSession) -> SandboxSessionUpdate:
        payload = {"agent_id": str(agent.id)}
        response = self._post("sandbox/compute/deploy_or_resume", payload)
        return _session_update_from_response(response)

    def run_command(
        self,
        agent,
        session: AgentComputeSession,
        command: str,
        *,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        timeout: Optional[int] = None,
        interactive: bool = False,
    ) -> Dict[str, Any]:
        payload = {
            "agent_id": str(agent.id),
            "command": command,
            "cwd": cwd,
            "env": env,
            "timeout": timeout,
            "interactive": interactive,
        }
        return self._post("sandbox/compute/run_command", payload)

    def mcp_request(
        self,
        agent,
        session: AgentComputeSession,
        server_config_id: str,
        tool_name: str,
        params: Dict[str, Any],
        *,
        full_tool_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload = {
            "agent_id": str(agent.id),
            "server_id": server_config_id,
            "tool_name": tool_name,
            "params": params,
        }
        return self._post("sandbox/compute/mcp_request", payload)

    def tool_request(
        self,
        agent,
        session: AgentComputeSession,
        tool_name: str,
        params: Dict[str, Any],
    ) -> Dict[str, Any]:
        payload = {
            "agent_id": str(agent.id),
            "tool_name": tool_name,
            "params": params,
        }
        return self._post("sandbox/compute/tool_request", payload)

    def sync_filespace(
        self,
        agent,
        session: AgentComputeSession,
        *,
        direction: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload = payload or {}
        payload.update({"agent_id": str(agent.id), "direction": direction})
        return self._post("sandbox/compute/sync_filespace", payload)

    def terminate(self, agent, session: AgentComputeSession, *, reason: str) -> SandboxSessionUpdate:
        payload = {"agent_id": str(agent.id), "reason": reason}
        response = self._post("sandbox/compute/terminate", payload)
        return _session_update_from_response(response)

    def discover_mcp_tools(self, server_config_id: str, *, reason: str) -> Dict[str, Any]:
        payload = {"server_id": server_config_id, "reason": reason}
        return self._post("sandbox/compute/discover_mcp_tools", payload)


def _session_update_from_response(response: Dict[str, Any]) -> SandboxSessionUpdate:
    return SandboxSessionUpdate(
        state=response.get("state"),
        pod_name=response.get("pod_name"),
        namespace=response.get("namespace"),
        workspace_snapshot_id=response.get("workspace_snapshot_id"),
    )


def _resolve_backend() -> SandboxComputeBackend:
    backend_name = str(getattr(settings, "SANDBOX_COMPUTE_BACKEND", "") or "").lower()
    if backend_name in ("http", "remote"):
        return HttpSandboxBackend(
            getattr(settings, "SANDBOX_COMPUTE_API_URL", ""),
            getattr(settings, "SANDBOX_COMPUTE_API_TOKEN", ""),
        )
    return LocalSandboxBackend()


def _truncate_streams(stdout: str, stderr: str, max_bytes: int) -> tuple[str, str]:
    stdout_bytes = stdout.encode("utf-8")
    stderr_bytes = stderr.encode("utf-8")
    total = len(stdout_bytes) + len(stderr_bytes)
    if total <= max_bytes:
        return stdout, stderr

    remaining = max_bytes
    truncated_stdout = stdout_bytes[:remaining]
    remaining -= len(truncated_stdout)
    truncated_stderr = stderr_bytes[:remaining]
    return (
        truncated_stdout.decode("utf-8", errors="ignore"),
        truncated_stderr.decode("utf-8", errors="ignore"),
    )


def _normalize_timeout(value: Any, *, default: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    if parsed <= 0:
        parsed = default
    return min(parsed, maximum)


def _execute_python_exec(params: Dict[str, Any]) -> Dict[str, Any]:
    code = params.get("code")
    if not isinstance(code, str) or not code.strip():
        return {"status": "error", "message": "Missing required parameter: code"}

    timeout_value = _normalize_timeout(
        params.get("timeout_seconds"),
        default=_python_default_timeout(),
        maximum=_python_max_timeout(),
    )

    try:
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=timeout_value,
        )
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "Python execution timed out."}
    except OSError as exc:
        return {"status": "error", "message": f"Python execution failed to start: {exc}"}

    stdout, stderr = _truncate_streams(result.stdout or "", result.stderr or "", _stdio_max_bytes())
    payload = {
        "status": "ok" if result.returncode == 0 else "error",
        "exit_code": result.returncode,
        "stdout": stdout,
        "stderr": stderr,
    }
    if result.returncode != 0:
        payload["message"] = "Python exited with non-zero status."
    return payload


def _local_tool_executors() -> Dict[str, Any]:
    from api.agent.tools.create_file import execute_create_file
    from api.agent.tools.create_csv import execute_create_csv
    from api.agent.tools.create_pdf import execute_create_pdf
    from api.agent.tools.create_chart import execute_create_chart

    return {
        "create_file": execute_create_file,
        "create_csv": execute_create_csv,
        "create_pdf": execute_create_pdf,
        "create_chart": execute_create_chart,
    }


class SandboxComputeService:
    def __init__(self, backend: Optional[SandboxComputeBackend] = None):
        if not sandbox_compute_enabled():
            raise SandboxComputeUnavailable("Sandbox compute is disabled.")
        self._backend = backend or _resolve_backend()

    def _touch_session(self, session: AgentComputeSession, *, source: str) -> None:
        now = timezone.now()
        session.last_activity_at = now
        session.lease_expires_at = now + timedelta(seconds=_idle_ttl_seconds())
        session.updated_at = now
        session.save(update_fields=["last_activity_at", "lease_expires_at", "updated_at"])
        logger.debug("Sandbox session touched agent=%s source=%s", session.agent_id, source)

    def _apply_session_update(self, session: AgentComputeSession, update: SandboxSessionUpdate) -> None:
        if update.state:
            session.state = update.state
        if update.pod_name is not None:
            session.pod_name = update.pod_name or ""
        if update.namespace is not None:
            session.namespace = update.namespace or ""
        if update.workspace_snapshot_id:
            snapshot = ComputeSnapshot.objects.filter(id=update.workspace_snapshot_id).first()
            if snapshot:
                session.workspace_snapshot = snapshot
        session.save(update_fields=["state", "pod_name", "namespace", "workspace_snapshot", "updated_at"])

    def _ensure_session(self, agent, *, source: str) -> AgentComputeSession:
        session, _created = AgentComputeSession.objects.get_or_create(
            agent=agent,
            defaults={"state": AgentComputeSession.State.STOPPED},
        )
        if session.state != AgentComputeSession.State.RUNNING:
            update = self._backend.deploy_or_resume(agent, session)
            if not update.state:
                update.state = AgentComputeSession.State.RUNNING
            self._apply_session_update(session, update)
        self._touch_session(session, source=source)
        return session

    def deploy_or_resume(self, agent, *, reason: str = "") -> AgentComputeSession:
        session = self._ensure_session(agent, source="deploy_or_resume")
        if reason:
            logger.info("Sandbox session deploy_or_resume agent=%s reason=%s", agent.id, reason)
        return session

    def run_command(
        self,
        agent,
        command: str,
        *,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        timeout: Optional[int] = None,
        interactive: bool = False,
    ) -> Dict[str, Any]:
        session = self._ensure_session(agent, source="run_command")
        result = self._backend.run_command(
            agent,
            session,
            command,
            cwd=cwd,
            env=env,
            timeout=timeout,
            interactive=interactive,
        )
        return result

    def mcp_request(
        self,
        agent,
        server_config_id: str,
        tool_name: str,
        params: Dict[str, Any],
        *,
        full_tool_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        session = self._ensure_session(agent, source="mcp_request")
        result = self._backend.mcp_request(
            agent,
            session,
            server_config_id,
            tool_name,
            params,
            full_tool_name=full_tool_name,
        )
        _log_tool_call("mcp_request", tool_name, params, agent_id=str(agent.id))
        return result

    def tool_request(self, agent, tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        session = self._ensure_session(agent, source="tool_request")
        result = self._backend.tool_request(agent, session, tool_name, params)
        _log_tool_call("tool_request", tool_name, params, agent_id=str(agent.id))
        return result

    def sync_filespace(
        self,
        agent,
        *,
        direction: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        session = self._ensure_session(agent, source="sync_filespace")
        result = self._backend.sync_filespace(agent, session, direction=direction, payload=payload)
        return result

    def terminate(self, agent, *, reason: str) -> AgentComputeSession:
        session = AgentComputeSession.objects.filter(agent=agent).first()
        if not session:
            raise SandboxComputeUnavailable("Sandbox session not found.")
        update = self._backend.terminate(agent, session, reason=reason)
        self._apply_session_update(session, update)
        return session

    def discover_mcp_tools(self, server_config_id: str, *, reason: str) -> Dict[str, Any]:
        result = self._backend.discover_mcp_tools(server_config_id, reason=reason)
        return result


def _log_tool_call(event: str, tool_name: str, params: Dict[str, Any], *, agent_id: str) -> None:
    try:
        serialized = json.dumps(params or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        params_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    except (TypeError, ValueError):
        params_hash = "unhashable"
    logger.info(
        "Sandbox %s agent=%s tool=%s params_hash=%s",
        event,
        agent_id,
        tool_name,
        params_hash,
    )
