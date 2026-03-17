from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, tag

from api.services.sandbox_kubernetes import KubernetesSandboxBackend, _pod_name


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

    def test_stdio_discovery_requires_agent_session(self):
        backend = self._backend()

        result = backend.discover_mcp_tools(
            "cfg-1",
            reason="unit-test",
            server_payload={"config_id": "cfg-1", "scope": "user", "command": "npx"},
        )

        self.assertEqual(result.get("status"), "error")
        self.assertIn("requires an agent session", result.get("message", ""))

    def test_stdio_discovery_routes_via_agent_pod(self):
        backend = self._backend()
        agent = SimpleNamespace(id="agent-1")
        session = SimpleNamespace(pod_name="sandbox-agent-agent-1", proxy_server=SimpleNamespace(proxy_url="http://proxy.example:3128"))

        with patch.object(
            backend,
            "_proxy_post",
            return_value={"status": "ok", "tools": []},
        ) as mock_proxy_post:
            result = backend.discover_mcp_tools(
                "cfg-2",
                reason="unit-test",
                agent=agent,
                session=session,
                server_payload={"config_id": "cfg-2", "scope": "user", "command": "npx"},
            )

        self.assertEqual(result.get("status"), "ok")
        mock_proxy_post.assert_called_once()
        proxy_args = mock_proxy_post.call_args.args
        self.assertEqual(proxy_args[0], session.pod_name)
        self.assertEqual(proxy_args[1], "/sandbox/compute/discover_mcp_tools")
        payload = mock_proxy_post.call_args.args[2]
        self.assertEqual(payload["agent_id"], str(agent.id))
        self.assertEqual(payload["proxy_env"]["HTTP_PROXY"], session.proxy_server.proxy_url)

    def test_discovery_uses_local_discovery_for_user_scope_http_server(self):
        backend = self._backend()

        with patch("api.agent.tools.mcp_manager.get_mcp_manager") as mock_get_manager:
            mock_get_manager.return_value.discover_tools_for_server.return_value = True
            result = backend.discover_mcp_tools(
                "cfg-4",
                reason="unit-test",
                server_payload={
                    "config_id": "cfg-4",
                    "scope": "user",
                    "url": "https://example.com/mcp",
                    "command": "",
                },
            )

        self.assertEqual(result.get("status"), "ok")
        mock_get_manager.return_value.discover_tools_for_server.assert_called_once_with("cfg-4", agent=None)
 
    def test_http_discovery_passes_agent_when_available(self):
        backend = self._backend()
        agent = SimpleNamespace(id="agent-4")

        with patch("api.agent.tools.mcp_manager.get_mcp_manager") as mock_get_manager:
            mock_get_manager.return_value.discover_tools_for_server.return_value = True
            result = backend.discover_mcp_tools(
                "cfg-4b",
                reason="unit-test",
                agent=agent,
                server_payload={
                    "config_id": "cfg-4b",
                    "scope": "user",
                    "url": "https://example.com/mcp",
                    "command": "",
                },
            )

        self.assertEqual(result.get("status"), "ok")
        mock_get_manager.return_value.discover_tools_for_server.assert_called_once_with("cfg-4b", agent=agent)

    def test_stdio_discovery_falls_back_to_default_agent_pod_name(self):
        backend = self._backend()
        agent = SimpleNamespace(id="agent-5")
        session = SimpleNamespace(pod_name="", proxy_server=None)

        with patch.object(
            backend,
            "_proxy_post",
            return_value={"status": "ok", "tools": []},
        ) as mock_proxy_post:
            result = backend.discover_mcp_tools(
                "cfg-5",
                reason="unit-test",
                agent=agent,
                session=session,
                server_payload={
                    "config_id": "cfg-5",
                    "scope": "organization",
                    "command": "npx",
                    "url": "",
                },
            )

        self.assertEqual(result.get("status"), "ok")
        mock_proxy_post.assert_called_once()
        self.assertEqual(mock_proxy_post.call_args.args[0], _pod_name(agent.id))
