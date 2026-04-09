import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional

import requests
from django.conf import settings

from api.models import AgentComputeSession, MCPServerConfig
from api.sandbox_utils import monotonic_elapsed_ms as _elapsed_ms, normalize_timeout as _normalize_timeout
from api.services.sandbox_compute import (
    SandboxComputeBackend,
    SandboxComputeUnavailable,
    SandboxSessionUpdate,
    _requires_agent_pod_discovery,
)
from api.services.system_settings import (
    get_sandbox_compute_pod_image,
    get_sandbox_compute_require_proxy,
    get_sandbox_egress_proxy_pod_image,
)

logger = logging.getLogger(__name__)

_SERVICE_ACCOUNT_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")
_SANDBOX_SERVICE_PORT = 8080
_DEFAULT_EGRESS_PROXY_HTTP_PORT = 3128
_DEFAULT_EGRESS_PROXY_SOCKS_PORT = 1080


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
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> Optional[Dict[str, Any]]:
        url = f"{self.base_url}{path}"
        headers = {"Authorization": f"Bearer {self.token}"}
        if extra_headers:
            headers.update(extra_headers)
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
    def custom_tool_workspace_root(self, agent_id: Any) -> str:
        return "/workspace"

    def __init__(self) -> None:
        base_url = _k8s_api_url()
        token = _read_service_account_token()
        if not token:
            raise SandboxComputeUnavailable("Kubernetes service account token not available.")
        ca_path = _service_account_path("ca.crt")
        timeout = int(getattr(settings, "SANDBOX_COMPUTE_K8S_TIMEOUT_SECONDS", 30))
        self._client = KubernetesApiClient(base_url=base_url, token=token, ca_path=ca_path, timeout=timeout)
        self._namespace = _k8s_namespace()
        self._compute_api_token = getattr(settings, "SANDBOX_COMPUTE_API_TOKEN", "") or ""
        self._pod_image = get_sandbox_compute_pod_image()
        self._pod_service_account = getattr(settings, "SANDBOX_COMPUTE_POD_SERVICE_ACCOUNT", "") or ""
        self._pod_runtime_class = getattr(settings, "SANDBOX_COMPUTE_POD_RUNTIME_CLASS", "gvisor")
        self._pod_configmap = getattr(settings, "SANDBOX_COMPUTE_POD_CONFIGMAP_NAME", "gobii-sandbox-common-env")
        self._pod_secret = getattr(settings, "SANDBOX_COMPUTE_POD_SECRET_NAME", "gobii-sandbox-env")
        self._egress_proxy_image = get_sandbox_egress_proxy_pod_image()
        self._egress_proxy_port = int(
            getattr(settings, "SANDBOX_EGRESS_PROXY_POD_PORT", _DEFAULT_EGRESS_PROXY_HTTP_PORT)
        )
        self._egress_proxy_service_port = int(
            getattr(settings, "SANDBOX_EGRESS_PROXY_SERVICE_PORT", self._egress_proxy_port)
        )
        self._egress_proxy_socks_port = int(
            getattr(settings, "SANDBOX_EGRESS_PROXY_SOCKS_POD_PORT", _DEFAULT_EGRESS_PROXY_SOCKS_PORT)
        )
        self._egress_proxy_socks_service_port = int(
            getattr(settings, "SANDBOX_EGRESS_PROXY_SOCKS_SERVICE_PORT", self._egress_proxy_socks_port)
        )
        self._egress_proxy_runtime_class = getattr(settings, "SANDBOX_EGRESS_PROXY_POD_RUNTIME_CLASS", "") or ""
        self._egress_proxy_service_account = (
            getattr(settings, "SANDBOX_EGRESS_PROXY_POD_SERVICE_ACCOUNT", "") or ""
        )
        self._no_proxy = getattr(settings, "SANDBOX_COMPUTE_NO_PROXY", "") or ""
        self._pod_ready_timeout = int(getattr(settings, "SANDBOX_COMPUTE_POD_READY_TIMEOUT_SECONDS", 60))
        self._service_routable_timeout = int(settings.SANDBOX_COMPUTE_SERVICE_ROUTABLE_TIMEOUT_SECONDS)
        self._pvc_size = getattr(settings, "SANDBOX_COMPUTE_PVC_SIZE", "1Gi")
        self._pvc_storage_class = getattr(settings, "SANDBOX_COMPUTE_PVC_STORAGE_CLASS", "")
        self._snapshot_class = getattr(settings, "SANDBOX_COMPUTE_SNAPSHOT_CLASS", "")
        self._proxy_timeout = int(getattr(settings, "SANDBOX_COMPUTE_HTTP_TIMEOUT_SECONDS", 180))
        self._mcp_timeout = int(getattr(settings, "SANDBOX_COMPUTE_MCP_REQUEST_TIMEOUT_SECONDS", self._proxy_timeout))
        self._tool_timeout = int(getattr(settings, "SANDBOX_COMPUTE_TOOL_REQUEST_TIMEOUT_SECONDS", self._proxy_timeout))
        self._discovery_timeout = int(
            getattr(settings, "SANDBOX_COMPUTE_DISCOVERY_TIMEOUT_SECONDS", self._proxy_timeout)
        )

        if not self._pod_image:
            raise SandboxComputeUnavailable("SANDBOX_COMPUTE_POD_IMAGE is required for kubernetes backend.")
        if not self._compute_api_token:
            raise SandboxComputeUnavailable("SANDBOX_COMPUTE_API_TOKEN is required for kubernetes backend.")

    def deploy_or_resume(self, agent, session: AgentComputeSession) -> SandboxSessionUpdate:
        pod_name = _pod_name(agent.id)
        sandbox_service_name = _sandbox_service_name(agent.id)
        pvc_name = _pvc_name(agent.id)
        egress_service_name = None
        no_proxy = None
        if session.proxy_server:
            if not self._egress_proxy_image:
                raise SandboxComputeUnavailable(
                    "SANDBOX_EGRESS_PROXY_POD_IMAGE is required to use proxy-backed sandbox pods."
                )
            egress_service_name = self._ensure_egress_proxy(agent, session.proxy_server)
            no_proxy = _merge_no_proxy_values(
                self._no_proxy,
                "localhost",
                "127.0.0.1",
                ".svc",
                ".cluster.local",
            )

        snapshot_name = session.workspace_snapshot.k8s_snapshot_name if session.workspace_snapshot else None
        if snapshot_name and not _resource_exists(self._client, _snapshot_path(self._namespace, snapshot_name)):
            logger.warning("Snapshot %s not found; provisioning fresh PVC for agent=%s", snapshot_name, agent.id)
            snapshot_name = None
        try:
            if not _resource_exists(self._client, _pvc_path(self._namespace, pvc_name)):
                self._create_pvc(pvc_name, snapshot_name=snapshot_name)
            if not _resource_exists(self._client, _service_path(self._namespace, sandbox_service_name)):
                self._create_service(sandbox_service_name, agent_id=str(agent.id))

            pod = self._get_pod(pod_name)
            if not pod:
                self._create_pod(
                    pod_name,
                    pvc_name,
                    agent_id=str(agent.id),
                    egress_service_name=egress_service_name,
                    no_proxy=no_proxy,
                )
            else:
                phase = (pod.get("status") or {}).get("phase")
                if not _sandbox_pod_matches(
                    pod,
                    image=self._pod_image,
                    egress_service_name=egress_service_name,
                    http_proxy_port=self._egress_proxy_service_port,
                    socks_proxy_port=self._egress_proxy_socks_service_port,
                    no_proxy=no_proxy,
                ):
                    self._delete_pod(pod_name)
                    self._create_pod(
                        pod_name,
                        pvc_name,
                        agent_id=str(agent.id),
                        egress_service_name=egress_service_name,
                        no_proxy=no_proxy,
                    )
                elif phase not in {"Running", "Pending"}:
                    self._delete_pod(pod_name)
                    self._create_pod(
                        pod_name,
                        pvc_name,
                        agent_id=str(agent.id),
                        egress_service_name=egress_service_name,
                        no_proxy=no_proxy,
                    )
        except KubernetesApiError as exc:
            raise SandboxComputeUnavailable(f"Kubernetes scheduler failed: {exc}") from exc

        if not self._wait_for_pod_ready(pod_name):
            return SandboxSessionUpdate(state=AgentComputeSession.State.ERROR, pod_name=pod_name, namespace=self._namespace)
        if not self._wait_for_service_routable(
            sandbox_service_name,
            timeout_seconds=self._service_routable_timeout,
        ):
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
        trusted_env_keys: Optional[list[str]] = None,
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
        if trusted_env_keys:
            payload["trusted_env_keys"] = [str(key) for key in trusted_env_keys if str(key)]
        return self._proxy_post(_sandbox_service_name(agent.id), "/sandbox/compute/run_command", payload, timeout=request_timeout)

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
        if not session.pod_name:
            return {"status": "error", "message": "Sandbox pod not available."}
        payload = {
            "agent_id": str(agent.id),
            "server_id": server_config_id,
            "tool_name": tool_name,
            "params": params,
        }
        if server_payload:
            payload["server"] = server_payload
        timeout_value = getattr(
            self,
            "_mcp_timeout",
            getattr(self, "_proxy_timeout", int(getattr(settings, "SANDBOX_COMPUTE_HTTP_TIMEOUT_SECONDS", 180))),
        )
        return self._proxy_post(
            _sandbox_service_name(agent.id),
            "/sandbox/compute/mcp_request",
            payload,
            timeout=timeout_value,
        )

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
        request_timeout = getattr(
            self,
            "_tool_timeout",
            getattr(self, "_proxy_timeout", int(getattr(settings, "SANDBOX_COMPUTE_HTTP_TIMEOUT_SECONDS", 180))),
        )
        if tool_name == "python_exec":
            normalized = _normalize_timeout(
                params_payload.get("timeout_seconds"),
                default=int(getattr(settings, "SANDBOX_COMPUTE_PYTHON_DEFAULT_TIMEOUT_SECONDS", 30)),
                maximum=int(getattr(settings, "SANDBOX_COMPUTE_PYTHON_MAX_TIMEOUT_SECONDS", 120)),
            )
            params_payload = dict(params_payload)
            params_payload["timeout_seconds"] = normalized
            request_timeout = max(request_timeout, normalized + 10)
        payload = {
            "agent_id": str(agent.id),
            "tool_name": tool_name,
            "params": params_payload,
        }
        return self._proxy_post(
            _sandbox_service_name(agent.id),
            "/sandbox/compute/tool_request",
            payload,
            timeout=request_timeout,
        )

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
        return self._proxy_post(_sandbox_service_name(agent.id), "/sandbox/compute/sync_filespace", body)

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
        self._delete_service(_sandbox_service_name(agent.id))
        self._delete_egress_proxy(agent)
        if delete_workspace:
            pvc_name = _pvc_name(agent.id)
            self._delete_pvc(pvc_name)
        return SandboxSessionUpdate(state=AgentComputeSession.State.STOPPED, pod_name=pod_name, namespace=self._namespace)

    def discover_mcp_tools(
        self,
        server_config_id: str,
        *,
        reason: str,
        agent=None,
        session: Optional[AgentComputeSession] = None,
        server_payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not server_payload:
            return {"status": "error", "message": "Missing MCP server payload for discovery."}
        if not _requires_agent_pod_discovery(server_payload):
            from api.agent.tools.mcp_manager import get_mcp_manager

            manager = get_mcp_manager()
            ok = manager.discover_tools_for_server(server_config_id, agent=agent)
            return {
                "status": "ok" if ok else "error",
                "reason": reason,
                "message": "Agent pod discovery skipped for non-sandboxed MCP server.",
            }

        if agent is None or session is None:
            return {"status": "error", "message": "Sandboxed stdio discovery requires an agent session."}

        service_name = _sandbox_service_name(agent.id)
        payload = {
            "agent_id": str(agent.id),
            "server_id": server_config_id,
            "reason": reason,
            "server": server_payload,
        }

        return self._proxy_post(
            service_name,
            "/sandbox/compute/discover_mcp_tools",
            payload,
            timeout=getattr(
                self,
                "_discovery_timeout",
                getattr(
                    self,
                    "_proxy_timeout",
                    int(getattr(settings, "SANDBOX_COMPUTE_HTTP_TIMEOUT_SECONDS", 180)),
                ),
            ),
        )

    def _proxy_post(
        self,
        service_name: str,
        path: str,
        payload: Dict[str, Any],
        *,
        timeout: Optional[int] = None,
    ) -> Dict[str, Any]:
        try:
            with requests.Session() as session:
                session.trust_env = False
                response = session.post(
                    _sandbox_service_url(self._namespace, service_name, path),
                    json=payload,
                    timeout=timeout or self._proxy_timeout,
                    headers={"X-Sandbox-Compute-Token": self._compute_api_token},
                )
                response.raise_for_status()
        except requests.RequestException as exc:
            return {"status": "error", "message": f"Sandbox proxy request failed: {exc}"}
        if not response.text:
            return {"status": "error", "message": "Sandbox proxy returned empty response."}
        try:
            return response.json()
        except ValueError:
            return {"status": "error", "message": "Sandbox proxy returned invalid JSON."}

    def _get_pod(self, pod_name: str) -> Optional[Dict[str, Any]]:
        try:
            return self._client.request_json("GET", _pod_path(self._namespace, pod_name), allow_404=True)
        except KubernetesApiError as exc:
            logger.warning("Failed to fetch pod %s: %s", pod_name, exc)
            return None

    def _get_service(self, service_name: str) -> Optional[Dict[str, Any]]:
        try:
            return self._client.request_json(
                "GET",
                _service_path(self._namespace, service_name),
                allow_404=True,
            )
        except KubernetesApiError as exc:
            logger.warning("Failed to fetch service %s: %s", service_name, exc)
            return None

    def _ensure_egress_proxy(self, agent, proxy_server) -> str:
        host = str(getattr(proxy_server, "host", "") or "").strip()
        port = getattr(proxy_server, "port", None)
        if not host or not port:
            raise SandboxComputeUnavailable("Proxy server is missing host/port metadata.")
        _upstream_proxy_scheme(proxy_server)

        pod_name = _egress_proxy_pod_name(agent.id)
        service_name = _egress_proxy_service_name(agent.id)

        try:
            service = self._get_service(service_name)
            if not service:
                self._create_egress_proxy_service(service_name, agent_id=str(agent.id))

            pod = self._get_pod(pod_name)
            if not pod:
                self._create_egress_proxy_pod(pod_name, agent_id=str(agent.id), proxy_server=proxy_server)
            else:
                phase = (pod.get("status") or {}).get("phase")
                if not _egress_proxy_pod_matches(
                    pod,
                    proxy_server=proxy_server,
                    http_listen_port=self._egress_proxy_port,
                    socks_listen_port=self._egress_proxy_socks_port,
                ):
                    self._delete_pod(pod_name)
                    self._create_egress_proxy_pod(pod_name, agent_id=str(agent.id), proxy_server=proxy_server)
                elif phase not in {"Running", "Pending"}:
                    self._delete_pod(pod_name)
                    self._create_egress_proxy_pod(pod_name, agent_id=str(agent.id), proxy_server=proxy_server)
        except KubernetesApiError as exc:
            raise SandboxComputeUnavailable(f"Egress proxy provisioning failed: {exc}") from exc

        if not self._wait_for_pod_ready(pod_name):
            raise SandboxComputeUnavailable("Egress proxy pod did not become ready in time.")

        return service_name

    def _create_pod(
        self,
        pod_name: str,
        pvc_name: str,
        *,
        agent_id: str,
        egress_service_name: Optional[str],
        no_proxy: Optional[str],
    ) -> None:
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
            egress_service_name=egress_service_name,
            http_proxy_port=self._egress_proxy_service_port,
            socks_proxy_port=self._egress_proxy_socks_service_port,
            no_proxy=no_proxy,
        )
        try:
            self._client.request_json("POST", _pod_collection_path(self._namespace), json_body=body)
        except KubernetesApiError as exc:
            if exc.status_code != 409:
                raise

    def _create_egress_proxy_pod(self, pod_name: str, *, agent_id: str, proxy_server) -> None:
        body = _build_egress_proxy_pod_manifest(
            pod_name=pod_name,
            namespace=self._namespace,
            image=self._egress_proxy_image,
            runtime_class=self._egress_proxy_runtime_class or None,
            service_account=self._egress_proxy_service_account or None,
            agent_id=agent_id,
            proxy_server=proxy_server,
            http_listen_port=self._egress_proxy_port,
            socks_listen_port=self._egress_proxy_socks_port,
        )
        try:
            self._client.request_json("POST", _pod_collection_path(self._namespace), json_body=body)
        except KubernetesApiError as exc:
            if exc.status_code != 409:
                raise

    def _create_egress_proxy_service(self, service_name: str, *, agent_id: str) -> None:
        body = _build_egress_proxy_service_manifest(
            service_name=service_name,
            namespace=self._namespace,
            agent_id=agent_id,
            http_port=self._egress_proxy_service_port,
            http_target_port=self._egress_proxy_port,
            socks_port=self._egress_proxy_socks_service_port,
            socks_target_port=self._egress_proxy_socks_port,
        )
        try:
            self._client.request_json("POST", _service_collection_path(self._namespace), json_body=body)
        except KubernetesApiError as exc:
            if exc.status_code != 409:
                raise

    def _create_service(self, service_name: str, *, agent_id: str) -> None:
        body = _build_sandbox_service_manifest(
            service_name=service_name,
            namespace=self._namespace,
            agent_id=agent_id,
            port=_SANDBOX_SERVICE_PORT,
            target_port=_SANDBOX_SERVICE_PORT,
        )
        try:
            self._client.request_json("POST", _service_collection_path(self._namespace), json_body=body)
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

    def _delete_service(self, service_name: str) -> None:
        try:
            self._client.request_json("DELETE", _service_path(self._namespace, service_name), allow_404=True)
        except KubernetesApiError as exc:
            logger.warning("Failed to delete service %s: %s", service_name, exc)

    def _delete_egress_proxy(self, agent) -> None:
        pod_name = _egress_proxy_pod_name(agent.id)
        service_name = _egress_proxy_service_name(agent.id)
        self._delete_pod(pod_name)
        try:
            self._client.request_json("DELETE", _service_path(self._namespace, service_name), allow_404=True)
        except KubernetesApiError as exc:
            logger.warning("Failed to delete egress proxy service %s: %s", service_name, exc)

    def _wait_for_pod_ready(self, pod_name: str) -> bool:
        started_at = time.monotonic()
        deadline = time.time() + self._pod_ready_timeout
        attempts = 0
        last_phase = None
        while time.time() < deadline:
            attempts += 1
            pod = self._get_pod(pod_name)
            if not pod:
                time.sleep(2)
                continue
            status = pod.get("status") or {}
            phase = status.get("phase")
            if phase != last_phase:
                logger.info(
                    "Sandbox pod readiness progress pod=%s phase=%s attempts=%s elapsed_ms=%s",
                    pod_name,
                    phase,
                    attempts,
                    _elapsed_ms(started_at),
                )
                last_phase = phase
            if phase == "Running":
                for condition in status.get("conditions", []):
                    if condition.get("type") == "Ready" and condition.get("status") == "True":
                        logger.info(
                            "Sandbox pod ready pod=%s attempts=%s elapsed_ms=%s",
                            pod_name,
                            attempts,
                            _elapsed_ms(started_at),
                        )
                        return True
            time.sleep(2)
        logger.warning(
            "Sandbox pod readiness timeout pod=%s attempts=%s elapsed_ms=%s timeout_seconds=%s last_phase=%s",
            pod_name,
            attempts,
            _elapsed_ms(started_at),
            self._pod_ready_timeout,
            last_phase,
        )
        return False

    def _wait_for_service_routable(self, service_name: str, *, timeout_seconds: float) -> bool:
        started_at = time.monotonic()
        deadline = started_at + max(float(timeout_seconds), 0.0)
        url = _sandbox_service_url(self._namespace, service_name, "/healthz")
        attempts = 0
        last_error = None

        with requests.Session() as session:
            session.trust_env = False
            while time.monotonic() < deadline:
                attempts += 1
                remaining = max(deadline - time.monotonic(), 0.1)
                try:
                    response = session.get(url, timeout=min(5.0, remaining))
                    response.raise_for_status()
                    logger.info(
                        "Sandbox service routable service=%s attempts=%s elapsed_ms=%s",
                        service_name,
                        attempts,
                        _elapsed_ms(started_at),
                    )
                    return True
                except requests.RequestException as exc:
                    last_error = exc
                    if attempts == 1 or attempts % 5 == 0:
                        logger.info(
                            "Sandbox service not routable yet service=%s attempts=%s elapsed_ms=%s error=%s",
                            service_name,
                            attempts,
                            _elapsed_ms(started_at),
                            exc,
                        )
                    time.sleep(min(1.0, remaining))

        logger.warning(
            "Sandbox service routability timeout service=%s attempts=%s elapsed_ms=%s timeout_seconds=%s last_error=%s",
            service_name,
            attempts,
            _elapsed_ms(started_at),
            timeout_seconds,
            last_error,
        )
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


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9-]", "-", value.lower())
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")
    return cleaned


