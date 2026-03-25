from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, tag

from api.services.sandbox_kubernetes import (
    KubernetesSandboxBackend,
    SandboxComputeUnavailable,
    _build_pod_manifest,
    _build_sandbox_service_manifest,
    _build_transparent_egress_network_policy_manifest,
    _build_transparent_egress_secret_manifest,
    _pod_name,
    _transparent_egress_network_policy_name,
    _transparent_egress_secret_name,
)


@tag("batch_agent_lifecycle")
class KubernetesSandboxMCPDiscoveryTests(SimpleTestCase):
    def _backend(self) -> KubernetesSandboxBackend:
        backend = object.__new__(KubernetesSandboxBackend)
        backend._client = Mock()
        backend._namespace = "default"
        backend._compute_api_token = "supervisor-token"
        backend._pod_image = "ghcr.io/example/sandbox:latest"
        backend._pod_runtime_class = "gvisor"
        backend._pod_service_account = "sandbox-sa"
        backend._pod_configmap = "sandbox-config"
        backend._pod_secret = "sandbox-secret"
        backend._pod_ready_timeout = 60
        backend._pvc_size = "1Gi"
        backend._pvc_storage_class = ""
        backend._snapshot_class = ""
        backend._proxy_timeout = 30
        backend._mcp_timeout = 30
        backend._tool_timeout = 30
        backend._discovery_timeout = 30
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
        session = SimpleNamespace(pod_name="sandbox-agent-agent-1", proxy_server=SimpleNamespace(proxy_url="socks5://proxy.example:1080"))

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
        self.assertEqual(mock_proxy_post.call_args.args[0], session.pod_name)
        self.assertEqual(mock_proxy_post.call_args.args[1], "/sandbox/compute/discover_mcp_tools")
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

        with patch("api.services.sandbox_kubernetes.requests.Session", return_value=session):
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


