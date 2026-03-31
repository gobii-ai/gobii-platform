from unittest.mock import patch

from django.test import TestCase, override_settings, tag


@tag("batch_setup_cookies")
class SetupWizardCsrfTests(TestCase):
    @override_settings(CSRF_COOKIE_NAME="gobii_platform_csrftoken")
    def test_setup_wizard_uses_configured_csrf_cookie_name(self):
        with patch("setup.views.is_initial_setup_complete", return_value=False), patch(
            "setup.views.SetupWizardView._ensure_database_ready"
        ):
            response = self.client.get("/setup/")

        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn('meta name="csrf-cookie-name" content="gobii_platform_csrftoken"', content)
        self.assertIn("function getCsrfCookieName()", content)
        self.assertIn("window.getCsrfTokenValue ? window.getCsrfTokenValue() : ''", content)
        self.assertNotIn("document.cookie.match(/csrftoken=([^;]+)/)", content)
