from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, tag

from api.services.sandbox_kubernetes import (
    _build_sandbox_service_manifest,
    KubernetesSandboxBackend,
    _build_egress_proxy_pod_manifest,
    _build_egress_proxy_service_manifest,
    _build_proxy_env,
    _build_pod_manifest,
    _pod_name,
)


@tag("batch_agent_lifecycle")
class KubernetesSandboxMCPDiscoveryTests(SimpleTestCase):
    def _backend(self) -> KubernetesSandboxBackend:
        backend = object.__new__(KubernetesSandboxBackend)
        backend._client = Mock()
        backend._no_proxy = ""
        backend._namespace = "default"
        backend._compute_api_token = "supervisor-token"
        backend._pod_image = "ghcr.io/example/sandbox:latest"
        backend._pod_runtime_class = "gvisor"
        backend._pod_service_account = "sandbox-sa"
        backend._pod_configmap = "sandbox-config"
        backend._pod_secret = "sandbox-secret"
        backend._egress_proxy_port = 3128
        backend._egress_proxy_service_port = 3128
        backend._egress_proxy_socks_port = 1080
        backend._egress_proxy_socks_service_port = 1080
        backend._proxy_timeout = 30
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
        self.assertNotIn("proxy_env", payload)

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

    def test_proxy_post_forwards_supervisor_token_header(self):
        backend = self._backend()
        response = Mock()
        response.raise_for_status.return_value = None
        response.text = '{"status": "ok"}'
        response.json.return_value = {"status": "ok"}
        session = Mock()
        session.__enter__ = Mock(return_value=session)
        session.__exit__ = Mock(return_value=False)
        session.post.return_value = response

        with patch("api.services.sandbox_kubernetes.requests.Session", return_value=session) as mock_session:
            result = backend._proxy_post(
                "sandbox-agent-agent-1",
                "/sandbox/compute/run_command",
                {"agent_id": "agent-1", "command": "pwd"},
            )

        self.assertEqual(result.get("status"), "ok")
        self.assertFalse(session.trust_env)
        self.assertEqual(
            session.post.call_args.kwargs["headers"],
            {"X-Sandbox-Compute-Token": "supervisor-token"},
        )
        self.assertEqual(
            session.post.call_args.args[0],
            "http://sandbox-agent-agent-1.default.svc.cluster.local:8080/sandbox/compute/run_command",
        )

    def test_ensure_egress_proxy_allows_socks5_upstream(self):
        backend = self._backend()
        agent = SimpleNamespace(id="agent-socks")
        proxy_server = SimpleNamespace(
            id="proxy-1",
            host="proxy.example",
            port=1080,
            proxy_type="SOCKS5",
            username="",
            password="",
        )
        backend._get_service = Mock(return_value={"metadata": {"name": "sandbox-egress-agent-socks"}})
        backend._get_pod = Mock(return_value=None)
        backend._create_egress_proxy_pod = Mock()
        backend._wait_for_pod_ready = Mock(return_value=True)

        service_name = backend._ensure_egress_proxy(agent, proxy_server)

        self.assertEqual(service_name, "sandbox-egress-agent-socks")
        backend._create_egress_proxy_pod.assert_called_once()

    def test_ensure_egress_proxy_recreates_stale_protocol_config(self):
        backend = self._backend()
        agent = SimpleNamespace(id="agent-stale")
        proxy_server = SimpleNamespace(
            id="proxy-2",
            host="proxy.example",
            port=1080,
            proxy_type="SOCKS5",
            username="user",
            password="secret",
        )
        backend._get_service = Mock(return_value={"metadata": {"name": "sandbox-egress-agent-stale"}})
        backend._get_pod = Mock(
            return_value={
                "metadata": {"labels": {"proxy_id": "proxy-2"}},
                "status": {"phase": "Running"},
                "spec": {
                    "containers": [
                        {
                            "env": [
                                {"name": "UPSTREAM_PROTOCOL", "value": "http"},
                                {"name": "UPSTREAM_PROXY_SCHEME", "value": "https"},
                                {"name": "UPSTREAM_HOST", "value": "proxy.example"},
                                {"name": "UPSTREAM_PORT", "value": "1080"},
                                {"name": "HTTP_LISTEN_PORT", "value": "3128"},
                                {"name": "SOCKS_LISTEN_PORT", "value": "1080"},
                                {"name": "UPSTREAM_USERNAME", "value": "user"},
                                {"name": "UPSTREAM_PASSWORD", "value": "secret"},
                            ]
                        }
                    ]
                },
            }
        )
        backend._delete_pod = Mock()
        backend._create_egress_proxy_pod = Mock()
        backend._wait_for_pod_ready = Mock(return_value=True)

        service_name = backend._ensure_egress_proxy(agent, proxy_server)

        self.assertEqual(service_name, "sandbox-egress-agent-stale")
        backend._delete_pod.assert_called_once_with("sandbox-egress-agent-stale")
        backend._create_egress_proxy_pod.assert_called_once_with(
            "sandbox-egress-agent-stale",
            agent_id="agent-stale",
            proxy_server=proxy_server,
        )

    def test_deploy_or_resume_creates_agent_service_when_missing(self):
        backend = self._backend()
        agent = SimpleNamespace(id="agent-svc")
        session = SimpleNamespace(proxy_server=None, workspace_snapshot=None)
        backend._create_pvc = Mock()
        backend._create_service = Mock()
        backend._get_pod = Mock(return_value=None)
        backend._create_pod = Mock()
        backend._wait_for_pod_ready = Mock(return_value=True)

        with patch("api.services.sandbox_kubernetes._resource_exists", side_effect=[False, False]):
            result = backend.deploy_or_resume(agent, session)

        self.assertEqual(result.state, "running")
        backend._create_service.assert_called_once_with("sandbox-agent-agent-svc", agent_id="agent-svc")
        backend._create_pod.assert_called_once()

    def test_deploy_or_resume_keeps_sandbox_service_separate_from_egress_service(self):
        backend = self._backend()
        backend._egress_proxy_image = "ghcr.io/example/egress:latest"
        backend._egress_proxy_service_port = 3128
        agent = SimpleNamespace(id="agent-proxy")
        session = SimpleNamespace(
            proxy_server=SimpleNamespace(id="proxy-1"),
            workspace_snapshot=None,
        )
        backend._ensure_egress_proxy = Mock(return_value="sandbox-egress-agent-proxy")
        backend._create_pvc = Mock()
        backend._create_service = Mock()
        backend._get_pod = Mock(return_value=None)
        backend._create_pod = Mock()
        backend._wait_for_pod_ready = Mock(return_value=True)

        with patch("api.services.sandbox_kubernetes._resource_exists", side_effect=[False, False]):
            result = backend.deploy_or_resume(agent, session)

        self.assertEqual(result.state, "running")
        backend._ensure_egress_proxy.assert_called_once_with(agent, session.proxy_server)
        backend._create_service.assert_called_once_with("sandbox-agent-agent-proxy", agent_id="agent-proxy")
        backend._create_pod.assert_called_once_with(
            "sandbox-agent-agent-proxy",
            "sandbox-workspace-agent-proxy",
            agent_id="agent-proxy",
            egress_service_name="sandbox-egress-agent-proxy",
            no_proxy="localhost,127.0.0.1,.svc,.cluster.local",
        )

    def test_deploy_or_resume_recreates_stale_sandbox_pod_image(self):
        backend = self._backend()
        agent = SimpleNamespace(id="agent-stale-image")
        session = SimpleNamespace(proxy_server=None, workspace_snapshot=None)
        backend._create_pvc = Mock()
        backend._create_service = Mock()
        backend._get_pod = Mock(
            return_value={
                "status": {"phase": "Running"},
                "spec": {
                    "containers": [
                        {
                            "name": "sandbox-supervisor",
                            "image": "ghcr.io/example/sandbox:old",
                            "env": [
                                {"name": "SANDBOX_RUNTIME_CACHE_ROOT", "value": "/runtime-cache"},
                            ],
                        }
                    ]
                },
            }
        )
        backend._delete_pod = Mock()
        backend._create_pod = Mock()
        backend._wait_for_pod_ready = Mock(return_value=True)

        with patch("api.services.sandbox_kubernetes._resource_exists", side_effect=[True, True]):
            result = backend.deploy_or_resume(agent, session)

        self.assertEqual(result.state, "running")
        backend._delete_pod.assert_called_once_with("sandbox-agent-agent-stale-image")
        backend._create_pod.assert_called_once_with(
            "sandbox-agent-agent-stale-image",
            "sandbox-workspace-agent-stale-image",
            agent_id="agent-stale-image",
            egress_service_name=None,
            no_proxy=None,
        )

    def test_deploy_or_resume_recreates_sandbox_pod_when_proxy_env_drifted(self):
        backend = self._backend()
        backend._egress_proxy_image = "ghcr.io/example/egress:latest"
        backend._egress_proxy_service_port = 3128
        backend._egress_proxy_socks_service_port = 1080
        agent = SimpleNamespace(id="agent-proxy-drift")
        session = SimpleNamespace(
            proxy_server=SimpleNamespace(id="proxy-1"),
            workspace_snapshot=None,
        )
        backend._ensure_egress_proxy = Mock(return_value="sandbox-egress-agent-proxy-drift")
        backend._create_pvc = Mock()
        backend._create_service = Mock()
        backend._get_pod = Mock(
            return_value={
                "status": {"phase": "Running"},
                "spec": {
                    "containers": [
                        {
                            "name": "sandbox-supervisor",
                            "image": "ghcr.io/example/sandbox:latest",
                            "env": [
                                {"name": "SANDBOX_RUNTIME_CACHE_ROOT", "value": "/runtime-cache"},
                            ],
                        }
                    ]
                },
            }
        )
        backend._delete_pod = Mock()
        backend._create_pod = Mock()
        backend._wait_for_pod_ready = Mock(return_value=True)

        with patch("api.services.sandbox_kubernetes._resource_exists", side_effect=[True, True]):
            result = backend.deploy_or_resume(agent, session)

        self.assertEqual(result.state, "running")
        backend._delete_pod.assert_called_once_with("sandbox-agent-agent-proxy-drift")
        backend._create_pod.assert_called_once_with(
            "sandbox-agent-agent-proxy-drift",
            "sandbox-workspace-agent-proxy-drift",
            agent_id="agent-proxy-drift",
            egress_service_name="sandbox-egress-agent-proxy-drift",
            no_proxy="localhost,127.0.0.1,.svc,.cluster.local",
        )


