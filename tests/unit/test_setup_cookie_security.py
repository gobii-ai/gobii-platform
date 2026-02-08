from django.test import SimpleTestCase, tag

from config import settings as project_settings


@tag("batch_setup_cookies")
class CookieSecurityInferenceTests(SimpleTestCase):
    def test_public_site_url_default_is_localhost_in_debug(self):
        self.assertEqual(
            project_settings._public_site_url_default(debug=True),
            "http://localhost:8000",
        )

    def test_cookie_secure_default_for_http_site_url_in_prod(self):
        self.assertFalse(
            project_settings._cookie_secure_default(
                "http://localhost:7000",
                debug=False,
            )
        )

    def test_cookie_secure_default_for_https_site_url_in_prod(self):
        self.assertTrue(
            project_settings._cookie_secure_default(
                "https://example.com",
                debug=False,
            )
        )

    def test_cookie_secure_default_for_protocol_relative_url_in_prod(self):
        self.assertFalse(
            project_settings._cookie_secure_default(
                "//example.com",
                debug=False,
            )
        )

    def test_cookie_secure_default_is_false_in_debug_even_for_https_site_url(self):
        self.assertFalse(
            project_settings._cookie_secure_default(
                "https://example.com",
                debug=True,
            )
        )
