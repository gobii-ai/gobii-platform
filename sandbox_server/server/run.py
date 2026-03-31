import json
import logging
import subprocess
import sys
import time
from typing import Any, Dict, Optional, Tuple

from sandbox_server.sandbox_utils import normalize_timeout as _normalize_timeout
from sandbox_server.config import (
    _agent_workspace,
    _python_default_timeout_seconds,
    _python_max_timeout_seconds,
    _run_command_timeout_seconds,
    _sandbox_env,
    _stdio_max_bytes,
)
from sandbox_server.manifest import _proxy_env_from_manifest, _store_proxy_env
from sandbox_server.workspace import (
    _elapsed_ms,
    _normalize_workspace_path,
    _require_agent_id,
    _session_update,
    _trace_context,
)

logger = logging.getLogger(__name__)


def _sandbox_env_with_proxy_manifest(
    agent_root,
    extra_env: Optional[Dict[str, str]] = None,
    *,
    trusted_env_keys: Optional[list[str]] = None,
) -> Dict[str, str]:
    env = _sandbox_env(agent_root, extra_env, trusted_env_keys=trusted_env_keys)
    proxy_env = _proxy_env_from_manifest(agent_root)
    if proxy_env:
        env.update(proxy_env)
    return env


def _truncate_streams(stdout: str, stderr: str) -> Tuple[str, str]:
    max_bytes = _stdio_max_bytes()
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


def _handle_deploy_or_resume(payload: Dict[str, Any]) -> Dict[str, Any]:
    started_at = time.monotonic()
    agent_id, error = _require_agent_id(payload)
    if error:
        return error
    agent_root = _agent_workspace(agent_id)
    proxy_env_updated = _store_proxy_env(agent_root, payload)
    trace_id, _traceparent = _trace_context(payload)
    logger.info(
        "Sandbox deploy_or_resume agent=%s status=ok duration_ms=%s proxy_env_updated=%s trace_id=%s",
        agent_id,
        _elapsed_ms(started_at),
        proxy_env_updated,
        trace_id,
    )
    return _session_update("running")


def _handle_run_command(payload: Dict[str, Any]) -> Dict[str, Any]:
    start = time.time()
    agent_id, error = _require_agent_id(payload)
    if error:
        return error
    agent_root = _agent_workspace(agent_id)
    _store_proxy_env(agent_root, payload)
    command = payload.get("command")
    if not isinstance(command, str) or not command.strip():
        return {"status": "error", "message": "Missing required parameter: command"}
    if payload.get("interactive") is True:
        return {"status": "error", "message": "Interactive sessions are not supported yet."}
    cwd = payload.get("cwd")
    if isinstance(cwd, str) and cwd.strip():
        cwd_path, _ = _normalize_workspace_path(agent_root, cwd)
        if cwd_path is None:
            return {"status": "error", "message": "Invalid cwd path."}
        cwd = str(cwd_path)
    else:
        cwd = None
    env = payload.get("env") if isinstance(payload.get("env"), dict) else None
    trusted_env_keys = payload.get("trusted_env_keys")
    if not isinstance(trusted_env_keys, list):
        trusted_env_keys = []
    timeout = _normalize_timeout(
        payload.get("timeout"),
        default=_run_command_timeout_seconds(),
    )

    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=cwd or None,
            env=_sandbox_env_with_proxy_manifest(agent_root, env, trusted_env_keys=trusted_env_keys),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        duration_ms = int((time.time() - start) * 1000)
        trace_id, _traceparent = _trace_context(payload)
        logger.exception(
            "Sandbox run_command timed out agent=%s command=%s cwd=%s duration_ms=%s trace_id=%s",
            agent_id,
            command,
            cwd,
            duration_ms,
            trace_id,
        )
        return {"status": "error", "message": "Command timed out."}
    except OSError as exc:
        duration_ms = int((time.time() - start) * 1000)
        trace_id, _traceparent = _trace_context(payload)
        logger.exception(
            "Sandbox run_command failed to start agent=%s command=%s cwd=%s duration_ms=%s trace_id=%s",
            agent_id,
            command,
            cwd,
            duration_ms,
            trace_id,
        )
        return {"status": "error", "message": f"Command failed to start: {exc}"}

    stdout, stderr = _truncate_streams(result.stdout or "", result.stderr or "")
    if result.returncode != 0:
        response = _build_nonzero_exit_error_payload(
            process_name="Command",
            exit_code=result.returncode,
            stdout=stdout,
            stderr=stderr,
        )
    else:
        response = {
            "status": "ok",
            "exit_code": result.returncode,
            "stdout": stdout,
            "stderr": stderr,
        }
    duration_ms = int((time.time() - start) * 1000)
    trace_id, _traceparent = _trace_context(payload)
    logger.info(
        "Sandbox run_command agent=%s command=%s cwd=%s status=%s exit_code=%s duration_ms=%s trace_id=%s result=%s",
        agent_id,
        command,
        cwd,
        response.get("status"),
        response.get("exit_code"),
        duration_ms,
        trace_id,
        json.dumps(response, sort_keys=True, ensure_ascii=True),
    )
    return response


