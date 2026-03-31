from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings, tag

from api.models import BrowserUseAgent, CommsChannel, PersistentAgent, PersistentAgentConversation
from api.services.sandbox_compute import _active_conversation_channel, _allowed_env_keys, _proxy_env_for_session


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
