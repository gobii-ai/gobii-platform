from unittest.mock import patch

from django.test import TestCase, override_settings, tag
from django.test.utils import modify_settings
from django.urls import reverse


BATCH_TAG = "batch_support_turnstile"


@modify_settings(INSTALLED_APPS={"prepend": "proprietary", "append": "turnstile"})
@override_settings(GOBII_PROPRIETARY_MODE=True, TURNSTILE_ENABLED=True)
class SupportViewTurnstileTests(TestCase):
    @tag(BATCH_TAG)
    def test_get_includes_turnstile_widget(self):
        response = self.client.get(reverse("proprietary:support"))

        self.assertContains(response, "cf-turnstile")
        self.assertContains(response, "turnstile/v0/api.js")

    @tag(BATCH_TAG)
    def test_post_without_turnstile_returns_error(self):
        payload = {
            "name": "Test User",
            "email": "user@example.com",
            "subject": "Need help",
            "message": "Please assist.",
        }

        response = self.client.post(reverse("proprietary:support"), payload)

        self.assertEqual(response.status_code, 400)
        self.assertIn("Please prove you are a human.", response.content.decode())

    @tag(BATCH_TAG)
    @patch("turnstile.fields.TurnstileField.validate", return_value=None)
    def test_post_with_turnstile_succeeds(self, _mock_validate):
        payload = {
            "name": "Test User",
            "email": "user@example.com",
            "subject": "Need help",
            "message": "Please assist.",
            "cf-turnstile-response": "stub-token",
        }

        response = self.client.post(reverse("proprietary:support"), payload)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Thank you for your message", response.content.decode())
