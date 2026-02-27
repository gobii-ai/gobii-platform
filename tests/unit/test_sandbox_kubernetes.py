from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, tag

from api.services.sandbox_kubernetes import KubernetesSandboxBackend, _discovery_pod_name


@tag("batch_agent_lifecycle")
class KubernetesSandboxMCPDiscoveryTests(SimpleTestCase):
    def _backend(self) -> KubernetesSandboxBackend:
        backend = object.__new__(KubernetesSandboxBackend)
        backend._no_proxy = ""
        backend._namespace = "default"
        backend._pod_image = "ghcr.io/example/sandbox:latest"
        backend._pod_runtime_class = "gvisor"
        backend._pod_service_account = "sandbox-sa"
        backend._pod_configmap = "sandbox-config"
        backend._pod_secret = "sandbox-secret"
        return backend

    def test_discovery_fails_when_proxy_required_and_none_available(self):
        backend = self._backend()

        with patch("api.services.sandbox_kubernetes.get_sandbox_compute_require_proxy", return_value=True), patch(
            "api.services.sandbox_kubernetes.select_proxy",
            return_value=None,
        ), patch.object(backend, "_create_discovery_pod") as mock_create_pod:
            result = backend.discover_mcp_tools(
                "cfg-1",
                reason="unit-test",
                server_payload={"config_id": "cfg-1", "command": "npx"},
            )

        self.assertEqual(result.get("status"), "error")
        self.assertIn("No proxy server available", result.get("message", ""))
        mock_create_pod.assert_not_called()

    def test_discovery_routes_via_selected_proxy_and_cleans_up_pod(self):
        backend = self._backend()
        selected_proxy = SimpleNamespace(proxy_url="http://proxy.example:3128")

        with patch("api.services.sandbox_kubernetes.get_sandbox_compute_require_proxy", return_value=True), patch(
            "api.services.sandbox_kubernetes.select_proxy",
            return_value=selected_proxy,
        ), patch.object(backend, "_create_discovery_pod") as mock_create_pod, patch.object(
            backend,
            "_wait_for_pod_ready",
            return_value=True,
        ), patch.object(
            backend,
            "_proxy_post",
            return_value={"status": "ok", "tools": []},
        ) as mock_proxy_post, patch.object(backend, "_delete_pod") as mock_delete_pod:
            result = backend.discover_mcp_tools(
                "cfg-2",
                reason="unit-test",
                server_payload={"config_id": "cfg-2", "command": "npx"},
            )

        self.assertEqual(result.get("status"), "ok")
        mock_create_pod.assert_called_once()
        create_args = mock_create_pod.call_args
        self.assertEqual(create_args.args[0], _discovery_pod_name("cfg-2"))
        self.assertEqual(create_args.kwargs.get("proxy_url"), selected_proxy.proxy_url)
        self.assertIn(".svc", create_args.kwargs.get("no_proxy", ""))

        mock_proxy_post.assert_called_once()
        proxy_args = mock_proxy_post.call_args.args
        self.assertEqual(proxy_args[0], _discovery_pod_name("cfg-2"))
        self.assertEqual(proxy_args[1], "/sandbox/compute/discover_mcp_tools")

        mock_delete_pod.assert_called_once_with(_discovery_pod_name("cfg-2"))

    def test_discovery_continues_without_proxy_when_not_required(self):
        backend = self._backend()

        with patch("api.services.sandbox_kubernetes.get_sandbox_compute_require_proxy", return_value=False), patch(
            "api.services.sandbox_kubernetes.select_proxy",
            side_effect=RuntimeError("no active proxy"),
        ), patch.object(backend, "_create_discovery_pod") as mock_create_pod, patch.object(
            backend,
            "_wait_for_pod_ready",
            return_value=True,
        ), patch.object(
            backend,
            "_proxy_post",
            return_value={"status": "ok", "tools": []},
        ), patch.object(backend, "_delete_pod") as mock_delete_pod:
            result = backend.discover_mcp_tools(
                "cfg-3",
                reason="unit-test",
                server_payload={"config_id": "cfg-3", "command": "npx"},
            )

        self.assertEqual(result.get("status"), "ok")
        create_args = mock_create_pod.call_args
        self.assertEqual(create_args.args[0], _discovery_pod_name("cfg-3"))
        self.assertIsNone(create_args.kwargs.get("proxy_url"))
        self.assertIsNone(create_args.kwargs.get("no_proxy"))
        mock_delete_pod.assert_called_once_with(_discovery_pod_name("cfg-3"))
