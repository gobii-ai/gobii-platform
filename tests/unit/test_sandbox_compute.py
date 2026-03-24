from types import SimpleNamespace

from django.test import SimpleTestCase, override_settings, tag

from api.services.sandbox_compute import _allowed_env_keys, _proxy_env_for_session


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