@tag("batch_agent_lifecycle")
class KubernetesSandboxProvisioningTests(SimpleTestCase):
    def _backend(self) -> KubernetesSandboxBackend:
        backend = object.__new__(KubernetesSandboxBackend)
        backend._client = Mock()
        backend._namespace = "default"
        backend._compute_api_token = "supervisor-token"
        backend._pod_image = "ghcr.io/example/sandbox:latest"
        backend._pod_runtime_class = "gvisor"
        backend._pod_service_account = "sandbox-sa"
        backend._pod_configmap = "sandbox-config"
        backend._pod_secret = "sandbox-secret"
        backend._pod_ready_timeout = 60
        backend._pvc_size = "1Gi"
        backend._pvc_storage_class = ""
        backend._snapshot_class = ""
        backend._proxy_timeout = 30
        backend._mcp_timeout = 30
        backend._tool_timeout = 30
        backend._discovery_timeout = 30
        return backend

    def test_deploy_or_resume_requires_proxy_server(self):
        backend = self._backend()
        agent = SimpleNamespace(id="agent-missing-proxy")
        session = SimpleNamespace(proxy_server=None, workspace_snapshot=None)

        with self.assertRaises(SandboxComputeUnavailable):
            backend.deploy_or_resume(agent, session)

    def test_deploy_or_resume_requires_socks5_proxy(self):
        backend = self._backend()
        agent = SimpleNamespace(id="agent-http-proxy")
        session = SimpleNamespace(
            proxy_server=SimpleNamespace(
                proxy_type="HTTPS",
                host="proxy.example",
                port=443,
                username="",
                password="",
            ),
            workspace_snapshot=None,
        )

        with self.assertRaises(SandboxComputeUnavailable):
            backend.deploy_or_resume(agent, session)

    def test_ensure_transparent_egress_upserts_secret_and_policy(self):
        backend = self._backend()
        agent = SimpleNamespace(id="agent-transparent")
        proxy_server = SimpleNamespace(
            proxy_type="SOCKS5",
            host="proxy.example",
            port=1080,
            username="user",
            password="secret",
        )
        backend._upsert_secret = Mock()
        backend._upsert_network_policy = Mock()

        with patch("api.services.sandbox_kubernetes._resolve_proxy_host_ips", return_value=["1.2.3.4", "5.6.7.8"]):
            secret_name = backend._ensure_transparent_egress(agent, proxy_server)

        self.assertEqual(secret_name, _transparent_egress_secret_name(agent.id))
        backend._upsert_secret.assert_called_once()
        backend._upsert_network_policy.assert_called_once()
        policy_body = backend._upsert_network_policy.call_args.args[2]
        upstream_targets = policy_body["spec"]["egress"][1]["to"]
        self.assertEqual(upstream_targets[0]["ipBlock"]["cidr"], "1.2.3.4/32")
        self.assertEqual(upstream_targets[1]["ipBlock"]["cidr"], "5.6.7.8/32")

    def test_deploy_or_resume_creates_transparent_egress_resources_and_agent_service(self):
        backend = self._backend()
        agent = SimpleNamespace(id="agent-svc")
        session = SimpleNamespace(
            proxy_server=SimpleNamespace(
                proxy_type="SOCKS5",
                host="proxy.example",
                port=1080,
                username="user",
                password="secret",
            ),
            workspace_snapshot=None,
        )
        backend._create_pvc = Mock()
        backend._create_service = Mock()
        backend._get_pod = Mock(return_value=None)
        backend._create_pod = Mock()
        backend._wait_for_pod_ready = Mock(return_value=True)
        backend._ensure_transparent_egress = Mock(return_value="sandbox-egress-config-agent-svc")

        with patch("api.services.sandbox_kubernetes._resource_exists", side_effect=[False, False]):
            result = backend.deploy_or_resume(agent, session)

        self.assertEqual(result.state, "running")
        backend._ensure_transparent_egress.assert_called_once_with(agent, session.proxy_server)
        backend._create_service.assert_called_once_with("sandbox-agent-agent-svc", agent_id="agent-svc")
        backend._create_pod.assert_called_once_with(
            "sandbox-agent-agent-svc",
            "sandbox-workspace-agent-svc",
            agent_id="agent-svc",
            transparent_egress_secret_name="sandbox-egress-config-agent-svc",
        )

    def test_deploy_or_resume_recreates_legacy_pod_without_transparent_annotations(self):
        backend = self._backend()
        agent = SimpleNamespace(id="agent-legacy")
        session = SimpleNamespace(
            proxy_server=SimpleNamespace(
                proxy_type="SOCKS5",
                host="proxy.example",
                port=1080,
                username="",
                password="",
            ),
            workspace_snapshot=None,
        )
        backend._create_pvc = Mock()
        backend._create_service = Mock()
        backend._get_pod = Mock(
            return_value={
                "metadata": {"annotations": {}},
                "status": {"phase": "Running"},
                "spec": {"containers": [{"env": [{"name": "HTTP_PROXY", "value": "http://old"}]}]},
            }
        )
        backend._delete_pod = Mock()
        backend._create_pod = Mock()
        backend._wait_for_pod_ready = Mock(return_value=True)
        backend._ensure_transparent_egress = Mock(return_value="sandbox-egress-config-agent-legacy")

        with patch("api.services.sandbox_kubernetes._resource_exists", side_effect=[False, False]):
            result = backend.deploy_or_resume(agent, session)

        self.assertEqual(result.state, "running")
        backend._delete_pod.assert_called_once_with("sandbox-agent-agent-legacy")
        backend._create_pod.assert_called_once_with(
            "sandbox-agent-agent-legacy",
            "sandbox-workspace-agent-legacy",
            agent_id="agent-legacy",
            transparent_egress_secret_name="sandbox-egress-config-agent-legacy",
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
            transparent_egress_secret_name="sandbox-egress-config-agent-1",
        )

        self.assertFalse(manifest["spec"]["automountServiceAccountToken"])
        self.assertNotIn("serviceAccountName", manifest["spec"])
        env = manifest["spec"]["containers"][0]["env"]
        self.assertEqual(env, [{"name": "SANDBOX_RUNTIME_CACHE_ROOT", "value": "/runtime-cache"}])
        annotations = manifest["metadata"]["annotations"]
        self.assertEqual(annotations["gobii.ai/transparent-egress-mode"], "socks5")
        self.assertEqual(annotations["gobii.ai/transparent-egress-secret"], "sandbox-egress-config-agent-1")
        self.assertEqual(annotations["gobii.ai/transparent-egress-port"], "15001")

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
            transparent_egress_secret_name="sandbox-egress-config-agent-2",
        )

        self.assertFalse(manifest["spec"]["automountServiceAccountToken"])
        self.assertEqual(manifest["spec"]["serviceAccountName"], "sandbox-sa")

    def test_transparent_egress_secret_manifest_uses_socks5_contract(self):
        manifest = _build_transparent_egress_secret_manifest(
            secret_name="sandbox-egress-config-agent-3",
            namespace="default",
            agent_id="agent-3",
            proxy_server=SimpleNamespace(
                host="proxy.example",
                port=1080,
                username="user",
                password="secret",
                proxy_type="SOCKS5",
            ),
        )

        self.assertEqual(manifest["type"], "Opaque")
        self.assertEqual(manifest["metadata"]["labels"]["agent_id"], "agent-3")
        self.assertEqual(manifest["stringData"]["UPSTREAM_HOST"], "proxy.example")
        self.assertEqual(manifest["stringData"]["UPSTREAM_PORT"], "1080")
        self.assertEqual(manifest["stringData"]["UPSTREAM_USERNAME"], "user")
        self.assertEqual(manifest["stringData"]["UPSTREAM_PASSWORD"], "secret")
        self.assertEqual(manifest["stringData"]["UPSTREAM_PROXY_TYPE"], "socks5")

    def test_transparent_egress_policy_manifest_allows_dns_and_upstream_only(self):
        manifest = _build_transparent_egress_network_policy_manifest(
            policy_name="sandbox-egress-policy-agent-4",
            namespace="default",
            agent_id="agent-4",
            upstream_ips=["1.2.3.4", "5.6.7.8"],
            upstream_port=10016,
        )

        self.assertEqual(manifest["metadata"]["name"], "sandbox-egress-policy-agent-4")
        self.assertEqual(manifest["spec"]["podSelector"]["matchLabels"]["agent_id"], "agent-4")
        ingress = manifest["spec"]["ingress"][0]
        self.assertEqual(ingress["ports"][0]["port"], 8080)
        dns_egress = manifest["spec"]["egress"][0]
        self.assertEqual(dns_egress["ports"][0]["port"], 53)
        upstream_egress = manifest["spec"]["egress"][1]
        self.assertEqual(upstream_egress["ports"][0]["port"], 10016)
        self.assertEqual(upstream_egress["to"][0]["ipBlock"]["cidr"], "1.2.3.4/32")
        self.assertEqual(upstream_egress["to"][1]["ipBlock"]["cidr"], "5.6.7.8/32")
