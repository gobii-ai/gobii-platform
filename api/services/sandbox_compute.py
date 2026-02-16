import hashlib
import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Dict, Optional

import requests
from django.conf import settings
from django.utils.dateparse import parse_datetime
from django.db import DatabaseError
from django.utils import timezone

from api.models import AgentComputeSession, ComputeSnapshot, PersistentAgent, MCPServerConfig
from api.proxy_selection import select_proxy, select_proxy_for_persistent_agent
from api.services.mcp_tool_cache import set_cached_mcp_tool_definitions
from api.services.sandbox_filespace_sync import apply_filespace_push, build_filespace_pull_manifest
from api.services.system_settings import get_sandbox_compute_enabled, get_sandbox_compute_require_proxy
from api.sandbox_utils import monotonic_elapsed_ms as _elapsed_ms, normalize_timeout as _normalize_timeout
from waffle import get_waffle_flag_model

logger = logging.getLogger(__name__)


SANDBOX_COMPUTE_WAFFLE_FLAG = "sandbox_compute"


def sandbox_compute_enabled() -> bool:
    return get_sandbox_compute_enabled()


def sandbox_compute_enabled_for_agent(agent: Optional[PersistentAgent]) -> bool:
    if not sandbox_compute_enabled():
        return False
    if agent is None:
        return False
    if not getattr(agent, "user_id", None):
        return False

    try:
        flag = get_waffle_flag_model().get(SANDBOX_COMPUTE_WAFFLE_FLAG)
    except Exception:
        logger.exception(
            "Failed loading waffle flag '%s' when evaluating sandbox eligibility for agent %s",
            SANDBOX_COMPUTE_WAFFLE_FLAG,
            getattr(agent, "id", None),
        )
        return False

    try:
        return bool(flag.is_active_for_user(agent.user))
    except Exception:
        logger.exception(
            "Error while evaluating waffle flag '%s' for user %s (agent %s)",
            SANDBOX_COMPUTE_WAFFLE_FLAG,
            getattr(agent, "user_id", None),
            getattr(agent, "id", None),
        )
        return False


def _idle_ttl_seconds() -> int:
    return int(getattr(settings, "SANDBOX_COMPUTE_IDLE_TTL_SECONDS", 60 * 60))


def _stdio_max_bytes() -> int:
    return int(getattr(settings, "SANDBOX_COMPUTE_STDIO_MAX_BYTES", 1024 * 1024))


def _http_timeout_seconds() -> int:
    return int(getattr(settings, "SANDBOX_COMPUTE_HTTP_TIMEOUT_SECONDS", 180))


def _run_command_timeout_seconds() -> int:
    return int(getattr(settings, "SANDBOX_COMPUTE_RUN_COMMAND_TIMEOUT_SECONDS", 120))


def _python_default_timeout() -> int:
    return int(getattr(settings, "SANDBOX_COMPUTE_PYTHON_DEFAULT_TIMEOUT_SECONDS", 30))


def _python_max_timeout() -> int:
    return int(getattr(settings, "SANDBOX_COMPUTE_PYTHON_MAX_TIMEOUT_SECONDS", 120))


def _sync_on_tool_call() -> bool:
    return bool(getattr(settings, "SANDBOX_COMPUTE_SYNC_ON_TOOL_CALL", True))


def _sync_on_mcp_call() -> bool:
    return bool(getattr(settings, "SANDBOX_COMPUTE_SYNC_ON_MCP_CALL", True))


def _sync_on_run_command() -> bool:
    return bool(getattr(settings, "SANDBOX_COMPUTE_SYNC_ON_RUN_COMMAND", False))


def _proxy_required() -> bool:
    return get_sandbox_compute_require_proxy()


def _no_proxy_value() -> str:
    return str(getattr(settings, "SANDBOX_COMPUTE_NO_PROXY", "") or "").strip()


def _allowed_env_keys() -> set[str]:
    keys = getattr(settings, "SANDBOX_COMPUTE_ALLOWED_ENV_KEYS", None)
    if isinstance(keys, (list, tuple, set)):
        return {str(key) for key in keys if str(key)}
    return {
        "PATH",
        "HOME",
        "USER",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TMPDIR",
        "TERM",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "PYTHONUNBUFFERED",
        "PYTHONIOENCODING",
    }