def _pod_name(agent_id: Any) -> str:
    return _slugify(f"sandbox-agent-{agent_id}")


def _sandbox_service_name(agent_id: Any) -> str:
    return _pod_name(agent_id)


def _pvc_name(agent_id: Any) -> str:
    return _slugify(f"sandbox-workspace-{agent_id}")


def _egress_proxy_pod_name(agent_id: Any) -> str:
    return _slugify(f"sandbox-egress-{agent_id}")


def _egress_proxy_service_name(agent_id: Any) -> str:
    return _slugify(f"sandbox-egress-{agent_id}")


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


def _service_collection_path(namespace: str) -> str:
    return f"/api/v1/namespaces/{namespace}/services"


def _service_path(namespace: str, service_name: str) -> str:
    return f"/api/v1/namespaces/{namespace}/services/{service_name}"


def _sandbox_service_url(namespace: str, service_name: str, path: str) -> str:
    suffix = path if path.startswith("/") else f"/{path}"
    return f"http://{service_name}.{namespace}.svc.cluster.local:{_SANDBOX_SERVICE_PORT}{suffix}"


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
    egress_service_name: Optional[str],
    http_proxy_port: int,
    socks_proxy_port: int,
    no_proxy: Optional[str],
) -> Dict[str, Any]:
    env = [
        {"name": "SANDBOX_RUNTIME_CACHE_ROOT", "value": "/runtime-cache"},
        {"name": "SANDBOX_AGENT_WORKSPACE_LAYOUT", "value": "isolated"},
    ]
    env.extend(
        _build_proxy_env(
            egress_service_name=egress_service_name,
            http_proxy_port=http_proxy_port,
            socks_proxy_port=socks_proxy_port,
            no_proxy=no_proxy,
        )
    )

    container: Dict[str, Any] = {
        "name": "sandbox-supervisor",
        "image": image,
        "imagePullPolicy": "IfNotPresent",
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
            {"name": "runtime-cache", "mountPath": "/runtime-cache"},
        ],
        "readinessProbe": {
            "httpGet": {"path": "/healthz", "port": 8080},
            "initialDelaySeconds": 10,
            "periodSeconds": 10,
            "failureThreshold": 3,
        },
    }
    container["env"] = env

    manifest = {
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
            "automountServiceAccountToken": False,
            "runtimeClassName": runtime_class,
            "terminationGracePeriodSeconds": 300,
            "securityContext": {
                "fsGroup": 1000,
                "fsGroupChangePolicy": "OnRootMismatch",
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "containers": [container],
            "volumes": [
                {
                    "name": "workspace",
                    "persistentVolumeClaim": {"claimName": pvc_name},
                },
                {"name": "runtime-cache", "emptyDir": {}},
            ],
        },
    }
    if service_account:
        manifest["spec"]["serviceAccountName"] = service_account
    return manifest


