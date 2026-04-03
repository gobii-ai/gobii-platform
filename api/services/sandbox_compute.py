import base64
import hashlib
import json
import logging
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Dict, Optional, Sequence

import requests
from celery.exceptions import CeleryError
from django.conf import settings
from django.utils.dateparse import parse_datetime
from django.db import DatabaseError
from django.utils import timezone
from redis.exceptions import RedisError
from kombu.exceptions import OperationalError as KombuOperationalError

from api.models import AgentComputeSession, ComputeSnapshot, PersistentAgent, MCPServerConfig, PersistentAgentSecret
from api.proxy_selection import select_proxy, select_proxy_for_persistent_agent
from api.services.mcp_tool_cache import set_cached_mcp_tool_definitions
from api.services.sandbox_filespace_sync import apply_filespace_push, build_filespace_pull_manifest
from api.services.sandbox_internal_paths import (
    CUSTOM_TOOL_SQLITE_FILESPACE_PATH,
    CUSTOM_TOOL_SQLITE_WORKSPACE_PATH,
)
from api.services.system_settings import get_sandbox_compute_enabled, get_sandbox_compute_require_proxy
from api.sandbox_utils import monotonic_elapsed_ms as _elapsed_ms, normalize_timeout as _normalize_timeout
from config.redis_client import get_redis_client
from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource
from waffle import get_waffle_flag_model

logger = logging.getLogger(__name__)


SANDBOX_COMPUTE_WAFFLE_FLAG = "sandbox_compute"
_POST_SYNC_QUEUE_KEY_PREFIX = "sandbox-compute:post-sync:queued"
_PROXY_URL_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "FTP_PROXY",
    "ALL_PROXY",
)
_PROXY_URL_ENV_KEYS_LOWER = tuple(key.lower() for key in _PROXY_URL_ENV_KEYS)
_NO_PROXY_ENV_KEYS = (
    "NO_PROXY",
    "no_proxy",
)
_CUSTOM_TOOL_SQLITE_MIME_TYPE = "application/vnd.sqlite3"
_SQLITE_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")
_ACTIVE_CONVERSATION_CHANNEL_CACHE_ATTR = "_sandbox_active_conversation_channel"
_ACTIVE_CONVERSATION_CHANNEL_UNSET = object()


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


def _mcp_request_timeout_seconds() -> int:
    return int(getattr(settings, "SANDBOX_COMPUTE_MCP_REQUEST_TIMEOUT_SECONDS", _http_timeout_seconds()))


def _tool_request_timeout_seconds() -> int:
    return int(getattr(settings, "SANDBOX_COMPUTE_TOOL_REQUEST_TIMEOUT_SECONDS", _http_timeout_seconds()))


def _discovery_timeout_seconds() -> int:
    return int(getattr(settings, "SANDBOX_COMPUTE_DISCOVERY_TIMEOUT_SECONDS", _http_timeout_seconds()))


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


def _post_sync_coalesce_ttl_seconds() -> int:
    return int(getattr(settings, "SANDBOX_COMPUTE_POST_SYNC_COALESCE_TTL_SECONDS", 600))


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
        *_PROXY_URL_ENV_KEYS,
        *_PROXY_URL_ENV_KEYS_LOWER,
        *_NO_PROXY_ENV_KEYS,
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "PYTHONUNBUFFERED",
        "PYTHONIOENCODING",
    }


