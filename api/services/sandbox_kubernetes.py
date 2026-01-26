import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional

import requests
from django.conf import settings

from api.models import AgentComputeSession
from api.services.sandbox_compute import SandboxComputeBackend, SandboxComputeUnavailable, SandboxSessionUpdate

logger = logging.getLogger(__name__)

_SERVICE_ACCOUNT_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")


class KubernetesApiError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class KubernetesApiClient:
    def __init__(self, *, base_url: str, token: str, ca_path: Optional[str], timeout: int) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.ca_path = ca_path
        self.timeout = timeout

    def request_json(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        timeout: Optional[int] = None,
        allow_404: bool = False,
    ) -> Optional[Dict[str, Any]]:
        url = f"{self.base_url}{path}"
        headers = {"Authorization": f"Bearer {self.token}"}
        try:
            response = requests.request(
                method,
                url,
                json=json_body,
                headers=headers,
                timeout=timeout or self.timeout,
                verify=self.ca_path or True,
            )
        except requests.RequestException as exc:
            raise KubernetesApiError(0, f"Kubernetes API request failed: {exc}") from exc

        if response.status_code == 404 and allow_404:
            return None
        if response.status_code >= 400:
            raise KubernetesApiError(response.status_code, response.text)
        if not response.text:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise KubernetesApiError(response.status_code, "Invalid JSON from Kubernetes API") from exc