def _build_proxy_env(
    *,
    egress_service_name: Optional[str],
    http_proxy_port: int,
    socks_proxy_port: int,
    no_proxy: Optional[str],
) -> list[Dict[str, str]]:
    return [
        {"name": key, "value": value}
        for key, value in _sandbox_sidecar_proxy_env_values(
            egress_service_name=egress_service_name,
            http_proxy_port=http_proxy_port,
            socks_proxy_port=socks_proxy_port,
            no_proxy=no_proxy,
        ).items()
    ]


def _sandbox_sidecar_proxy_env_values(
    *,
    egress_service_name: Optional[str],
    http_proxy_port: int,
    socks_proxy_port: int,
    no_proxy: Optional[str],
) -> Dict[str, str]:
    http_proxy_url = None
    socks_proxy_url = None
    if egress_service_name:
        http_proxy_url = f"http://{egress_service_name}:{http_proxy_port}"
        socks_proxy_url = f"socks5://{egress_service_name}:{socks_proxy_port}"
    env: Dict[str, str] = {}
    if http_proxy_url:
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "FTP_PROXY", "http_proxy", "https_proxy", "ftp_proxy"):
            env[key] = http_proxy_url
    if socks_proxy_url:
        for key in ("ALL_PROXY", "all_proxy"):
            env[key] = socks_proxy_url
    no_proxy_value = str(no_proxy or "").strip()
    if no_proxy_value:
        env["NO_PROXY"] = no_proxy_value
        env["no_proxy"] = no_proxy_value
    return env