def _handle_python_exec(payload: Dict[str, Any]) -> Dict[str, Any]:
    start = time.time()
    agent_id, error = _require_agent_id(payload)
    if error:
        return error
    agent_root = _agent_workspace(agent_id)
    _store_proxy_env(agent_root, payload)
    code = payload.get("code")
    if not isinstance(code, str) or not code.strip():
        return {"status": "error", "message": "Missing required parameter: code"}
    timeout = _normalize_timeout(
        payload.get("timeout_seconds"),
        default=_python_default_timeout_seconds(),
        maximum=_python_max_timeout_seconds(),
    )
    extra_env = payload.get("env") if isinstance(payload.get("env"), dict) else None
    trusted_env_keys = payload.get("trusted_env_keys")
    if not isinstance(trusted_env_keys, list):
        trusted_env_keys = []

    try:
        result = subprocess.run(
            [sys.executable, "-c", code],
            env=_sandbox_env_with_proxy_manifest(agent_root, extra_env, trusted_env_keys=trusted_env_keys),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        duration_ms = int((time.time() - start) * 1000)
        trace_id, _traceparent = _trace_context(payload)
        logger.exception(
            "Sandbox python_exec timed out agent=%s duration_ms=%s trace_id=%s",
            agent_id,
            duration_ms,
            trace_id,
        )
        return {"status": "error", "message": "Python execution timed out."}
    except OSError as exc:
        duration_ms = int((time.time() - start) * 1000)
        trace_id, _traceparent = _trace_context(payload)
        logger.exception(
            "Sandbox python_exec failed to start agent=%s duration_ms=%s trace_id=%s",
            agent_id,
            duration_ms,
            trace_id,
        )
        return {"status": "error", "message": f"Python execution failed to start: {exc}"}

    stdout, stderr = _truncate_streams(result.stdout or "", result.stderr or "")
    if result.returncode != 0:
        response = _build_nonzero_exit_error_payload(
            process_name="Python",
            exit_code=result.returncode,
            stdout=stdout,
            stderr=stderr,
        )
    else:
        response = {
            "status": "ok",
            "exit_code": result.returncode,
            "stdout": stdout,
            "stderr": stderr,
        }
    return response


def _handle_terminate(payload: Dict[str, Any]) -> Dict[str, Any]:
    started_at = time.monotonic()
    agent_id, error = _require_agent_id(payload)
    if error:
        return error
    agent_root = _agent_workspace(agent_id)
    proxy_env_updated = _store_proxy_env(agent_root, payload)
    trace_id, _traceparent = _trace_context(payload)
    logger.info(
        "Sandbox terminate agent=%s status=ok duration_ms=%s proxy_env_updated=%s trace_id=%s",
        agent_id,
        _elapsed_ms(started_at),
        proxy_env_updated,
        trace_id,
    )
    return _session_update("stopped")
