from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings, tag
from django.test.utils import modify_settings
from django.urls import reverse


BATCH_TAG = "batch_support_turnstile"


@modify_settings(INSTALLED_APPS={"prepend": "proprietary", "append": "turnstile"})
@override_settings(
    GOBII_PROPRIETARY_MODE=True,
    TURNSTILE_ENABLED=True,
    SUPPORT_EMAIL="support@example.com",
    PUBLIC_CONTACT_EMAIL="contact@example.com",
)
class SupportViewTurnstileTests(TestCase):
    @staticmethod
    def _payload(include_turnstile=False):
        payload = {
            "name": "Test User",
            "email": "user@example.com",
            "subject": "Need help",
            "message": "Please assist.",
        }
        if include_turnstile:
            payload["cf-turnstile-response"] = "stub-token"
        return payload

    @tag(BATCH_TAG)
    def test_get_includes_turnstile_widget(self):
        response = self.client.get(reverse("proprietary:support"))

        self.assertContains(response, "cf-turnstile")
        self.assertContains(response, "turnstile/v0/api.js")

    @tag(BATCH_TAG)
    def test_post_without_turnstile_returns_error(self):
        response = self.client.post(reverse("proprietary:support"), self._payload())

        self.assertEqual(response.status_code, 400)
        self.assertIn("Please prove you are a human.", response.content.decode())

    @tag(BATCH_TAG)
    @patch("turnstile.fields.TurnstileField.validate", return_value=None)
    def test_post_with_turnstile_succeeds(self, _mock_validate):
        response = self.client.post(reverse("proprietary:support"), self._payload(include_turnstile=True))

        self.assertEqual(response.status_code, 200)
        self.assertIn("Thank you for your message", response.content.decode())
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["support@example.com"])
        self.assertEqual(mail.outbox[0].subject, "Support Request: Need help")

    @tag(BATCH_TAG)
    def test_contact_get_includes_turnstile_widget_and_hides_faq(self):
        response = self.client.get(reverse("proprietary:contact"))

        self.assertContains(response, "cf-turnstile")
        self.assertContains(response, "turnstile/v0/api.js")
        self.assertNotContains(response, "Frequently Asked Questions")

    @tag(BATCH_TAG)
    def test_contact_get_includes_turnstile_widget_for_authenticated_users(self):
        user = get_user_model().objects.create_user(
            username="turnstile-user",
            email="turnstile-user@example.com",
            password="password123",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("proprietary:contact"))

        self.assertContains(response, "cf-turnstile")
        self.assertContains(response, "turnstile/v0/api.js")

    @tag(BATCH_TAG)
    def test_contact_post_without_turnstile_returns_error(self):
        response = self.client.post(reverse("proprietary:contact"), self._payload())

        self.assertEqual(response.status_code, 400)
        self.assertIn("Please prove you are a human.", response.content.decode())

    @tag(BATCH_TAG)
    @patch("turnstile.fields.TurnstileField.validate", return_value=None)
    def test_contact_post_with_turnstile_succeeds(self, _mock_validate):
        response = self.client.post(reverse("proprietary:contact"), self._payload(include_turnstile=True))

        self.assertEqual(response.status_code, 200)
        self.assertIn("Thank you for your message", response.content.decode())
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["contact@example.com"])
        self.assertEqual(mail.outbox[0].subject, "Contact Request: Need help")
