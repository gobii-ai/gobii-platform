import logging
import os
import re
from typing import Optional

from django.conf import settings
from django.utils import timezone

try:
    from kubernetes import client, config
    from kubernetes.stream import stream
    from kubernetes.client.rest import ApiException
except Exception:  # pragma: no cover - defensive import guard for local tooling
    client = None  # type: ignore[assignment]
    config = None  # type: ignore[assignment]
    stream = None  # type: ignore[assignment]
    ApiException = Exception  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_CONFIG_LOADED = False


class SandboxK8sError(RuntimeError):
    pass


def _load_k8s_config() -> None:
    global _CONFIG_LOADED
    if _CONFIG_LOADED:
        return

    if config is None:
        raise SandboxK8sError("kubernetes client is not available")

    try:
        config.load_incluster_config()
        _CONFIG_LOADED = True
        return
    except Exception:
        pass

    try:
        config.load_kube_config()
        _CONFIG_LOADED = True
    except Exception as exc:
        raise SandboxK8sError("Failed to load Kubernetes configuration") from exc


def _core_v1() -> "client.CoreV1Api":
    _load_k8s_config()
    return client.CoreV1Api()


def _sanitize_name(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9-]", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    if len(value) > 63:
        value = value[:63].rstrip("-")
    return value


def sandbox_namespace() -> str:
    namespace = getattr(settings, "SANDBOX_NAMESPACE", "")
    if not namespace:
        namespace = os.getenv("SANDBOX_NAMESPACE", "")
    if not namespace:
        raise SandboxK8sError("Sandbox namespace is not configured")
    return namespace


def sandbox_pvc_name(agent_id: str) -> str:
    return _sanitize_name(f"sandbox-workspace-{agent_id}")


def sandbox_pod_name(agent_id: str) -> str:
    return _sanitize_name(f"sandbox-agent-{agent_id}")


def ensure_workspace_pvc(agent_id: str, *, namespace: str) -> str:
    pvc_name = sandbox_pvc_name(agent_id)
    core = _core_v1()

    def _create_workspace_pvc() -> str:
        storage_class = getattr(settings, "SANDBOX_STORAGE_CLASS", "") or None
        storage_size = getattr(settings, "SANDBOX_WORKSPACE_SIZE", "1Gi")

        pvc = client.V1PersistentVolumeClaim(
            metadata=client.V1ObjectMeta(
                name=pvc_name,
                labels={"app": "sandbox-compute", "agent_id": str(agent_id)},
            ),
            spec=client.V1PersistentVolumeClaimSpec(
                access_modes=["ReadWriteOnce"],
                resources=client.V1ResourceRequirements(requests={"storage": storage_size}),
                storage_class_name=storage_class,
            ),
        )

        try:
            core.create_namespaced_persistent_volume_claim(namespace=namespace, body=pvc)
        except ApiException as exc:
            status = getattr(exc, "status", None)
            if status == 409:
                return pvc_name
            if status == 403:
                raise SandboxK8sError(
                    "Sandbox workspace PVC access is forbidden by Kubernetes RBAC. "
                    f"Grant the sandbox service account get/create access to persistentvolumeclaims in {namespace}."
                ) from exc
            raise SandboxK8sError(f"Failed to create workspace PVC {pvc_name}: {exc}") from exc
        return pvc_name

    try:
        core.read_namespaced_persistent_volume_claim(name=pvc_name, namespace=namespace)
        return pvc_name
    except ApiException as exc:
        status = getattr(exc, "status", None)
        if status in {403, 404}:
            return _create_workspace_pvc()
        raise SandboxK8sError(f"Failed to read workspace PVC {pvc_name}: {exc}") from exc


def _build_pod(agent_id: str, *, namespace: str, pvc_name: str) -> "client.V1Pod":
    pod_name = sandbox_pod_name(agent_id)
    labels = {
        "app": "sandbox-compute",
        "agent_id": str(agent_id),
    }
    volume_name = "workspace"

    supervisor_image = getattr(settings, "SANDBOX_SUPERVISOR_IMAGE", "")
    supervisor_port = int(getattr(settings, "SANDBOX_SUPERVISOR_PORT", 8081))
    runtime_class = getattr(settings, "SANDBOX_RUNTIME_CLASS", "gvisor")

    container = client.V1Container(
        name="sandbox-supervisor",
        image=supervisor_image,
        image_pull_policy="Always",
        command=["python", "-m", "api.sandbox.supervisor"],
        env=[
            client.V1EnvVar(name="SANDBOX_WORKDIR", value="/workspace"),
            client.V1EnvVar(name="SANDBOX_SUPERVISOR_PORT", value=str(supervisor_port)),
        ],
        ports=[client.V1ContainerPort(container_port=supervisor_port)],
        volume_mounts=[
            client.V1VolumeMount(name=volume_name, mount_path="/workspace"),
        ],
    )

    pod_security = client.V1PodSecurityContext(run_as_user=0, run_as_group=0)

    pod_spec = client.V1PodSpec(
        containers=[container],
        restart_policy="Always",
        runtime_class_name=runtime_class,
        volumes=[
            client.V1Volume(
                name=volume_name,
                persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(claim_name=pvc_name),
            )
        ],
        security_context=pod_security,
        termination_grace_period_seconds=30,
    )

    return client.V1Pod(
        metadata=client.V1ObjectMeta(name=pod_name, namespace=namespace, labels=labels),
        spec=pod_spec,
    )


def ensure_sandbox_pod(agent_id: str, *, namespace: str, pvc_name: str) -> str:
    core = _core_v1()
    pod_name = sandbox_pod_name(agent_id)

    try:
        existing = core.read_namespaced_pod(name=pod_name, namespace=namespace)
    except ApiException as exc:
        if getattr(exc, "status", None) != 404:
            raise
        existing = None

    if existing is not None:
        phase = getattr(existing.status, "phase", "") or ""
        if phase.lower() == "running":
            return pod_name
        try:
            core.delete_namespaced_pod(name=pod_name, namespace=namespace)
        except ApiException:
            logger.exception("Failed to delete stale sandbox pod %s", pod_name)

    pod = _build_pod(agent_id, namespace=namespace, pvc_name=pvc_name)
    core.create_namespaced_pod(namespace=namespace, body=pod)
    return pod_name


def delete_sandbox_pod(agent_id: str, *, namespace: str) -> None:
    core = _core_v1()
    pod_name = sandbox_pod_name(agent_id)
    try:
        core.delete_namespaced_pod(name=pod_name, namespace=namespace)
    except ApiException as exc:
        if getattr(exc, "status", None) != 404:
            raise


def exec_in_pod(
    agent_id: str,
    *,
    namespace: str,
    command: list[str],
    timeout_seconds: int,
) -> dict:
    if stream is None:
        raise SandboxK8sError("kubernetes stream helper is unavailable")

    core = _core_v1()
    pod_name = sandbox_pod_name(agent_id)
    started_at = timezone.now()

    resp = stream(
        core.connect_get_namespaced_pod_exec,
        pod_name,
        namespace,
        command=command,
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False,
        _preload_content=False,
        _request_timeout=timeout_seconds,
    )

    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []

    try:
        while resp.is_open():
            resp.update(timeout=1)
            if resp.peek_stdout():
                stdout_chunks.append(resp.read_stdout())
            if resp.peek_stderr():
                stderr_chunks.append(resp.read_stderr())
    finally:
        resp.close()

    duration = (timezone.now() - started_at).total_seconds()
    return {
        "stdout": "".join(stdout_chunks),
        "stderr": "".join(stderr_chunks),
        "duration_seconds": duration,
    }
