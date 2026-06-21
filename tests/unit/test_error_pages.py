from django.core.exceptions import PermissionDenied
from django.test import RequestFactory, SimpleTestCase, override_settings, tag

from config.error_views import csrf_failure, permission_denied, server_error


@tag("batch_pages")
class ErrorPageRenderingTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    @override_settings(DEBUG=False)
    def test_site_404_renders_branded_page_with_404_status(self):
        response = self.client.get("/definitely-not-a-real-gobii-page/")

        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "That page is not here.", status_code=404)
        self.assertContains(response, "Browse templates", status_code=404)

    def test_permission_denied_renders_branded_page_with_403_status(self):
        request = self.factory.get("/private/")

        response = permission_denied(request, PermissionDenied("not allowed"))

        self.assertEqual(response.status_code, 403)
        content = response.content.decode("utf-8")
        self.assertIn("You do not have access to this page.", content)
        self.assertIn(">403<", content)

    def test_csrf_failure_renders_branded_page_with_403_status(self):
        request = self.factory.post("/accounts/login/")

        response = csrf_failure(request, reason="CSRF cookie not set.")

        self.assertEqual(response.status_code, 403)
        content = response.content.decode("utf-8")
        self.assertIn("You do not have access to this page.", content)
        self.assertIn(">403<", content)

    def test_server_error_renders_branded_page_with_500_status(self):
        request = self.factory.get("/broken/")

        response = server_error(request)

        self.assertEqual(response.status_code, 500)
        content = response.content.decode("utf-8")
        self.assertIn("We hit an internal error.", content)
        self.assertIn(">500<", content)