def _build_egress_proxy_pod_manifest(
    *,
    pod_name: str,
    namespace: str,
    image: str,
    runtime_class: Optional[str],
    service_account: Optional[str],
    agent_id: str,
    proxy_server: Any,
    http_listen_port: int,
    socks_listen_port: int,
) -> Dict[str, Any]:
    host = str(getattr(proxy_server, "host", "") or "").strip()
    port = str(getattr(proxy_server, "port", "") or "").strip()
    username = str(getattr(proxy_server, "username", "") or "").strip()
    password = str(getattr(proxy_server, "password", "") or "").strip()
    proxy_id = str(getattr(proxy_server, "id", "") or "").strip()
    upstream_protocol = _upstream_proxy_protocol(proxy_server)
    upstream_proxy_scheme = _upstream_proxy_scheme(proxy_server)

    env = [
        {"name": "UPSTREAM_PROTOCOL", "value": upstream_protocol},
        {"name": "UPSTREAM_PROXY_SCHEME", "value": upstream_proxy_scheme},
        {"name": "UPSTREAM_HOST", "value": host},
        {"name": "UPSTREAM_PORT", "value": port},
        {"name": "HTTP_LISTEN_PORT", "value": str(http_listen_port)},
        {"name": "SOCKS_LISTEN_PORT", "value": str(socks_listen_port)},
    ]
    if username:
        env.append({"name": "UPSTREAM_USERNAME", "value": username})
    if password:
        env.append({"name": "UPSTREAM_PASSWORD", "value": password})

    labels = {
        "app": "sandbox-egress-proxy",
        "component": "sandbox-egress",
        "agent_id": agent_id,
    }
    if proxy_id:
        labels["proxy_id"] = proxy_id

    spec: Dict[str, Any] = {
        "automountServiceAccountToken": False,
        "terminationGracePeriodSeconds": 30,
        "securityContext": {
            "seccompProfile": {"type": "RuntimeDefault"},
        },
        "containers": [
            {
                "name": "egress-proxy",
                "image": image,
                "imagePullPolicy": "IfNotPresent",
                "ports": [
                    {"name": "http", "containerPort": http_listen_port},
                    {"name": "socks5", "containerPort": socks_listen_port},
                ],
                "env": env,
                "securityContext": {
                    "allowPrivilegeEscalation": False,
                    "runAsNonRoot": True,
                    "runAsUser": 1000,
                    "runAsGroup": 1000,
                    "capabilities": {"drop": ["ALL"]},
                },
                "readinessProbe": {
                    "tcpSocket": {"port": http_listen_port},
                    "initialDelaySeconds": 3,
                    "periodSeconds": 5,
                    "failureThreshold": 3,
                },
                "livenessProbe": {
                    "tcpSocket": {"port": http_listen_port},
                    "initialDelaySeconds": 5,
                    "periodSeconds": 10,
                    "failureThreshold": 3,
                },
            }
        ],
    }
    if runtime_class:
        spec["runtimeClassName"] = runtime_class
    if service_account:
        spec["serviceAccountName"] = service_account

    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": pod_name,
            "namespace": namespace,
            "labels": labels,
        },
        "spec": spec,
    }