def _sanitize_env(
    extra_env: Optional[Dict[str, str]] = None,
    trusted_env_keys: Optional[Sequence[str]] = None,
) -> Dict[str, str]:
    allowed = _allowed_env_keys()
    trusted = {str(key) for key in (trusted_env_keys or []) if isinstance(key, str) and key.strip()}
    env = {key: value for key, value in os.environ.items() if key in allowed}
    env.setdefault("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
    if extra_env:
        for key, value in extra_env.items():
            key_str = str(key)
            if key_str in allowed or key_str.startswith("SANDBOX_") or key_str in trusted:
                env[str(key)] = str(value)
    return env


def _resolved_env_var_secrets_for_agent(agent: Optional[PersistentAgent]) -> Dict[str, str]:
    if agent is None or not sandbox_compute_enabled_for_agent(agent):
        return {}

    merged: Dict[str, str] = {}
    secrets = (
        PersistentAgentSecret.objects.filter(
            agent=agent,
            requested=False,
            secret_type=PersistentAgentSecret.SecretType.ENV_VAR,
        )
        .order_by("updated_at", "created_at")
        .only("key", "encrypted_value")
    )
    for secret in secrets:
        key = str(secret.key or "").strip()
        if not key:
            continue
        try:
            merged[key] = secret.get_value()
        except Exception:
            logger.warning(
                "Failed to decrypt env_var secret for agent=%s key=%s",
                agent.id,
                key,
                exc_info=True,
            )
    return merged


def _merge_agent_env_vars(
    agent: Optional[PersistentAgent],
    base_env: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    merged, _ = _merge_agent_env_vars_with_secret_keys(agent, base_env)
    return merged


def _merge_agent_env_vars_with_secret_keys(
    agent: Optional[PersistentAgent],
    base_env: Optional[Dict[str, Any]] = None,
) -> tuple[Dict[str, str], list[str]]:
    merged = {str(k): str(v) for k, v in (base_env or {}).items()}
    secret_env = _resolved_env_var_secrets_for_agent(agent)
    merged.update(secret_env)
    return merged, sorted(secret_env.keys())


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
        trusted_env_keys: Optional[Sequence[str]] = None,
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
        agent: Optional[PersistentAgent] = None,
        session: Optional[AgentComputeSession] = None,
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
        trusted_env_keys: Optional[Sequence[str]] = None,
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
                cwd=cwd or str(getattr(settings, "BASE_DIR", os.getcwd())),
                env=_sanitize_env(env, trusted_env_keys=trusted_env_keys),
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
        agent: Optional[PersistentAgent] = None,
        session: Optional[AgentComputeSession] = None,
        server_payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        from api.agent.tools.mcp_manager import get_mcp_manager

        manager = get_mcp_manager()
        ok = manager.discover_tools_for_server(server_config_id, agent=agent)
        return {"status": "ok" if ok else "error", "reason": reason}


class HttpSandboxBackend(SandboxComputeBackend):
    def __init__(self, base_url: str, token: str):
        if not base_url:
            raise SandboxComputeUnavailable("SANDBOX_COMPUTE_API_URL is required.")
        if not token:
            raise SandboxComputeUnavailable("SANDBOX_COMPUTE_API_TOKEN is required for HTTP sandbox backend.")
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
        trusted_env_keys: Optional[Sequence[str]] = None,
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
        if trusted_env_keys:
            payload["trusted_env_keys"] = [str(key) for key in trusted_env_keys if str(key)]
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
        return self._post("sandbox/compute/mcp_request", payload, timeout=_mcp_request_timeout_seconds())

    def tool_request(
        self,
        agent,
        session: AgentComputeSession,
        tool_name: str,
        params: Dict[str, Any],
    ) -> Dict[str, Any]:
        params_payload = params or {}
        request_timeout = _tool_request_timeout_seconds()
        if tool_name == "python_exec":
            normalized = _normalize_timeout(
                params_payload.get("timeout_seconds"),
                default=_python_default_timeout(),
                maximum=_python_max_timeout(),
            )
            params_payload = dict(params_payload)
            params_payload["timeout_seconds"] = normalized
            request_timeout = max(_tool_request_timeout_seconds(), normalized + 10)

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
        agent: Optional[PersistentAgent] = None,
        session: Optional[AgentComputeSession] = None,
        server_payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload = {"server_id": server_config_id, "reason": reason}
        if agent is not None:
            payload["agent_id"] = str(agent.id)
        if session is not None:
            proxy_env = _proxy_env_for_session(session)
            if proxy_env:
                payload["proxy_env"] = proxy_env
        if server_payload:
            payload["server"] = server_payload
        return self._post("sandbox/compute/discover_mcp_tools", payload, timeout=_discovery_timeout_seconds())

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
    backend_name = str(settings.SANDBOX_COMPUTE_BACKEND or "").lower()
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

    extra_env = params.get("env")
    if not isinstance(extra_env, dict):
        extra_env = None
    trusted_env_keys = params.get("trusted_env_keys")
    if not isinstance(trusted_env_keys, list):
        trusted_env_keys = []

    try:
        result = subprocess.run(
            [sys.executable, "-c", code],
            env=_sanitize_env(extra_env, trusted_env_keys=trusted_env_keys),
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


def _decode_sync_change_content(change: Dict[str, Any]) -> Optional[bytes]:
    if "content_b64" in change and isinstance(change.get("content_b64"), str):
        try:
            return base64.b64decode(change["content_b64"], validate=True)
        except (TypeError, ValueError):
            return None
    content = change.get("content")
    if isinstance(content, bytes):
        return content
    if isinstance(content, str):
        return content.encode("utf-8")
    return None


def _sqlite_sidecar_paths(db_path: str) -> list[str]:
    return [f"{db_path}{suffix}" for suffix in _SQLITE_SIDECAR_SUFFIXES]


def _remove_sqlite_sidecars(db_path: str) -> None:
    for sidecar_path in _sqlite_sidecar_paths(db_path):
        try:
            os.remove(sidecar_path)
        except FileNotFoundError:
            continue


def _snapshot_sqlite_file_content(db_path: str) -> bytes:
    with tempfile.TemporaryDirectory(prefix="gobii-sqlite-sync-") as tmp_dir:
        snapshot_path = os.path.join(tmp_dir, "snapshot.db")
        source_conn = sqlite3.connect(db_path, timeout=5)
        snapshot_conn = sqlite3.connect(snapshot_path, timeout=5)
        try:
            try:
                source_conn.execute("PRAGMA busy_timeout=5000;")
            except sqlite3.Error:
                pass
            source_conn.backup(snapshot_conn)
            snapshot_conn.commit()
        finally:
            snapshot_conn.close()
            source_conn.close()

        with open(snapshot_path, "rb") as handle:
            return handle.read()


def _restore_sqlite_file_content(db_path: str, content: bytes) -> None:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="gobii-sqlite-sync-") as tmp_dir:
        snapshot_path = os.path.join(tmp_dir, "snapshot.db")
        with open(snapshot_path, "wb") as handle:
            handle.write(content)

        _remove_sqlite_sidecars(db_path)
        try:
            os.remove(db_path)
        except FileNotFoundError:
            pass

        source_conn = sqlite3.connect(snapshot_path, timeout=5)
        target_conn = sqlite3.connect(db_path, timeout=5)
        try:
            try:
                target_conn.execute("PRAGMA busy_timeout=5000;")
            except sqlite3.Error:
                pass
            source_conn.backup(target_conn)
            target_conn.commit()
        finally:
            target_conn.close()
            source_conn.close()

        _remove_sqlite_sidecars(db_path)


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
    return _proxy_env_values(proxy.proxy_url, _no_proxy_value())


def _proxy_env_values(proxy_url: Optional[str], no_proxy: Optional[str]) -> Dict[str, str]:
    env: Dict[str, str] = {}
    if proxy_url:
        for key in _PROXY_URL_ENV_KEYS:
            env[key] = proxy_url
        for key in _PROXY_URL_ENV_KEYS_LOWER:
            env[key] = proxy_url
    no_proxy_value = str(no_proxy or "").strip()
    if no_proxy_value:
        for key in _NO_PROXY_ENV_KEYS:
            env[key] = no_proxy_value
    return env


def _build_mcp_server_payload(
    config_id: str,
    *,
    agent: Optional[PersistentAgent] = None,
) -> tuple[Optional[Dict[str, Any]], Optional[Any]]:
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
        "env": _merge_agent_env_vars(agent, runtime.env or {}),
        "headers": headers,
        "auth_method": runtime.auth_method,
        "scope": runtime.scope,
    }
    return payload, runtime


def _requires_agent_pod_discovery(runtime: Any) -> bool:
    if runtime is None:
        return False
    if isinstance(runtime, dict):
        scope = runtime.get("scope")
        command = runtime.get("command")
        url = runtime.get("url")
    else:
        scope = getattr(runtime, "scope", None)
        command = getattr(runtime, "command", None)
        url = getattr(runtime, "url", None)
    if scope == MCPServerConfig.Scope.PLATFORM:
        return False
    return bool(command) and not bool(url)


def _post_sync_queue_key(agent_id: str) -> str:
    return f"{_POST_SYNC_QUEUE_KEY_PREFIX}:{agent_id}"


def _sandbox_backend_name(backend: Any) -> str | None:
    if backend is None:
        return None
    if isinstance(backend, str):
        return backend or None
    backend_name = backend.__class__.__name__.removesuffix("SandboxBackend").lower()
    return backend_name or None


def _configured_sandbox_backend_name() -> str:
    backend_name = str(settings.SANDBOX_COMPUTE_BACKEND or "").strip().lower()
    if backend_name in {"", "local"}:
        return "local"
    if backend_name in {"http", "remote"}:
        return "http"
    if backend_name in {"kubernetes", "k8s"}:
        return "kubernetes"
    return backend_name


def _active_conversation_channel(agent: Optional[PersistentAgent]) -> str | None:
    if agent is None:
        return None

    cached_channel = getattr(agent, _ACTIVE_CONVERSATION_CHANNEL_CACHE_ATTR, _ACTIVE_CONVERSATION_CHANNEL_UNSET)
    if cached_channel is not _ACTIVE_CONVERSATION_CHANNEL_UNSET:
        return cached_channel

    preferred = getattr(agent, "preferred_contact_endpoint", None)
    preferred_channel = getattr(preferred, "channel", None)
    if preferred_channel:
        cached_channel = str(preferred_channel)
        setattr(agent, _ACTIVE_CONVERSATION_CHANNEL_CACHE_ATTR, cached_channel)
        return cached_channel

    try:
        latest_channel = agent.owned_conversations.order_by("-id").values_list("channel", flat=True).first()
    except DatabaseError:
        logger.debug(
            "Failed to derive active conversation channel for sandbox analytics agent=%s",
            getattr(agent, "id", None),
            exc_info=True,
        )
        return None

    cached_channel = str(latest_channel) if latest_channel else None
    setattr(agent, _ACTIVE_CONVERSATION_CHANNEL_CACHE_ATTR, cached_channel)
    return cached_channel


def _build_sandbox_analytics_properties(
    agent: PersistentAgent,
    *,
    backend: Any = None,
    session: Optional[AgentComputeSession] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "agent_id": str(agent.id),
        "agent_name": agent.name,
        "backend": _sandbox_backend_name(backend),
        "active_conversation_channel": _active_conversation_channel(agent),
    }
    if session is not None:
        payload["session_id"] = str(session.pk)
    if extra:
        payload.update(extra)
    return Analytics.with_org_properties(payload, organization=getattr(agent, "organization", None))


def _track_sandbox_event(
    *,
    agent: Optional[PersistentAgent],
    event: AnalyticsEvent,
    backend: Any = None,
    session: Optional[AgentComputeSession] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    user_id = getattr(agent, "user_id", None)
    if agent is None or not user_id:
        return

    try:
        Analytics.track_event(
            user_id=str(user_id),
            event=event,
            source=AnalyticsSource.AGENT,
            properties=_build_sandbox_analytics_properties(
                agent,
                backend=backend,
                session=session,
                extra=extra,
            ),
        )
    except Exception:
        logger.debug(
            "Failed to emit sandbox analytics event=%s agent=%s",
            event,
            getattr(agent, "id", None),
            exc_info=True,
        )


def _execution_result_status(result: Any) -> str:
    if isinstance(result, dict) and str(result.get("status")).strip().lower() == "error":
        return "error"
    return "ok"


def _error_type_from_result(result: Any) -> str | None:
    if _execution_result_status(result) != "error" or not isinstance(result, dict):
        return None

    for key in ("error_type", "error_code"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _error_type_from_exception(exc: Exception) -> str:
    return exc.__class__.__name__


def _track_execution_event(
    *,
    agent: PersistentAgent,
    backend: Any,
    session: AgentComputeSession,
    request_source: str,
    result: Any,
    sync_enqueued: bool,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    properties: Dict[str, Any] = {
        "request_source": request_source,
        "result_status": _execution_result_status(result),
        "sync_enqueued": sync_enqueued,
    }
    error_type = _error_type_from_result(result)
    if error_type:
        properties["error_type"] = error_type
    if extra:
        properties.update(extra)
    _track_sandbox_event(
        agent=agent,
        event=AnalyticsEvent.SANDBOX_EXECUTION_ROUTED,
        backend=backend,
        session=session,
        extra=properties,
    )


def track_sandbox_unavailable(
    agent: Optional[PersistentAgent],
    *,
    request_source: str,
    tool_name: str | None = None,
) -> None:
    extra: Dict[str, Any] = {
        "request_source": request_source,
        "error_type": "unavailable",
    }
    if tool_name:
        extra["tool_name"] = tool_name
    _track_sandbox_event(
        agent=agent,
        event=AnalyticsEvent.SANDBOX_UNAVAILABLE,
        backend=_configured_sandbox_backend_name(),
        extra=extra,
    )


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
        previous_state = session.state
        started = session.state != AgentComputeSession.State.RUNNING
        bootstrap_started_at = time.monotonic()
        deploy_started_at = time.monotonic()
        update = self._backend.deploy_or_resume(agent, session)
        deploy_duration_ms = _elapsed_ms(deploy_started_at)
        if not update.state:
            update.state = AgentComputeSession.State.RUNNING
        self._apply_session_update(session, update)
        logger.info(
            (
                "Sandbox %s deploy_or_resume agent=%s source=%s duration_ms=%s "
                "state=%s pod=%s namespace=%s"
            ),
            "bootstrap" if started else "refresh",
            agent.id,
            source,
            deploy_duration_ms,
            update.state,
            update.pod_name or session.pod_name,
            update.namespace or session.namespace,
        )
        if session.state != AgentComputeSession.State.RUNNING:
            raise SandboxComputeUnavailable(
                "Sandbox session is not ready"
                f" (state={session.state}, pod={session.pod_name or 'unknown'},"
                f" namespace={session.namespace or 'unknown'})."
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
            _track_sandbox_event(
                agent=agent,
                event=AnalyticsEvent.SANDBOX_SESSION_STARTED,
                backend=self._backend,
                session=session,
                extra={
                    "start_mode": "created" if _created else "resumed",
                    "previous_state": previous_state,
                    "namespace": session.namespace or None,
                    "pod_name_present": bool(session.pod_name),
                },
            )
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

    def _enqueue_post_sync_after_call(self, agent, *, source: str) -> None:
        if isinstance(self._backend, LocalSandboxBackend):
            return

        agent_id = str(agent.id)
        queue_key = _post_sync_queue_key(agent_id)
        redis_client = None
        should_enqueue = True

        try:
            redis_client = get_redis_client()
        except RedisError:
            logger.warning(
                "Sandbox post-sync Redis unavailable; enqueueing without coalescing agent=%s source=%s",
                agent_id,
                source,
            )

        if redis_client is not None:
            try:
                queued = redis_client.set(
                    queue_key,
                    source,
                    nx=True,
                    ex=_post_sync_coalesce_ttl_seconds(),
                )
                should_enqueue = bool(queued)
            except RedisError:
                logger.warning(
                    "Sandbox post-sync coalescing failed; enqueueing without lock agent=%s source=%s",
                    agent_id,
                    source,
                )
                should_enqueue = True

        if not should_enqueue:
            logger.debug("Sandbox post-sync already queued agent=%s source=%s", agent_id, source)
            return

        try:
            from api.tasks.sandbox_compute import sync_filespace_after_call

            sync_filespace_after_call.delay(agent_id, source=source)
        except (CeleryError, KombuOperationalError):
            logger.warning(
                "Sandbox post-sync task enqueue failed agent=%s source=%s",
                agent_id,
                source,
                exc_info=True,
            )
            if redis_client is not None:
                try:
                    redis_client.delete(queue_key)
                except RedisError:
                    logger.warning(
                        "Sandbox post-sync failed to clear coalesce key after enqueue error agent=%s source=%s",
                        agent_id,
                        source,
                        exc_info=True,
                    )

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
        merged_env, trusted_secret_keys = _merge_agent_env_vars_with_secret_keys(agent, env)
        backend_env = merged_env if merged_env else (env if env else None)
        try:
            result = self._backend.run_command(
                agent,
                session,
                command,
                cwd=cwd,
                env=backend_env,
                trusted_env_keys=trusted_secret_keys or None,
                timeout=timeout,
                interactive=interactive,
            )
        except Exception as exc:
            _track_sandbox_event(
                agent=agent,
                event=AnalyticsEvent.SANDBOX_EXECUTION_ROUTED,
                backend=self._backend,
                session=session,
                extra={
                    "request_source": "run_command",
                    "result_status": "error",
                    "sync_enqueued": False,
                    "command_present": True,
                    "error_type": _error_type_from_exception(exc),
                },
            )
            raise

        sync_enqueued = False
        if isinstance(result, dict) and result.get("status") != "error":
            if _sync_on_run_command():
                self._enqueue_post_sync_after_call(agent, source="run_command")
                sync_enqueued = True
        _track_execution_event(
            agent=agent,
            backend=self._backend,
            session=session,
            request_source="run_command",
            result=result,
            sync_enqueued=sync_enqueued,
            extra={"command_present": True},
        )
        return result

    def _sync_custom_tool_sqlite_pull(
        self,
        agent,
        session: AgentComputeSession,
        local_sqlite_db_path: str,
    ) -> Dict[str, Any]:
        if isinstance(self._backend, LocalSandboxBackend):
            return {"status": "skipped", "message": "Local backend does not require SQLite sync."}

        entry: Dict[str, Any] = {
            "path": CUSTOM_TOOL_SQLITE_FILESPACE_PATH,
        }
        if os.path.exists(local_sqlite_db_path):
            try:
                content = _snapshot_sqlite_file_content(local_sqlite_db_path)
            except (OSError, sqlite3.Error) as exc:
                return {"status": "error", "message": f"Failed to snapshot shared SQLite DB: {exc}"}
            entry.update(
                {
                    "content_b64": base64.b64encode(content).decode("ascii"),
                    "mime_type": _CUSTOM_TOOL_SQLITE_MIME_TYPE,
                    "size_bytes": len(content),
                    "checksum_sha256": hashlib.sha256(content).hexdigest(),
                }
            )
        else:
            entry["is_deleted"] = True

        return self._backend.sync_filespace(agent, session, direction="pull", payload={"files": [entry]})

    def _sync_custom_tool_sqlite_push(
        self,
        agent,
        session: AgentComputeSession,
        local_sqlite_db_path: str,
    ) -> Dict[str, Any]:
        if isinstance(self._backend, LocalSandboxBackend):
            return {"status": "skipped", "message": "Local backend does not require SQLite sync."}

        payload: Dict[str, Any] = {"internal_paths": [CUSTOM_TOOL_SQLITE_FILESPACE_PATH]}
        response = self._backend.sync_filespace(agent, session, direction="push", payload=payload)
        if not isinstance(response, dict):
            return {"status": "error", "message": "Sandbox SQLite sync returned an invalid response."}
        if response.get("status") != "ok":
            return response

        sqlite_change = None
        for change in response.get("changes") or []:
            if not isinstance(change, dict):
                continue
            if change.get("path") != CUSTOM_TOOL_SQLITE_FILESPACE_PATH:
                continue
            sqlite_change = change
            break

        if sqlite_change is None:
            return {"status": "ok", "sqlite_synced": False}

        if bool(sqlite_change.get("is_deleted")):
            try:
                os.remove(local_sqlite_db_path)
            except FileNotFoundError:
                pass
            except OSError as exc:
                return {"status": "error", "message": f"Failed to remove synced SQLite DB: {exc}"}
            return {"status": "ok", "sqlite_synced": True, "deleted": True}

        content = _decode_sync_change_content(sqlite_change)
        if content is None:
            return {"status": "error", "message": "Sandbox SQLite sync returned invalid file content."}

        try:
            _restore_sqlite_file_content(local_sqlite_db_path, content)
        except (OSError, sqlite3.Error) as exc:
            return {"status": "error", "message": f"Failed to write synced SQLite DB: {exc}"}

        return {
            "status": "ok",
            "sqlite_synced": True,
            "deleted": False,
            "size_bytes": len(content),
        }

    def run_custom_tool_command(
        self,
        agent,
        command: str,
        *,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        timeout: Optional[int] = None,
        interactive: bool = False,
        local_sqlite_db_path: Optional[str] = None,
        sqlite_env_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        session = self._ensure_session(agent, source="custom_tool_run_command")
        shared_sqlite_db: Optional[Dict[str, Any]] = None

        runtime_env = dict(env or {})
        if sqlite_env_key and local_sqlite_db_path:
            runtime_env[sqlite_env_key] = (
                local_sqlite_db_path
                if isinstance(self._backend, LocalSandboxBackend)
                else CUSTOM_TOOL_SQLITE_WORKSPACE_PATH
            )

        merged_env, trusted_secret_keys = _merge_agent_env_vars_with_secret_keys(agent, runtime_env)
        backend_env = merged_env if merged_env else None

        if local_sqlite_db_path and not isinstance(self._backend, LocalSandboxBackend):
            pull_result = self._sync_custom_tool_sqlite_pull(agent, session, local_sqlite_db_path)
            if not isinstance(pull_result, dict):
                return {"status": "error", "message": "Custom tool SQLite pull sync returned an invalid response."}
            if pull_result.get("status") != "ok":
                return pull_result

        result = self._backend.run_command(
            agent,
            session,
            command,
            cwd=cwd,
            env=backend_env,
            trusted_env_keys=trusted_secret_keys or None,
            timeout=timeout,
            interactive=interactive,
        )

        if local_sqlite_db_path and isinstance(self._backend, LocalSandboxBackend):
            shared_sqlite_db = {
                "available": True,
                "same_db_as_sqlite_batch": True,
                "transport": "local",
                "sync_back": "not_needed",
            }

        if local_sqlite_db_path and not isinstance(self._backend, LocalSandboxBackend):
            push_result = self._sync_custom_tool_sqlite_push(
                agent,
                session,
                local_sqlite_db_path,
            )
            if push_result.get("status") != "ok":
                if isinstance(result, dict) and result.get("status") == "error":
                    result = dict(result)
                    result["sqlite_sync_error"] = push_result.get("message")
                    return result
                return push_result
            if not push_result.get("sqlite_synced"):
                message = "Custom tool SQLite sync did not return the shared agent DB."
                if isinstance(result, dict) and result.get("status") == "error":
                    result = dict(result)
                    result["sqlite_sync_error"] = message
                    return result
                return {"status": "error", "message": message}
            shared_sqlite_db = {
                "available": True,
                "same_db_as_sqlite_batch": True,
                "transport": "sandbox_sync",
                "sync_back": "ok",
                "deleted": bool(push_result.get("deleted")),
            }
            if isinstance(push_result.get("size_bytes"), int):
                shared_sqlite_db["size_bytes"] = push_result["size_bytes"]

        if shared_sqlite_db and isinstance(result, dict):
            result = dict(result)
            result["shared_sqlite_db"] = shared_sqlite_db

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
        server_payload, _runtime = _build_mcp_server_payload(server_config_id, agent=agent)
        if not server_payload:
            result = {"status": "error", "message": "MCP server config not available."}
            _track_execution_event(
                agent=agent,
                backend=self._backend,
                session=session,
                request_source="mcp_request",
                result=result,
                sync_enqueued=False,
                extra={
                    "mcp_server": str(server_config_id),
                    "mcp_method": tool_name,
                },
            )
            return result
        try:
            result = self._backend.mcp_request(
                agent,
                session,
                server_config_id,
                tool_name,
                params,
                full_tool_name=full_tool_name,
                server_payload=server_payload,
            )
        except Exception as exc:
            _track_sandbox_event(
                agent=agent,
                event=AnalyticsEvent.SANDBOX_EXECUTION_ROUTED,
                backend=self._backend,
                session=session,
                extra={
                    "request_source": "mcp_request",
                    "result_status": "error",
                    "sync_enqueued": False,
                    "mcp_server": str(server_payload.get("name") or server_config_id),
                    "mcp_method": tool_name,
                    "error_type": _error_type_from_exception(exc),
                },
            )
            raise

        sync_enqueued = False
        if isinstance(result, dict) and result.get("status") != "error":
            if _sync_on_mcp_call():
                self._enqueue_post_sync_after_call(agent, source="mcp_request")
                sync_enqueued = True
        _track_execution_event(
            agent=agent,
            backend=self._backend,
            session=session,
            request_source="mcp_request",
            result=result,
            sync_enqueued=sync_enqueued,
            extra={
                "mcp_server": str(server_payload.get("name") or server_config_id),
                "mcp_method": tool_name,
            },
        )
        _log_tool_call("mcp_request", tool_name, params, agent_id=str(agent.id))
        return result

    def tool_request(self, agent, tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        session = self._ensure_session(agent, source="tool_request")
        params_payload = params or {}
        if tool_name == "python_exec":
            params_payload = dict(params_payload)
            existing_env = params_payload.get("env")
            if not isinstance(existing_env, dict):
                existing_env = {}
            merged_env, trusted_secret_keys = _merge_agent_env_vars_with_secret_keys(agent, existing_env)
            if merged_env:
                params_payload["env"] = merged_env
            if trusted_secret_keys:
                params_payload["trusted_env_keys"] = trusted_secret_keys
        try:
            result = self._backend.tool_request(agent, session, tool_name, params_payload)
        except Exception as exc:
            _track_sandbox_event(
                agent=agent,
                event=AnalyticsEvent.SANDBOX_EXECUTION_ROUTED,
                backend=self._backend,
                session=session,
                extra={
                    "request_source": "tool_request",
                    "result_status": "error",
                    "sync_enqueued": False,
                    "tool_name": tool_name,
                    "error_type": _error_type_from_exception(exc),
                },
            )
            raise

        sync_enqueued = False
        if isinstance(result, dict) and result.get("status") != "error":
            if _sync_on_tool_call():
                self._enqueue_post_sync_after_call(agent, source="tool_request")
                sync_enqueued = True
            export_path = result.get("export_path")
            if isinstance(export_path, str) and export_path.strip():
                response = _build_filespace_export_response(agent, export_path)
                if response.get("status") == "ok":
                    result = response
        _track_execution_event(
            agent=agent,
            backend=self._backend,
            session=session,
            request_source="tool_request",
            result=result,
            sync_enqueued=sync_enqueued,
            extra={"tool_name": tool_name},
        )
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
        try:
            update = self._backend.terminate(agent, session, reason=reason, delete_workspace=False)
            self._apply_session_update(session, update)
        except Exception as exc:
            _track_sandbox_event(
                agent=agent,
                event=AnalyticsEvent.SANDBOX_SESSION_STOPPED,
                backend=self._backend,
                session=session,
                extra={
                    "stop_reason": "terminate",
                    "result_status": "error",
                    "delete_workspace": False,
                    "final_state": session.state,
                    "error_type": _error_type_from_exception(exc),
                },
            )
            raise
        _track_sandbox_event(
            agent=agent,
            event=AnalyticsEvent.SANDBOX_SESSION_STOPPED,
            backend=self._backend,
            session=session,
            extra={
                "stop_reason": "terminate",
                "result_status": "ok",
                "delete_workspace": False,
                "final_state": session.state,
            },
        )
        return session

    def idle_stop_session(self, session: AgentComputeSession, *, reason: str = "idle_ttl") -> Dict[str, Any]:
        agent = session.agent
        delete_workspace = False
        try:
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
        except Exception as exc:
            _track_sandbox_event(
                agent=agent,
                event=AnalyticsEvent.SANDBOX_SESSION_STOPPED,
                backend=self._backend,
                session=session,
                extra={
                    "stop_reason": "idle_stop",
                    "result_status": "error",
                    "delete_workspace": delete_workspace,
                    "final_state": session.state,
                    "error_type": _error_type_from_exception(exc),
                },
            )
            raise

        result = {
            "status": "ok" if not (sync_failed or snapshot_failed) else "error",
            "sync_result": sync_result,
            "snapshot": snapshot_payload,
            "stopped": True,
        }
        error_type = None
        if sync_failed and snapshot_failed:
            error_type = "sync_and_snapshot_failed"
        elif sync_failed:
            error_type = "sync_failed"
        elif snapshot_failed:
            error_type = "snapshot_failed"
        properties = {
            "stop_reason": "idle_stop",
            "result_status": _execution_result_status(result),
            "delete_workspace": delete_workspace,
            "final_state": session.state,
        }
        if error_type:
            properties["error_type"] = error_type
        _track_sandbox_event(
            agent=agent,
            event=AnalyticsEvent.SANDBOX_SESSION_STOPPED,
            backend=self._backend,
            session=session,
            extra=properties,
        )
        return result

    def discover_mcp_tools(
        self,
        server_config_id: str,
        *,
        reason: str,
        agent: Optional[PersistentAgent] = None,
    ) -> Dict[str, Any]:
        server_payload, runtime = _build_mcp_server_payload(server_config_id, agent=agent)
        if not server_payload or runtime is None:
            return {"status": "error", "message": "MCP server config not available."}

        session: Optional[AgentComputeSession] = None
        if _requires_agent_pod_discovery(runtime):
            if agent is None:
                return {"status": "skipped", "message": "Sandboxed stdio discovery requires an agent context."}
            session = self._ensure_session(agent, source="discover_mcp_tools")

        result = self._backend.discover_mcp_tools(
            server_config_id,
            reason=reason,
            agent=agent,
            session=session,
            server_payload=server_payload,
        )
        if isinstance(result, dict) and result.get("status") == "ok":
            tools = result.get("tools")
            if isinstance(tools, list):
                from api.agent.tools.mcp_manager import get_mcp_manager

                manager = get_mcp_manager()
                sandbox_context = manager._sandbox_cache_context_for_runtime(runtime, agent)
                fingerprint = manager._build_tool_cache_fingerprint(runtime, sandbox_context=sandbox_context)
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
