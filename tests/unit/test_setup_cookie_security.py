from django.test import SimpleTestCase, tag

from config import settings as project_settings


@tag("batch_setup_cookies")
class CookieSecurityInferenceTests(SimpleTestCase):
    def test_cookie_secure_default_for_http_site_url(self):
        self.assertFalse(project_settings._cookie_secure_default("http://localhost:7000"))

    def test_cookie_secure_default_for_https_site_url(self):
        self.assertTrue(project_settings._cookie_secure_default("https://example.com"))

    def test_cookie_secure_default_for_protocol_relative_url(self):
        self.assertFalse(project_settings._cookie_secure_default("//example.com"))