def _upstream_proxy_scheme(proxy_server: Any) -> str:
    proxy_type = str(getattr(proxy_server, "proxy_type", "") or "").strip().upper()
    if proxy_type in {"HTTP", "HTTPS", "SOCKS5"}:
        return proxy_type.lower()
    raise SandboxComputeUnavailable(f"Unsupported proxy type for sandbox egress: {proxy_type or 'unknown'}")


def _upstream_proxy_protocol(proxy_server: Any) -> str:
    proxy_type = str(getattr(proxy_server, "proxy_type", "") or "").strip().upper()
    if proxy_type == "SOCKS5":
        return "socks5"
    if proxy_type in {"HTTP", "HTTPS"}:
        return "http"
    raise SandboxComputeUnavailable(f"Unsupported proxy type for sandbox egress: {proxy_type or 'unknown'}")


def _egress_proxy_pod_matches(
    pod: Dict[str, Any],
    *,
    proxy_server: Any,
    http_listen_port: int,
    socks_listen_port: int,
) -> bool:
    metadata = (pod.get("metadata") or {}) if isinstance(pod, dict) else {}
    labels = (metadata.get("labels") or {}) if isinstance(metadata, dict) else {}
    desired_proxy_id = str(getattr(proxy_server, "id", "") or "")
    if desired_proxy_id and labels.get("proxy_id") != desired_proxy_id:
        return False

    spec = (pod.get("spec") or {}) if isinstance(pod, dict) else {}
    containers = spec.get("containers") or []
    if not containers:
        return False
    container = containers[0] or {}
    env_entries = container.get("env") or []
    env = {
        str(entry.get("name")): str(entry.get("value", ""))
        for entry in env_entries
        if isinstance(entry, dict) and entry.get("name")
    }

    expected = {
        "UPSTREAM_PROTOCOL": _upstream_proxy_protocol(proxy_server),
        "UPSTREAM_PROXY_SCHEME": _upstream_proxy_scheme(proxy_server),
        "UPSTREAM_HOST": str(getattr(proxy_server, "host", "") or "").strip(),
        "UPSTREAM_PORT": str(getattr(proxy_server, "port", "") or "").strip(),
        "HTTP_LISTEN_PORT": str(http_listen_port),
        "SOCKS_LISTEN_PORT": str(socks_listen_port),
        "UPSTREAM_USERNAME": str(getattr(proxy_server, "username", "") or "").strip(),
        "UPSTREAM_PASSWORD": str(getattr(proxy_server, "password", "") or "").strip(),
    }
    return all(env.get(key, "") == value for key, value in expected.items())