class KubernetesSandboxBackend(SandboxComputeBackend):
    def __init__(self) -> None:
        base_url = _k8s_api_url()
        token = _read_service_account_token()
        if not token:
            raise SandboxComputeUnavailable("Kubernetes service account token not available.")
        ca_path = _service_account_path("ca.crt")
        timeout = int(getattr(settings, "SANDBOX_COMPUTE_K8S_TIMEOUT_SECONDS", 30))
        self._client = KubernetesApiClient(base_url=base_url, token=token, ca_path=ca_path, timeout=timeout)
        self._namespace = _k8s_namespace()
        self._pod_image = getattr(settings, "SANDBOX_COMPUTE_POD_IMAGE", "")
        self._pod_service_account = getattr(settings, "SANDBOX_COMPUTE_POD_SERVICE_ACCOUNT", "gobii-sa")
        self._pod_runtime_class = getattr(settings, "SANDBOX_COMPUTE_POD_RUNTIME_CLASS", "gvisor")
        self._pod_configmap = getattr(settings, "SANDBOX_COMPUTE_POD_CONFIGMAP_NAME", "gobii-sandbox-common-env")
        self._pod_secret = getattr(settings, "SANDBOX_COMPUTE_POD_SECRET_NAME", "gobii-sandbox-env")
        self._pod_ready_timeout = int(getattr(settings, "SANDBOX_COMPUTE_POD_READY_TIMEOUT_SECONDS", 60))
        self._pvc_size = getattr(settings, "SANDBOX_COMPUTE_PVC_SIZE", "1Gi")
        self._pvc_storage_class = getattr(settings, "SANDBOX_COMPUTE_PVC_STORAGE_CLASS", "")
        self._snapshot_class = getattr(settings, "SANDBOX_COMPUTE_SNAPSHOT_CLASS", "")
        self._proxy_timeout = int(getattr(settings, "SANDBOX_COMPUTE_HTTP_TIMEOUT_SECONDS", 180))

        if not self._pod_image:
            raise SandboxComputeUnavailable("SANDBOX_COMPUTE_POD_IMAGE is required for kubernetes backend.")

    def deploy_or_resume(self, agent, session: AgentComputeSession) -> SandboxSessionUpdate:
        pod_name = _pod_name(agent.id)
        pvc_name = _pvc_name(agent.id)

        snapshot_name = session.workspace_snapshot.k8s_snapshot_name if session.workspace_snapshot else None
        if snapshot_name and not _resource_exists(self._client, _snapshot_path(self._namespace, snapshot_name)):
            logger.warning("Snapshot %s not found; provisioning fresh PVC for agent=%s", snapshot_name, agent.id)
            snapshot_name = None
        try:
            if not _resource_exists(self._client, _pvc_path(self._namespace, pvc_name)):
                self._create_pvc(pvc_name, snapshot_name=snapshot_name)

            pod = self._get_pod(pod_name)
            if not pod:
                self._create_pod(pod_name, pvc_name, agent_id=str(agent.id))
            else:
                phase = (pod.get("status") or {}).get("phase")
                if phase not in {"Running", "Pending"}:
                    self._delete_pod(pod_name)
                    self._create_pod(pod_name, pvc_name, agent_id=str(agent.id))
        except KubernetesApiError as exc:
            raise SandboxComputeUnavailable(f"Kubernetes scheduler failed: {exc}") from exc

        if not self._wait_for_pod_ready(pod_name):
            return SandboxSessionUpdate(state=AgentComputeSession.State.ERROR, pod_name=pod_name, namespace=self._namespace)

        return SandboxSessionUpdate(state=AgentComputeSession.State.RUNNING, pod_name=pod_name, namespace=self._namespace)

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
        if not session.pod_name:
            return {"status": "error", "message": "Sandbox pod not available."}
        timeout_value = _normalize_timeout(
            timeout,
            default=int(getattr(settings, "SANDBOX_COMPUTE_RUN_COMMAND_TIMEOUT_SECONDS", 120)),
        )
        request_timeout = max(self._proxy_timeout, timeout_value + 10)
        payload = {
            "agent_id": str(agent.id),
            "command": command,
            "cwd": cwd,
            "env": env,
            "timeout": timeout_value,
            "interactive": interactive,
        }
        return self._proxy_post(session.pod_name, "/sandbox/compute/run_command", payload, timeout=request_timeout)

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
        if not session.pod_name:
            return {"status": "error", "message": "Sandbox pod not available."}
        payload = {
            "agent_id": str(agent.id),
            "server_id": server_config_id,
            "tool_name": tool_name,
            "params": params,
        }
        return self._proxy_post(session.pod_name, "/sandbox/compute/mcp_request", payload)

    def tool_request(
        self,
        agent,
        session: AgentComputeSession,
        tool_name: str,
        params: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not session.pod_name:
            return {"status": "error", "message": "Sandbox pod not available."}
        params_payload = params or {}
        request_timeout = self._proxy_timeout
        if tool_name == "python_exec":
            normalized = _normalize_timeout(
                params_payload.get("timeout_seconds"),
                default=int(getattr(settings, "SANDBOX_COMPUTE_PYTHON_DEFAULT_TIMEOUT_SECONDS", 30)),
                maximum=int(getattr(settings, "SANDBOX_COMPUTE_PYTHON_MAX_TIMEOUT_SECONDS", 120)),
            )
            params_payload = dict(params_payload)
            params_payload["timeout_seconds"] = normalized
            request_timeout = max(self._proxy_timeout, normalized + 10)
        payload = {
            "agent_id": str(agent.id),
            "tool_name": tool_name,
            "params": params_payload,
        }
        return self._proxy_post(session.pod_name, "/sandbox/compute/tool_request", payload, timeout=request_timeout)

    def sync_filespace(
        self,
        agent,
        session: AgentComputeSession,
        *,
        direction: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not session.pod_name:
            return {"status": "error", "message": "Sandbox pod not available."}
        body = payload or {}
        body.update({"agent_id": str(agent.id), "direction": direction})
        return self._proxy_post(session.pod_name, "/sandbox/compute/sync_filespace", body)

    def snapshot_workspace(self, agent, session: AgentComputeSession, *, reason: str) -> Dict[str, Any]:
        pvc_name = _pvc_name(agent.id)
        if not _resource_exists(self._client, _pvc_path(self._namespace, pvc_name)):
            return {"status": "error", "message": "Workspace PVC not found."}
        snapshot_name = _snapshot_name(agent.id)
        body = {
            "apiVersion": "snapshot.storage.k8s.io/v1",
            "kind": "VolumeSnapshot",
            "metadata": {
                "name": snapshot_name,
                "labels": {
                    "app": "sandbox-compute",
                    "agent_id": str(agent.id),
                },
            },
            "spec": {
                "source": {"persistentVolumeClaimName": pvc_name},
            },
        }
        if self._snapshot_class:
            body["spec"]["volumeSnapshotClassName"] = self._snapshot_class

        try:
            self._client.request_json("POST", _snapshot_collection_path(self._namespace), json_body=body)
        except KubernetesApiError as exc:
            return {"status": "error", "message": f"Snapshot create failed: {exc}"}

        ready = self._wait_for_snapshot_ready(snapshot_name)
        if not ready:
            return {"status": "error", "message": "Snapshot did not become ready in time."}

        return {
            "status": "ok",
            "snapshot_name": snapshot_name,
        }

    def terminate(
        self,
        agent,
        session: AgentComputeSession,
        *,
        reason: str,
        delete_workspace: bool = False,
    ) -> SandboxSessionUpdate:
        pod_name = session.pod_name or _pod_name(agent.id)
        self._delete_pod(pod_name)
        if delete_workspace:
            pvc_name = _pvc_name(agent.id)
            self._delete_pvc(pvc_name)
        return SandboxSessionUpdate(state=AgentComputeSession.State.STOPPED, pod_name=pod_name, namespace=self._namespace)

    def discover_mcp_tools(self, server_config_id: str, *, reason: str) -> Dict[str, Any]:
        return {"status": "error", "message": "MCP discovery must run inside sandbox pod."}

    def _proxy_post(
        self,
        pod_name: str,
        path: str,
        payload: Dict[str, Any],
        *,
        timeout: Optional[int] = None,
    ) -> Dict[str, Any]:
        proxy_path = _pod_proxy_path(self._namespace, pod_name, path)
        try:
            response = self._client.request_json(
                "POST",
                proxy_path,
                json_body=payload,
                timeout=timeout or self._proxy_timeout,
            )
        except KubernetesApiError as exc:
            return {"status": "error", "message": f"Sandbox proxy request failed: {exc}"}
        if response is None:
            return {"status": "error", "message": "Sandbox proxy returned empty response."}
        return response

    def _get_pod(self, pod_name: str) -> Optional[Dict[str, Any]]:
        try:
            return self._client.request_json("GET", _pod_path(self._namespace, pod_name), allow_404=True)
        except KubernetesApiError as exc:
            logger.warning("Failed to fetch pod %s: %s", pod_name, exc)
            return None

    def _create_pod(self, pod_name: str, pvc_name: str, *, agent_id: str) -> None:
        body = _build_pod_manifest(
            pod_name=pod_name,
            pvc_name=pvc_name,
            namespace=self._namespace,
            image=self._pod_image,
            runtime_class=self._pod_runtime_class,
            service_account=self._pod_service_account,
            configmap_name=self._pod_configmap,
            secret_name=self._pod_secret,
            agent_id=agent_id,
        )
        try:
            self._client.request_json("POST", _pod_collection_path(self._namespace), json_body=body)
        except KubernetesApiError as exc:
            if exc.status_code != 409:
                raise

    def _delete_pod(self, pod_name: str) -> None:
        try:
            self._client.request_json("DELETE", _pod_path(self._namespace, pod_name), allow_404=True)
        except KubernetesApiError as exc:
            logger.warning("Failed to delete pod %s: %s", pod_name, exc)

    def _create_pvc(self, pvc_name: str, *, snapshot_name: Optional[str]) -> None:
        body = _build_pvc_manifest(
            pvc_name=pvc_name,
            namespace=self._namespace,
            size=self._pvc_size,
            storage_class=self._pvc_storage_class,
            snapshot_name=snapshot_name,
        )
        try:
            self._client.request_json("POST", _pvc_collection_path(self._namespace), json_body=body)
        except KubernetesApiError as exc:
            if exc.status_code != 409:
                raise

    def _delete_pvc(self, pvc_name: str) -> None:
        try:
            self._client.request_json("DELETE", _pvc_path(self._namespace, pvc_name), allow_404=True)
        except KubernetesApiError as exc:
            logger.warning("Failed to delete PVC %s: %s", pvc_name, exc)

    def _wait_for_pod_ready(self, pod_name: str) -> bool:
        deadline = time.time() + self._pod_ready_timeout
        while time.time() < deadline:
            pod = self._get_pod(pod_name)
            if not pod:
                time.sleep(2)
                continue
            status = pod.get("status") or {}
            phase = status.get("phase")
            if phase == "Running":
                for condition in status.get("conditions", []):
                    if condition.get("type") == "Ready" and condition.get("status") == "True":
                        return True
            time.sleep(2)
        return False

    def _wait_for_snapshot_ready(self, snapshot_name: str) -> bool:
        deadline = time.time() + int(getattr(settings, "SANDBOX_COMPUTE_SNAPSHOT_TIMEOUT_SECONDS", 60))
        while time.time() < deadline:
            try:
                snapshot = self._client.request_json(
                    "GET",
                    _snapshot_path(self._namespace, snapshot_name),
                    allow_404=True,
                )
            except KubernetesApiError as exc:
                logger.warning("Snapshot status check failed: %s", exc)
                time.sleep(2)
                continue
            if snapshot and (snapshot.get("status") or {}).get("readyToUse") is True:
                return True
            time.sleep(2)
        return False


def _read_service_account_token() -> str:
    path = _service_account_path("token")
    if not path:
        return ""
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ""


def _service_account_path(filename: str) -> Optional[str]:
    candidate = _SERVICE_ACCOUNT_DIR / filename
    if candidate.exists():
        return str(candidate)
    return None


def _k8s_api_url() -> str:
    explicit = getattr(settings, "SANDBOX_COMPUTE_K8S_API_URL", "") or os.environ.get("SANDBOX_COMPUTE_K8S_API_URL")
    if explicit:
        return explicit.rstrip("/")
    host = os.environ.get("KUBERNETES_SERVICE_HOST")
    port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
    if not host:
        raise SandboxComputeUnavailable("Kubernetes service host not configured.")
    return f"https://{host}:{port}"


def _k8s_namespace() -> str:
    explicit = getattr(settings, "SANDBOX_COMPUTE_K8S_NAMESPACE", "") or os.environ.get("SANDBOX_COMPUTE_K8S_NAMESPACE")
    if explicit:
        return explicit
    path = _service_account_path("namespace")
    if path:
        try:
            return Path(path).read_text().strip()
        except OSError:
            pass
    return "default"


def _resource_exists(client: KubernetesApiClient, path: str) -> bool:
    try:
        return client.request_json("GET", path, allow_404=True) is not None
    except KubernetesApiError:
        return False


def _normalize_timeout(value: Any, *, default: int, maximum: Optional[int] = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    if parsed <= 0:
        parsed = default
    if maximum is not None:
        return min(parsed, maximum)
    return parsed


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9-]", "-", value.lower())
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")
    return cleaned


def _pod_name(agent_id: Any) -> str:
    return _slugify(f"sandbox-agent-{agent_id}")


def _pvc_name(agent_id: Any) -> str:
    return _slugify(f"sandbox-workspace-{agent_id}")


def _snapshot_name(agent_id: Any) -> str:
    stamp = time.strftime("%Y%m%d%H%M%S", time.gmtime())
    return _slugify(f"sandbox-snap-{str(agent_id)[:8]}-{stamp}")


def _pod_collection_path(namespace: str) -> str:
    return f"/api/v1/namespaces/{namespace}/pods"


def _pod_path(namespace: str, pod_name: str) -> str:
    return f"/api/v1/namespaces/{namespace}/pods/{pod_name}"


def _pod_proxy_path(namespace: str, pod_name: str, path: str) -> str:
    suffix = path if path.startswith("/") else f"/{path}"
    return f"/api/v1/namespaces/{namespace}/pods/{pod_name}/proxy{suffix}"


def _pvc_collection_path(namespace: str) -> str:
    return f"/api/v1/namespaces/{namespace}/persistentvolumeclaims"


def _pvc_path(namespace: str, pvc_name: str) -> str:
    return f"/api/v1/namespaces/{namespace}/persistentvolumeclaims/{pvc_name}"


def _snapshot_collection_path(namespace: str) -> str:
    return f"/apis/snapshot.storage.k8s.io/v1/namespaces/{namespace}/volumesnapshots"


def _snapshot_path(namespace: str, snapshot_name: str) -> str:
    return f"/apis/snapshot.storage.k8s.io/v1/namespaces/{namespace}/volumesnapshots/{snapshot_name}"


def _build_pvc_manifest(
    *,
    pvc_name: str,
    namespace: str,
    size: str,
    storage_class: str,
    snapshot_name: Optional[str],
) -> Dict[str, Any]:
    spec: Dict[str, Any] = {
        "accessModes": ["ReadWriteOnce"],
        "resources": {"requests": {"storage": size}},
    }
    if storage_class:
        spec["storageClassName"] = storage_class
    if snapshot_name:
        spec["dataSource"] = {
            "name": snapshot_name,
            "kind": "VolumeSnapshot",
            "apiGroup": "snapshot.storage.k8s.io",
        }
    return {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": {
            "name": pvc_name,
            "namespace": namespace,
            "labels": {
                "app": "sandbox-compute",
            },
        },
        "spec": spec,
    }


def _build_pod_manifest(
    *,
    pod_name: str,
    pvc_name: str,
    namespace: str,
    image: str,
    runtime_class: str,
    service_account: str,
    configmap_name: str,
    secret_name: str,
    agent_id: str,
) -> Dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": pod_name,
            "namespace": namespace,
            "labels": {
                "app": "sandbox-compute",
                "component": "sandbox-agent",
                "agent_id": agent_id,
            },
        },
        "spec": {
            "serviceAccountName": service_account,
            "runtimeClassName": runtime_class,
            "terminationGracePeriodSeconds": 300,
            "securityContext": {
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "containers": [
                {
                    "name": "sandbox-supervisor",
                    "image": image,
                    "imagePullPolicy": "Always",
                    "ports": [{"containerPort": 8080}],
                    "envFrom": [
                        {"secretRef": {"name": secret_name}},
                        {"configMapRef": {"name": configmap_name}},
                    ],
                    "securityContext": {
                        "allowPrivilegeEscalation": False,
                        "runAsNonRoot": True,
                        "runAsUser": 1000,
                        "runAsGroup": 1000,
                        "capabilities": {"drop": ["ALL"]},
                    },
                    "volumeMounts": [
                        {"name": "workspace", "mountPath": "/workspace"},
                    ],
                    "readinessProbe": {
                        "httpGet": {"path": "/healthz", "port": 8080},
                        "initialDelaySeconds": 10,
                        "periodSeconds": 10,
                        "failureThreshold": 3,
                    },
                }
            ],
            "volumes": [
                {
                    "name": "workspace",
                    "persistentVolumeClaim": {"claimName": pvc_name},
                }
            ],
        },
    }
