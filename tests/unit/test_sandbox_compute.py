from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings, tag

from api.models import (
    AgentComputeSession,
    BrowserUseAgent,
    CommsChannel,
    PersistentAgent,
    PersistentAgentConversation,
    ProxyServer,
)
from api.services.sandbox_compute import (
    SandboxComputeUnavailable,
    _SANDBOX_PROXY_CLEARED_ATTR,
    _active_conversation_channel,
    _allowed_env_keys,
    _proxy_env_for_session,
    _select_proxy_for_session,
)


@tag("batch_agent_lifecycle")
class SandboxComputeProxyEnvTests(SimpleTestCase):
    def test_allowed_env_keys_include_proxy_variants(self):
        allowed = _allowed_env_keys()

        expected = {
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "FTP_PROXY",
            "ALL_PROXY",
            "http_proxy",
            "https_proxy",
            "ftp_proxy",
            "all_proxy",
            "NO_PROXY",
            "no_proxy",
        }
        self.assertTrue(expected.issubset(allowed))

    @override_settings(SANDBOX_COMPUTE_NO_PROXY="localhost,127.0.0.1")
    def test_proxy_env_for_session_includes_proxy_variants(self):
        session = SimpleNamespace(
            proxy_server=SimpleNamespace(proxy_url="http://sandbox-egress-agent-1:3128")
        )

        env = _proxy_env_for_session(session)

        self.assertEqual(env["HTTP_PROXY"], "http://sandbox-egress-agent-1:3128")
        self.assertEqual(env["HTTPS_PROXY"], "http://sandbox-egress-agent-1:3128")
        self.assertEqual(env["FTP_PROXY"], "http://sandbox-egress-agent-1:3128")
        self.assertEqual(env["ALL_PROXY"], "http://sandbox-egress-agent-1:3128")
        self.assertEqual(env["http_proxy"], "http://sandbox-egress-agent-1:3128")
        self.assertEqual(env["https_proxy"], "http://sandbox-egress-agent-1:3128")
        self.assertEqual(env["ftp_proxy"], "http://sandbox-egress-agent-1:3128")
        self.assertEqual(env["all_proxy"], "http://sandbox-egress-agent-1:3128")
        self.assertIn("NO_PROXY", env)
        self.assertEqual(env["NO_PROXY"], env["no_proxy"])

    @override_settings(SANDBOX_COMPUTE_NO_PROXY="localhost,127.0.0.1")
    def test_proxy_env_for_session_preserves_socks5_urls(self):
        session = SimpleNamespace(
            proxy_server=SimpleNamespace(proxy_url="socks5://user:pass@proxy.internal:1080")
        )

        env = _proxy_env_for_session(session)

        self.assertEqual(env["HTTP_PROXY"], "socks5://user:pass@proxy.internal:1080")
        self.assertEqual(env["ALL_PROXY"], "socks5://user:pass@proxy.internal:1080")
        self.assertEqual(env["http_proxy"], "socks5://user:pass@proxy.internal:1080")
        self.assertEqual(env["all_proxy"], "socks5://user:pass@proxy.internal:1080")


@tag("batch_agent_lifecycle")
class SandboxComputeAnalyticsTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="sandbox-analytics-user",
            email="sandbox-analytics-user@example.com",
            password="pw",
        )
        browser_agent = BrowserUseAgent.objects.create(user=self.user, name="Sandbox Analytics Browser")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Sandbox Analytics Agent",
            charter="sandbox analytics charter",
            browser_use_agent=browser_agent,
        )

    def test_active_conversation_channel_caches_latest_lookup(self):
        PersistentAgentConversation.objects.create(
            channel=CommsChannel.WEB,
            address="sandbox-analytics@example.com",
            owner_agent=self.agent,
        )

        with self.assertNumQueries(1):
            self.assertEqual(_active_conversation_channel(self.agent), CommsChannel.WEB)
            self.assertEqual(_active_conversation_channel(self.agent), CommsChannel.WEB)