def _sandbox_pod_matches(
    pod: Dict[str, Any],
    *,
    image: str,
    egress_service_name: Optional[str],
    http_proxy_port: int,
    socks_proxy_port: int,
    no_proxy: Optional[str],
) -> bool:
    spec = (pod.get("spec") or {}) if isinstance(pod, dict) else {}
    containers = spec.get("containers") or []
    if not containers:
        return False
    container = next(
        (
            entry
            for entry in containers
            if isinstance(entry, dict) and str(entry.get("name", "")).strip() == "sandbox-supervisor"
        ),
        containers[0],
    )
    if not isinstance(container, dict):
        return False
    if str(container.get("image", "") or "").strip() != image:
        return False

    env_entries = container.get("env") or []
    env = {
        str(entry.get("name")): str(entry.get("value", ""))
        for entry in env_entries
        if isinstance(entry, dict) and entry.get("name")
    }
    if env.get("SANDBOX_RUNTIME_CACHE_ROOT", "") != "/runtime-cache":
        return False
    if env.get("SANDBOX_AGENT_WORKSPACE_LAYOUT", "") != "isolated":
        return False

    expected_proxy_env = {
        str(entry.get("name")): str(entry.get("value", ""))
        for entry in _build_proxy_env(
            egress_service_name=egress_service_name,
            http_proxy_port=http_proxy_port,
            socks_proxy_port=socks_proxy_port,
            no_proxy=no_proxy,
        )
        if isinstance(entry, dict) and entry.get("name")
    }
    proxy_keys = {
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
    }
    return all(env.get(key, "") == expected_proxy_env.get(key, "") for key in proxy_keys)


