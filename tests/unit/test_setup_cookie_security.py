from django.http import HttpResponse
from django.middleware.security import SecurityMiddleware
from django.test import RequestFactory, SimpleTestCase, override_settings, tag

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


@tag("batch_setup_cookies")
class TransportSecurityTests(SimpleTestCase):
    def setUp(self):
        self.request_factory = RequestFactory()

    @staticmethod
    def _middleware():
        return SecurityMiddleware(lambda request: HttpResponse("ok"))

    def test_https_site_enables_transport_security_outside_debug(self):
        self.assertTrue(
            project_settings._secure_transport_default(
                "https://example.com",
                debug=False,
            )
        )

    def test_http_site_does_not_enable_transport_security(self):
        self.assertFalse(
            project_settings._secure_transport_default(
                "http://localhost:8000",
                debug=False,
            )
        )

    def test_debug_does_not_enable_transport_security(self):
        self.assertFalse(
            project_settings._secure_transport_default(
                "https://example.com",
                debug=True,
            )
        )

    @override_settings(
        SECURE_SSL_REDIRECT=True,
        SECURE_REDIRECT_EXEMPT=[r"^healthz/$"],
    )
    def test_http_requests_redirect_except_for_internal_health_check(self):
        middleware = self._middleware()
        redirect = middleware(self.request_factory.get("/app/"))
        health_check = middleware(self.request_factory.get("/healthz/"))

        self.assertEqual(redirect.status_code, 301)
        self.assertEqual(redirect["Location"], "https://testserver/app/")
        self.assertEqual(health_check.status_code, 200)

    @override_settings(
        SECURE_SSL_REDIRECT=True,
        SECURE_HSTS_SECONDS=31536000,
        SECURE_HSTS_INCLUDE_SUBDOMAINS=True,
        SECURE_HSTS_PRELOAD=True,
    )
    def test_secure_responses_include_full_hsts_policy(self):
        response = self._middleware()(
            self.request_factory.get("/", HTTP_X_FORWARDED_PROTO="https")
        )

        self.assertEqual(
            response["Strict-Transport-Security"],
            "max-age=31536000; includeSubDomains; preload",
        )
