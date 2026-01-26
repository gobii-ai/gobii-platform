import logging
import shlex
from typing import Any, Dict, Optional

from django.utils import timezone

from api.models import AgentComputeSession, PersistentAgent
from api.services.compute_sessions import (
    get_or_create_session,
    mark_session_error,
    mark_session_running,
    mark_session_stopped,
    touch_session,
)
from api.services.sandbox_k8s import (
    SandboxK8sError,
    delete_sandbox_pod,
    ensure_sandbox_pod,
    ensure_workspace_pvc,
    exec_in_pod,
    sandbox_namespace,
)

logger = logging.getLogger(__name__)


def _build_shell_command(command: str | list[str], cwd: Optional[str], env: Optional[Dict[str, str]]) -> list[str]:
    if isinstance(command, list):
        command_str = " ".join(shlex.quote(str(part)) for part in command)
    else:
        command_str = command

    export_parts = []
    if env:
        for key, value in env.items():
            export_parts.append(f"{key}={shlex.quote(str(value))}")

    segments = []
    if export_parts:
        segments.append("export " + " ".join(export_parts))
    if cwd:
        segments.append(f"cd {shlex.quote(cwd)}")
    segments.append(command_str)
    segments.append('printf "\\n__gobii_exit_code__%s\\n" $?')
    shell = "; ".join(segments)

    return ["/bin/sh", "-lc", shell]


def deploy_or_resume(agent: PersistentAgent) -> Dict[str, Any]:
    namespace = sandbox_namespace()
    session = get_or_create_session(agent, namespace=namespace)

    try:
        pvc_name = ensure_workspace_pvc(str(agent.id), namespace=namespace)
        pod_name = ensure_sandbox_pod(str(agent.id), namespace=namespace, pvc_name=pvc_name)
        mark_session_running(session, pod_name=pod_name, workspace_pvc=pvc_name)
        return {
            "agent_id": str(agent.id),
            "pod_name": pod_name,
            "namespace": namespace,
            "workspace_pvc": pvc_name,
            "state": session.state,
        }
    except SandboxK8sError as exc:
        mark_session_error(session, error_message=str(exc))
        raise


def run_command(
    agent: PersistentAgent,
    *,
    command: str | list[str],
    cwd: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    timeout_seconds: Optional[int] = None,
) -> Dict[str, Any]:
    namespace = sandbox_namespace()
    session = get_or_create_session(agent, namespace=namespace)

    if session.state != AgentComputeSession.State.RUNNING:
        deploy_or_resume(agent)

    exec_timeout = int(timeout_seconds or 30)
    exec_timeout = max(1, min(exec_timeout, 120))

    cmd = _build_shell_command(command, cwd, env)
    result = exec_in_pod(str(agent.id), namespace=namespace, command=cmd, timeout_seconds=exec_timeout)
    touch_session(session, now=timezone.now())

    stdout = result.get("stdout", "") or ""
    stderr = result.get("stderr", "") or ""
    exit_code = 0
    marker = "__gobii_exit_code__"
    if marker in stdout:
        body, _, tail = stdout.rpartition(marker)
        stdout = body.rstrip("\n")
        try:
            exit_code = int(tail.strip().splitlines()[0])
        except (ValueError, IndexError):
            exit_code = 0

    return {
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": exit_code,
        "duration_seconds": result.get("duration_seconds"),
    }


def terminate(agent: PersistentAgent, *, reason: Optional[str] = None) -> Dict[str, Any]:
    namespace = sandbox_namespace()
    session = AgentComputeSession.objects.filter(agent=agent).first()
    if session is None:
        return {"status": "missing", "message": "No compute session exists."}

    try:
        delete_sandbox_pod(str(agent.id), namespace=namespace)
    except Exception:
        logger.exception("Failed to delete sandbox pod for agent %s", agent.id)
        mark_session_error(session, error_message="Failed to delete sandbox pod")
        return {"status": "error", "message": "Failed to delete sandbox pod."}

    mark_session_stopped(session, error_message=reason)
    return {"status": "stopped", "message": "Sandbox terminated."}