@tag("batch_agent_lifecycle")
class KubernetesSandboxPodManifestTests(SimpleTestCase):
    def test_sandbox_service_manifest_targets_agent_pod(self):
        manifest = _build_sandbox_service_manifest(
            service_name="sandbox-agent-agent-1",
            namespace="default",
            agent_id="agent-1",
            port=8080,
            target_port=8080,
        )

        self.assertEqual(manifest["spec"]["selector"]["app"], "sandbox-compute")
        self.assertEqual(manifest["spec"]["selector"]["agent_id"], "agent-1")
        self.assertEqual(manifest["spec"]["ports"][0]["port"], 8080)
        self.assertEqual(manifest["spec"]["ports"][0]["targetPort"], 8080)

    def test_agent_pod_manifest_disables_service_account_token_automount(self):
        manifest = _build_pod_manifest(
            pod_name="sandbox-agent-agent-1",
            pvc_name="sandbox-workspace-agent-1",
            namespace="default",
            image="ghcr.io/example/sandbox:latest",
            runtime_class="gvisor",
            service_account="",
            configmap_name="sandbox-config",
            secret_name="sandbox-secret",
            agent_id="agent-1",
            egress_service_name=None,
            http_proxy_port=3128,
            socks_proxy_port=1080,
            no_proxy=None,
        )

        self.assertFalse(manifest["spec"]["automountServiceAccountToken"])
        self.assertNotIn("serviceAccountName", manifest["spec"])
        env = {
            entry["name"]: entry["value"]
            for entry in manifest["spec"]["containers"][0]["env"]
        }
        self.assertEqual(env["SANDBOX_RUNTIME_CACHE_ROOT"], "/runtime-cache")
        self.assertEqual(env["SANDBOX_AGENT_WORKSPACE_LAYOUT"], "isolated")

    def test_agent_pod_manifest_keeps_explicit_service_account_opt_in(self):
        manifest = _build_pod_manifest(
            pod_name="sandbox-agent-agent-2",
            pvc_name="sandbox-workspace-agent-2",
            namespace="default",
            image="ghcr.io/example/sandbox:latest",
            runtime_class="gvisor",
            service_account="sandbox-sa",
            configmap_name="sandbox-config",
            secret_name="sandbox-secret",
            agent_id="agent-2",
            egress_service_name=None,
            http_proxy_port=3128,
            socks_proxy_port=1080,
            no_proxy=None,
        )

        self.assertFalse(manifest["spec"]["automountServiceAccountToken"])
        self.assertEqual(manifest["spec"]["serviceAccountName"], "sandbox-sa")

    def test_egress_proxy_pod_manifest_disables_service_account_token_automount(self):
        manifest = _build_egress_proxy_pod_manifest(
            pod_name="sandbox-egress-agent-1",
            namespace="default",
            image="ghcr.io/example/egress-proxy:latest",
            runtime_class="gvisor",
            service_account="",
            agent_id="agent-1",
            proxy_server=SimpleNamespace(
                host="proxy.example",
                port=8080,
                username="",
                password="",
                id="proxy-1",
                proxy_type="HTTP",
            ),
            http_listen_port=3128,
            socks_listen_port=1080,
        )

        self.assertFalse(manifest["spec"]["automountServiceAccountToken"])
        self.assertNotIn("serviceAccountName", manifest["spec"])

    def test_build_proxy_env_includes_uppercase_lowercase_and_no_proxy(self):
        env = _build_proxy_env(
            egress_service_name="sandbox-egress-agent-1",
            http_proxy_port=3128,
            socks_proxy_port=1080,
            no_proxy="localhost,127.0.0.1",
        )

        values = {entry["name"]: entry["value"] for entry in env}
        self.assertEqual(values["HTTP_PROXY"], "http://sandbox-egress-agent-1:3128")
        self.assertEqual(values["HTTPS_PROXY"], "http://sandbox-egress-agent-1:3128")
        self.assertEqual(values["FTP_PROXY"], "http://sandbox-egress-agent-1:3128")
        self.assertEqual(values["ALL_PROXY"], "socks5://sandbox-egress-agent-1:1080")
        self.assertEqual(values["http_proxy"], "http://sandbox-egress-agent-1:3128")
        self.assertEqual(values["https_proxy"], "http://sandbox-egress-agent-1:3128")
        self.assertEqual(values["ftp_proxy"], "http://sandbox-egress-agent-1:3128")
        self.assertEqual(values["all_proxy"], "socks5://sandbox-egress-agent-1:1080")
        self.assertEqual(values["NO_PROXY"], "localhost,127.0.0.1")
        self.assertEqual(values["no_proxy"], "localhost,127.0.0.1")

    def test_egress_proxy_pod_manifest_includes_upstream_proxy_scheme(self):
        manifest = _build_egress_proxy_pod_manifest(
            pod_name="sandbox-egress-agent-2",
            namespace="default",
            image="ghcr.io/example/egress-proxy:latest",
            runtime_class="gvisor",
            service_account="",
            agent_id="agent-2",
            proxy_server=SimpleNamespace(
                host="proxy.example",
                port=1080,
                username="",
                password="",
                id="proxy-2",
                proxy_type="SOCKS5",
            ),
            http_listen_port=3128,
            socks_listen_port=1080,
        )

        env = {
            entry["name"]: entry["value"]
            for entry in manifest["spec"]["containers"][0]["env"]
        }
        self.assertEqual(env["UPSTREAM_PROTOCOL"], "socks5")
        self.assertEqual(env["UPSTREAM_PROXY_SCHEME"], "socks5")
        ports = manifest["spec"]["containers"][0]["ports"]
        self.assertEqual(ports[0]["containerPort"], 3128)
        self.assertEqual(ports[1]["containerPort"], 1080)

    def test_egress_proxy_pod_manifest_normalizes_https_to_http_protocol(self):
        manifest = _build_egress_proxy_pod_manifest(
            pod_name="sandbox-egress-agent-https",
            namespace="default",
            image="ghcr.io/example/egress-proxy:latest",
            runtime_class="gvisor",
            service_account="",
            agent_id="agent-https",
            proxy_server=SimpleNamespace(
                host="proxy.example",
                port=443,
                username="",
                password="",
                id="proxy-https",
                proxy_type="HTTPS",
            ),
            http_listen_port=3128,
            socks_listen_port=1080,
        )

        env = {
            entry["name"]: entry["value"]
            for entry in manifest["spec"]["containers"][0]["env"]
        }
        self.assertEqual(env["UPSTREAM_PROTOCOL"], "http")
        self.assertEqual(env["UPSTREAM_PROXY_SCHEME"], "https")

    def test_egress_proxy_service_manifest_exposes_http_and_socks_ports(self):
        manifest = _build_egress_proxy_service_manifest(
            service_name="sandbox-egress-agent-3",
            namespace="default",
            agent_id="agent-3",
            http_port=3128,
            http_target_port=3128,
            socks_port=1080,
            socks_target_port=1080,
        )

        ports = manifest["spec"]["ports"]
        self.assertEqual(ports[0]["name"], "http")
        self.assertEqual(ports[0]["port"], 3128)
        self.assertEqual(ports[1]["name"], "socks5")
        self.assertEqual(ports[1]["port"], 1080)
