from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings, tag

from api.agent.files.attachment_helpers import build_signed_filespace_download_url


@tag("batch_agent_filesystem")
class SignedFilesDownloadUrlTests(SimpleTestCase):
    @override_settings(PUBLIC_SITE_URL="http://localhost:8000")
    @patch("api.agent.files.attachment_helpers.Site.objects.get_current")
    def test_private_site_domain_uses_http(self, mock_get_current):
        mock_get_current.return_value = SimpleNamespace(domain="100.68.89.32:8000")

        url = build_signed_filespace_download_url("agent-1", "node-1")

        self.assertTrue(url.startswith("http://100.68.89.32:8000/d/"), url)

    @override_settings(PUBLIC_SITE_URL="http://localhost:8000")
    @patch("api.agent.files.attachment_helpers.Site.objects.get_current")
    def test_public_site_domain_defaults_to_https_when_public_url_is_localhost(self, mock_get_current):
        mock_get_current.return_value = SimpleNamespace(domain="preview.gobii.ai")

        url = build_signed_filespace_download_url("agent-1", "node-1")

        self.assertTrue(url.startswith("https://preview.gobii.ai/d/"), url)

    @override_settings(PUBLIC_SITE_URL="https://app.gobii.ai")
    @patch("api.agent.files.attachment_helpers.Site.objects.get_current")
    def test_public_site_url_scheme_is_respected(self, mock_get_current):
        mock_get_current.return_value = SimpleNamespace(domain="staging.gobii.ai")

        url = build_signed_filespace_download_url("agent-1", "node-1")

        self.assertTrue(url.startswith("https://staging.gobii.ai/d/"), url)

    @override_settings(PUBLIC_SITE_URL="http://localhost:8000")
    @patch("api.agent.files.attachment_helpers.Site.objects.get_current")
    def test_explicit_scheme_on_site_domain_is_respected(self, mock_get_current):
        mock_get_current.return_value = SimpleNamespace(domain="http://100.68.89.32:8000")

        url = build_signed_filespace_download_url("agent-1", "node-1")

        self.assertTrue(url.startswith("http://100.68.89.32:8000/d/"), url)