def _build_egress_proxy_service_manifest(
    *,
    service_name: str,
    namespace: str,
    agent_id: str,
    http_port: int,
    http_target_port: int,
    socks_port: int,
    socks_target_port: int,
) -> Dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {
            "name": service_name,
            "namespace": namespace,
            "labels": {
                "app": "sandbox-egress-proxy",
                "component": "sandbox-egress",
                "agent_id": agent_id,
            },
        },
        "spec": {
            "type": "ClusterIP",
            "selector": {
                "app": "sandbox-egress-proxy",
                "agent_id": agent_id,
            },
            "ports": [
                {
                    "name": "http",
                    "port": http_port,
                    "targetPort": http_target_port,
                    "protocol": "TCP",
                },
                {
                    "name": "socks5",
                    "port": socks_port,
                    "targetPort": socks_target_port,
                    "protocol": "TCP",
                }
            ],
        },
    }


def _build_sandbox_service_manifest(
    *,
    service_name: str,
    namespace: str,
    agent_id: str,
    port: int,
    target_port: int,
) -> Dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {
            "name": service_name,
            "namespace": namespace,
            "labels": {
                "app": "sandbox-compute",
                "component": "sandbox-agent",
                "agent_id": agent_id,
            },
        },
        "spec": {
            "type": "ClusterIP",
            "selector": {
                "app": "sandbox-compute",
                "agent_id": agent_id,
            },
            "ports": [
                {
                    "name": "http",
                    "port": port,
                    "targetPort": target_port,
                    "protocol": "TCP",
                }
            ],
        },
    }


def _merge_no_proxy_values(*values: str) -> str:
    parts: list[str] = []
    for value in values:
        if not value:
            continue
        for item in value.split(","):
            cleaned = item.strip()
            if not cleaned or cleaned in parts:
                continue
            parts.append(cleaned)
    return ",".join(parts)