def _sanitize_env(extra_env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    allowed = _allowed_env_keys()
    env = {key: value for key, value in os.environ.items() if key in allowed}
    env.setdefault("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
    if extra_env:
        for key, value in extra_env.items():
            if key in allowed or str(key).startswith("SANDBOX_"):
                env[str(key)] = str(value)
    return env


def _stderr_summary(stderr: str) -> str:
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    if not lines:
        return ""
    return lines[-1]


def _build_nonzero_exit_error_payload(
    *,
    process_name: str,
    exit_code: int,
    stdout: str,
    stderr: str,
) -> Dict[str, Any]:
    summary = _stderr_summary(stderr)
    payload: Dict[str, Any] = {
        "status": "error",
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "message": summary or f"{process_name} exited with status {exit_code}.",
    }
    if stderr.strip():
        payload["detail"] = stderr
    return payload


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
        server_payload: Optional[Dict[str, Any]] = None,
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

    def snapshot_workspace(self, agent, session: AgentComputeSession, *, reason: str) -> Dict[str, Any]:
        raise NotImplementedError

    def terminate(
        self,
        agent,
        session: AgentComputeSession,
        *,
        reason: str,
        delete_workspace: bool = False,
    ) -> SandboxSessionUpdate:
        raise NotImplementedError

    def discover_mcp_tools(
        self,
        server_config_id: str,
        *,
        reason: str,
        server_payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
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
        if interactive:
            return {"status": "error", "message": "Interactive sessions are not supported yet."}

        timeout_value = timeout or getattr(settings, "SANDBOX_COMPUTE_RUN_COMMAND_TIMEOUT_SECONDS", 120)
        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=cwd or None,
                env=_sanitize_env(env),
                capture_output=True,
                text=True,
                timeout=timeout_value,
            )
        except subprocess.TimeoutExpired:
            return {"status": "error", "message": "Command timed out."}
        except OSError as exc:
            return {"status": "error", "message": f"Command failed to start: {exc}"}

        stdout, stderr = _truncate_streams(result.stdout or "", result.stderr or "", _stdio_max_bytes())
        if result.returncode != 0:
            return _build_nonzero_exit_error_payload(
                process_name="Command",
                exit_code=result.returncode,
                stdout=stdout,
                stderr=stderr,
            )
        return {
            "status": "ok",
            "exit_code": result.returncode,
            "stdout": stdout,
            "stderr": stderr,
        }

    def mcp_request(
        self,
        agent,
        session: AgentComputeSession,
        server_config_id: str,
        tool_name: str,
        params: Dict[str, Any],
        *,
        full_tool_name: Optional[str] = None,
        server_payload: Optional[Dict[str, Any]] = None,
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

    def terminate(
        self,
        agent,
        session: AgentComputeSession,
        *,
        reason: str,
        delete_workspace: bool = False,
    ) -> SandboxSessionUpdate:
        return SandboxSessionUpdate(state=AgentComputeSession.State.STOPPED)

    def snapshot_workspace(self, agent, session: AgentComputeSession, *, reason: str) -> Dict[str, Any]:
        return {"status": "skipped", "message": "Snapshots are not supported by local backend."}

    def discover_mcp_tools(
        self,
        server_config_id: str,
        *,
        reason: str,
        server_payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
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

    def _post(self, path: str, payload: Dict[str, Any], *, timeout: Optional[int] = None) -> Dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            response = requests.post(
                url,
                json=payload,
                headers=headers,
                timeout=timeout or _http_timeout_seconds(),
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            return {"status": "error", "message": str(exc)}
        try:
            return response.json()
        except ValueError:
            return {"status": "error", "message": "Invalid JSON response from sandbox API."}

    def deploy_or_resume(self, agent, session: AgentComputeSession) -> SandboxSessionUpdate:
        payload = {"agent_id": str(agent.id)}
        proxy_env = _proxy_env_for_session(session)
        if proxy_env:
            payload["proxy_env"] = proxy_env
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
        timeout_value = timeout if isinstance(timeout, int) and timeout > 0 else _run_command_timeout_seconds()
        payload = {
            "agent_id": str(agent.id),
            "command": command,
            "cwd": cwd,
            "env": env,
            "timeout": timeout_value,
            "interactive": interactive,
        }
        proxy_env = _proxy_env_for_session(session)
        if proxy_env:
            payload["proxy_env"] = proxy_env
        request_timeout = max(_http_timeout_seconds(), timeout_value + 10)
        return self._post("sandbox/compute/run_command", payload, timeout=request_timeout)

    def mcp_request(
        self,
        agent,
        session: AgentComputeSession,
        server_config_id: str,
        tool_name: str,
        params: Dict[str, Any],
        *,
        full_tool_name: Optional[str] = None,
        server_payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload = {
            "agent_id": str(agent.id),
            "server_id": server_config_id,
            "tool_name": tool_name,
            "params": params,
        }
        if server_payload:
            payload["server"] = server_payload
        proxy_env = _proxy_env_for_session(session)
        if proxy_env:
            payload["proxy_env"] = proxy_env
        return self._post("sandbox/compute/mcp_request", payload)

    def tool_request(
        self,
        agent,
        session: AgentComputeSession,
        tool_name: str,
        params: Dict[str, Any],
    ) -> Dict[str, Any]:
        params_payload = params or {}
        request_timeout = _http_timeout_seconds()
        if tool_name == "python_exec":
            normalized = _normalize_timeout(
                params_payload.get("timeout_seconds"),
                default=_python_default_timeout(),
                maximum=_python_max_timeout(),
            )
            params_payload = dict(params_payload)
            params_payload["timeout_seconds"] = normalized
            request_timeout = max(_http_timeout_seconds(), normalized + 10)

        payload = {
            "agent_id": str(agent.id),
            "tool_name": tool_name,
            "params": params_payload,
        }
        proxy_env = _proxy_env_for_session(session)
        if proxy_env:
            payload["proxy_env"] = proxy_env
        return self._post("sandbox/compute/tool_request", payload, timeout=request_timeout)

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
        proxy_env = _proxy_env_for_session(session)
        if proxy_env:
            payload["proxy_env"] = proxy_env
        return self._post("sandbox/compute/sync_filespace", payload)

    def terminate(
        self,
        agent,
        session: AgentComputeSession,
        *,
        reason: str,
        delete_workspace: bool = False,
    ) -> SandboxSessionUpdate:
        payload = {"agent_id": str(agent.id), "reason": reason, "delete_workspace": delete_workspace}
        proxy_env = _proxy_env_for_session(session)
        if proxy_env:
            payload["proxy_env"] = proxy_env
        response = self._post("sandbox/compute/terminate", payload)
        return _session_update_from_response(response)

    def discover_mcp_tools(
        self,
        server_config_id: str,
        *,
        reason: str,
        server_payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload = {"server_id": server_config_id, "reason": reason}
        if server_payload:
            payload["server"] = server_payload
        return self._post("sandbox/compute/discover_mcp_tools", payload)

    def snapshot_workspace(self, agent, session: AgentComputeSession, *, reason: str) -> Dict[str, Any]:
        return {"status": "skipped", "message": "Workspace snapshots are not available via HTTP backend."}


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
    if backend_name in ("kubernetes", "k8s"):
        from api.services.sandbox_kubernetes import KubernetesSandboxBackend

        return KubernetesSandboxBackend()
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


def _parse_sync_timestamp(value: Any) -> Optional[timezone.datetime]:
    if isinstance(value, timezone.datetime):
        return value
    if isinstance(value, str) and value.strip():
        parsed = parse_datetime(value.strip())
        if parsed:
            return parsed
    return None


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
            env=_sanitize_env(),
            capture_output=True,
            text=True,
            timeout=timeout_value,
        )
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "Python execution timed out."}
    except OSError as exc:
        return {"status": "error", "message": f"Python execution failed to start: {exc}"}

    stdout, stderr = _truncate_streams(result.stdout or "", result.stderr or "", _stdio_max_bytes())
    if result.returncode != 0:
        return _build_nonzero_exit_error_payload(
            process_name="Python",
            exit_code=result.returncode,
            stdout=stdout,
            stderr=stderr,
        )
    return {
        "status": "ok",
        "exit_code": result.returncode,
        "stdout": stdout,
        "stderr": stderr,
    }


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


def _build_filespace_export_response(agent, export_path: str) -> Dict[str, Any]:
    from api.agent.files.filespace_service import get_or_create_default_filespace
    from api.agent.files.attachment_helpers import build_signed_filespace_download_url
    from api.agent.tools.agent_variables import set_agent_variable
    from api.models import AgentFsNode

    filespace = get_or_create_default_filespace(agent)
    node = AgentFsNode.objects.filter(filespace=filespace, path=export_path).first()
    if not node:
        return {"status": "error", "message": "Exported file not found in filespace."}

    signed_url = build_signed_filespace_download_url(
        agent_id=str(agent.id),
        node_id=str(node.id),
    )
    set_agent_variable(export_path, signed_url)

    var_ref = f"$[{export_path}]"
    return {
        "status": "ok",
        "file": var_ref,
        "inline": f"[Download]({var_ref})",
        "inline_html": f"<a href='{var_ref}'>Download</a>",
        "attach": var_ref,
    }


def _select_proxy_for_session(agent, session: AgentComputeSession) -> Optional[Any]:
    if not getattr(settings, "ENABLE_PROXY_ROUTING", True):
        return None
    preferred_proxy = session.proxy_server if session.proxy_server and session.proxy_server.is_active else None
    try:
        if preferred_proxy:
            proxy = select_proxy(
                preferred_proxy=preferred_proxy,
                allow_no_proxy_in_debug=not _proxy_required(),
                context_id=f"sandbox_agent_{agent.id}",
            )
        else:
            proxy = select_proxy_for_persistent_agent(
                agent,
                allow_no_proxy_in_debug=not _proxy_required(),
            )
    except RuntimeError as exc:
        if _proxy_required():
            raise SandboxComputeUnavailable(str(exc)) from exc
        logger.warning("Sandbox proxy selection failed for agent=%s: %s", agent.id, exc)
        proxy = None

    if _proxy_required() and not proxy:
        raise SandboxComputeUnavailable("No proxy server available for sandbox compute.")

    if proxy and proxy != session.proxy_server:
        session.proxy_server = proxy
        session.save(update_fields=["proxy_server", "updated_at"])
    return proxy


def _proxy_env_for_session(session: AgentComputeSession) -> Optional[Dict[str, str]]:
    proxy = session.proxy_server
    if not proxy:
        return None
    proxy_url = proxy.proxy_url
    env = {
        "HTTP_PROXY": proxy_url,
        "HTTPS_PROXY": proxy_url,
    }
    no_proxy = _no_proxy_value()
    if no_proxy:
        env["NO_PROXY"] = no_proxy
    return env


def _build_mcp_server_payload(config_id: str) -> tuple[Optional[Dict[str, Any]], Optional[Any]]:
    if not config_id:
        return None, None

    try:
        cfg = (
            MCPServerConfig.objects.filter(id=config_id, is_active=True)
            .select_related("oauth_credential")
            .first()
        )
    except Exception:
        logger.exception("Failed to load MCP server config %s for sandbox payload", config_id)
        return None, None

    if not cfg:
        return None, None

    from api.agent.tools.mcp_manager import get_mcp_manager

    manager = get_mcp_manager()
    runtime = manager._build_runtime_from_config(cfg)
    headers = dict(runtime.headers or {})
    auth_headers = manager._build_auth_headers(runtime)
    if auth_headers:
        headers.update(auth_headers)

    payload = {
        "config_id": runtime.config_id,
        "name": runtime.name,
        "command": runtime.command or "",
        "command_args": list(runtime.args or []),
        "url": runtime.url or "",
        "env": runtime.env or {},
        "headers": headers,
        "auth_method": runtime.auth_method,
        "scope": runtime.scope,
    }
    return payload, runtime


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
        _select_proxy_for_session(agent, session)
        started = session.state != AgentComputeSession.State.RUNNING
        bootstrap_started_at = time.monotonic()
        if started:
            deploy_started_at = time.monotonic()
            update = self._backend.deploy_or_resume(agent, session)
            deploy_duration_ms = _elapsed_ms(deploy_started_at)
            if not update.state:
                update.state = AgentComputeSession.State.RUNNING
            self._apply_session_update(session, update)
            logger.info(
                (
                    "Sandbox bootstrap deploy_or_resume agent=%s source=%s duration_ms=%s "
                    "state=%s pod=%s namespace=%s"
                ),
                agent.id,
                source,
                deploy_duration_ms,
                update.state,
                update.pod_name or session.pod_name,
                update.namespace or session.namespace,
            )
        pull_started_at = time.monotonic()
        sync_result = self._sync_workspace_pull(agent, session)
        pull_duration_ms = _elapsed_ms(pull_started_at)
        pull_status = sync_result.get("status") if isinstance(sync_result, dict) else "skipped"
        logger.info(
            "Sandbox %s pull agent=%s source=%s duration_ms=%s status=%s",
            "bootstrap" if started else "refresh",
            agent.id,
            source,
            pull_duration_ms,
            pull_status,
        )
        if sync_result and sync_result.get("status") != "ok":
            logger.warning("Sandbox pull sync failed agent=%s result=%s", agent.id, sync_result)
        self._touch_session(session, source=source)
        if started:
            logger.info(
                "Sandbox bootstrap complete agent=%s source=%s total_duration_ms=%s",
                agent.id,
                source,
                _elapsed_ms(bootstrap_started_at),
            )
        return session

    def _sync_workspace_pull(self, agent, session: AgentComputeSession) -> Optional[Dict[str, Any]]:
        if isinstance(self._backend, LocalSandboxBackend):
            return None
        pull_started_at = time.monotonic()
        since = session.last_filespace_pull_at
        manifest = build_filespace_pull_manifest(agent, since=since)
        manifest_duration_ms = _elapsed_ms(pull_started_at)
        if manifest.get("status") != "ok":
            logger.warning(
                "Sandbox pull manifest failed agent=%s duration_ms=%s status=%s result=%s",
                agent.id,
                manifest_duration_ms,
                manifest.get("status"),
                manifest,
            )
            return manifest
        files = manifest.get("files") or []
        logger.info(
            "Sandbox pull manifest built agent=%s files=%s duration_ms=%s since_set=%s",
            agent.id,
            len(files),
            manifest_duration_ms,
            since is not None,
        )
        payload = {"files": files}
        backend_started_at = time.monotonic()
        response = self._backend.sync_filespace(agent, session, direction="pull", payload=payload)
        backend_duration_ms = _elapsed_ms(backend_started_at)
        total_duration_ms = _elapsed_ms(pull_started_at)
        cursor_value = _parse_sync_timestamp(manifest.get("sync_cursor"))
        cursor_persisted = False
        if response.get("status") == "ok" and cursor_value:
            session.last_filespace_pull_at = cursor_value
            session.save(update_fields=["last_filespace_pull_at", "updated_at"])
            cursor_persisted = True
        logger.info(
            (
                "Sandbox pull sync completed agent=%s files=%s status=%s "
                "backend_duration_ms=%s total_duration_ms=%s applied=%s skipped=%s conflicts=%s "
                "cursor_set=%s"
            ),
            agent.id,
            len(files),
            response.get("status"),
            backend_duration_ms,
            total_duration_ms,
            response.get("applied"),
            response.get("skipped"),
            response.get("conflicts"),
            cursor_persisted,
        )
        return response

    def _sync_workspace_push(self, agent, session: AgentComputeSession) -> Optional[Dict[str, Any]]:
        if isinstance(self._backend, LocalSandboxBackend):
            return None
        since = session.last_filespace_sync_at.isoformat() if session.last_filespace_sync_at else None
        response = self._backend.sync_filespace(agent, session, direction="push", payload={"since": since})
        if response.get("status") != "ok":
            return response

        changes = response.get("changes") or []
        sync_timestamp = _parse_sync_timestamp(response.get("sync_timestamp"))
        applied = apply_filespace_push(agent, changes, sync_timestamp=sync_timestamp)
        if applied.get("status") != "ok":
            return applied

        stamped = _parse_sync_timestamp(applied.get("sync_timestamp")) or sync_timestamp or timezone.now()
        session.last_filespace_sync_at = stamped
        session.save(update_fields=["last_filespace_sync_at", "updated_at"])
        return applied

    def _record_snapshot(self, agent, snapshot_payload: Dict[str, Any]) -> Optional[ComputeSnapshot]:
        if not snapshot_payload or snapshot_payload.get("status") != "ok":
            return None
        snapshot_name = snapshot_payload.get("snapshot_name") or snapshot_payload.get("k8s_snapshot_name")
        if not snapshot_name:
            return None
        size_bytes = snapshot_payload.get("size_bytes")
        try:
            return ComputeSnapshot.objects.create(
                agent=agent,
                k8s_snapshot_name=str(snapshot_name),
                size_bytes=size_bytes if isinstance(size_bytes, int) else None,
                status=ComputeSnapshot.Status.READY,
            )
        except (DatabaseError, ValueError, TypeError):
            return None

    def _maybe_sync_after_tool(self, agent, session: AgentComputeSession) -> Optional[Dict[str, Any]]:
        if not _sync_on_tool_call():
            return None
        return self._sync_workspace_push(agent, session)

    def _maybe_sync_after_mcp(self, agent, session: AgentComputeSession) -> Optional[Dict[str, Any]]:
        if not _sync_on_mcp_call():
            return None
        return self._sync_workspace_push(agent, session)

    def _maybe_sync_after_run_command(self, agent, session: AgentComputeSession) -> Optional[Dict[str, Any]]:
        if not _sync_on_run_command():
            return None
        return self._sync_workspace_push(agent, session)

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
        if isinstance(result, dict) and result.get("status") != "error":
            sync_result = self._maybe_sync_after_run_command(agent, session)
            if sync_result and sync_result.get("status") != "ok":
                return sync_result
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
        server_payload, _runtime = _build_mcp_server_payload(server_config_id)
        if not server_payload:
            return {"status": "error", "message": "MCP server config not available."}
        result = self._backend.mcp_request(
            agent,
            session,
            server_config_id,
            tool_name,
            params,
            full_tool_name=full_tool_name,
            server_payload=server_payload,
        )
        if isinstance(result, dict) and result.get("status") != "error":
            sync_result = self._maybe_sync_after_mcp(agent, session)
            if sync_result and sync_result.get("status") != "ok":
                return sync_result
        _log_tool_call("mcp_request", tool_name, params, agent_id=str(agent.id))
        return result

    def tool_request(self, agent, tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        session = self._ensure_session(agent, source="tool_request")
        result = self._backend.tool_request(agent, session, tool_name, params)
        if isinstance(result, dict) and result.get("status") != "error":
            sync_result = self._maybe_sync_after_tool(agent, session)
            if sync_result and sync_result.get("status") != "ok":
                return sync_result
            export_path = result.get("export_path")
            if isinstance(export_path, str) and export_path.strip():
                response = _build_filespace_export_response(agent, export_path)
                if response.get("status") == "ok":
                    result = response
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
        update = self._backend.terminate(agent, session, reason=reason, delete_workspace=False)
        self._apply_session_update(session, update)
        return session

    def idle_stop_session(self, session: AgentComputeSession, *, reason: str = "idle_ttl") -> Dict[str, Any]:
        agent = session.agent
        sync_result = self._sync_workspace_push(agent, session)
        sync_failed = False
        if sync_result and sync_result.get("status") != "ok":
            retry_result = self._sync_workspace_push(agent, session)
            if retry_result and retry_result.get("status") != "ok":
                sync_failed = True
            sync_result = retry_result

        snapshot_payload = self._backend.snapshot_workspace(agent, session, reason=reason)
        snapshot = self._record_snapshot(agent, snapshot_payload or {})
        snapshot_failed = bool(snapshot_payload and snapshot_payload.get("status") == "error")
        delete_workspace = snapshot is not None and not sync_failed

        update = self._backend.terminate(
            agent,
            session,
            reason=reason,
            delete_workspace=delete_workspace,
        )
        self._apply_session_update(session, update)

        if snapshot:
            session.workspace_snapshot = snapshot
        if sync_failed or snapshot_failed:
            session.state = AgentComputeSession.State.ERROR
        else:
            session.state = AgentComputeSession.State.STOPPED
        session.lease_expires_at = None
        session.save(update_fields=["state", "workspace_snapshot", "lease_expires_at", "updated_at"])

        return {
            "status": "ok" if not (sync_failed or snapshot_failed) else "error",
            "sync_result": sync_result,
            "snapshot": snapshot_payload,
            "stopped": True,
        }

    def discover_mcp_tools(self, server_config_id: str, *, reason: str) -> Dict[str, Any]:
        server_payload, runtime = _build_mcp_server_payload(server_config_id)
        if not server_payload or runtime is None:
            return {"status": "error", "message": "MCP server config not available."}

        result = self._backend.discover_mcp_tools(
            server_config_id,
            reason=reason,
            server_payload=server_payload,
        )
        if isinstance(result, dict) and result.get("status") == "ok":
            tools = result.get("tools")
            if isinstance(tools, list):
                from api.agent.tools.mcp_manager import get_mcp_manager

                manager = get_mcp_manager()
                fingerprint = manager._build_tool_cache_fingerprint(runtime)
                set_cached_mcp_tool_definitions(server_config_id, fingerprint, tools)
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