@tag("batch_agent_lifecycle")
class SandboxComputeProxySelectionTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="sandbox-proxy-user",
            email="sandbox-proxy-user@example.com",
            password="pw",
        )
        browser_agent = BrowserUseAgent.objects.create(user=self.user, name="Sandbox Proxy Browser")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Sandbox Proxy Agent",
            charter="sandbox proxy charter",
            browser_use_agent=browser_agent,
        )
        self.inactive_proxy = ProxyServer.objects.create(
            name="Inactive Sandbox Proxy",
            proxy_type=ProxyServer.ProxyType.HTTP,
            host="inactive-proxy.example.com",
            port=8010,
            is_active=False,
        )
        self.active_proxy = ProxyServer.objects.create(
            name="Active Sandbox Proxy",
            proxy_type=ProxyServer.ProxyType.HTTP,
            host="active-proxy.example.com",
            port=8011,
            is_active=True,
        )

    def test_inactive_session_proxy_is_replaced_with_selected_proxy(self):
        session = AgentComputeSession.objects.create(
            agent=self.agent,
            state=AgentComputeSession.State.RUNNING,
            proxy_server=self.inactive_proxy,
        )

        with patch("api.services.sandbox_compute._proxy_required", return_value=False), patch(
            "api.services.sandbox_compute.select_proxy_for_persistent_agent",
            return_value=self.active_proxy,
        ):
            result = _select_proxy_for_session(self.agent, session)

        self.assertEqual(result, self.active_proxy)
        session.refresh_from_db()
        self.assertEqual(session.proxy_server, self.active_proxy)
        self.assertFalse(getattr(session, _SANDBOX_PROXY_CLEARED_ATTR))

    def test_inactive_agent_preferred_proxy_is_not_reused_for_session(self):
        self.agent.browser_use_agent.preferred_proxy = self.inactive_proxy
        self.agent.browser_use_agent.save(update_fields=["preferred_proxy"])
        session = AgentComputeSession.objects.create(
            agent=self.agent,
            state=AgentComputeSession.State.RUNNING,
            proxy_server=self.inactive_proxy,
        )

        with patch("api.services.sandbox_compute._proxy_required", return_value=False), patch(
            "api.models.BrowserUseAgent.select_random_proxy",
            return_value=self.active_proxy,
        ):
            result = _select_proxy_for_session(self.agent, session)

        self.assertEqual(result, self.active_proxy)
        session.refresh_from_db()
        self.assertEqual(session.proxy_server, self.active_proxy)
        self.assertFalse(getattr(session, _SANDBOX_PROXY_CLEARED_ATTR))

    def test_inactive_session_proxy_is_cleared_when_required_proxy_unavailable(self):
        session = AgentComputeSession.objects.create(
            agent=self.agent,
            state=AgentComputeSession.State.RUNNING,
            proxy_server=self.inactive_proxy,
        )

        with patch("api.services.sandbox_compute._proxy_required", return_value=True), patch(
            "api.services.sandbox_compute.select_proxy_for_persistent_agent",
            return_value=None,
        ):
            with self.assertRaisesMessage(
                SandboxComputeUnavailable,
                "No proxy server available for sandbox compute.",
            ):
                _select_proxy_for_session(self.agent, session)

        session.refresh_from_db()
        self.assertIsNone(session.proxy_server)
        self.assertTrue(getattr(session, _SANDBOX_PROXY_CLEARED_ATTR))

    def test_inactive_session_proxy_is_cleared_when_optional_proxy_unavailable(self):
        session = AgentComputeSession.objects.create(
            agent=self.agent,
            state=AgentComputeSession.State.RUNNING,
            proxy_server=self.inactive_proxy,
        )

        with patch("api.services.sandbox_compute._proxy_required", return_value=False), patch(
            "api.services.sandbox_compute.select_proxy_for_persistent_agent",
            return_value=None,
        ):
            result = _select_proxy_for_session(self.agent, session)

        self.assertIsNone(result)
        session.refresh_from_db()
        self.assertIsNone(session.proxy_server)
        self.assertTrue(getattr(session, _SANDBOX_PROXY_CLEARED_ATTR))

    @override_settings(ENABLE_PROXY_ROUTING=False)
    def test_proxy_routing_disabled_clears_existing_session_proxy(self):
        session = AgentComputeSession.objects.create(
            agent=self.agent,
            state=AgentComputeSession.State.RUNNING,
            proxy_server=self.active_proxy,
        )

        with patch("api.services.sandbox_compute.select_proxy_for_persistent_agent") as select_proxy:
            result = _select_proxy_for_session(self.agent, session)

        self.assertIsNone(result)
        select_proxy.assert_not_called()
        session.refresh_from_db()
        self.assertIsNone(session.proxy_server)
        self.assertTrue(getattr(session, _SANDBOX_PROXY_CLEARED_ATTR))
