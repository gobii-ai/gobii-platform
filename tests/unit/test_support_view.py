from unittest.mock import patch
from smtplib import SMTPException

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.mail import EmailMultiAlternatives
from django.test import TestCase, override_settings, tag
from django.test.utils import modify_settings
from django.urls import reverse
from constants.feature_flags import SUPPORT_INTERCOM
from waffle.models import Flag
from waffle.testutils import override_flag


BATCH_TAG = "batch_support_turnstile"


@modify_settings(INSTALLED_APPS={"prepend": "proprietary", "append": "turnstile"})
@override_settings(
    GOBII_PROPRIETARY_MODE=True,
    TURNSTILE_ENABLED=True,
    SUPPORT_EMAIL="support@example.com",
    PUBLIC_CONTACT_EMAIL="contact@example.com",
    INTERCOM_SUPPORT_EMAIL="help@gobii.ai",
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

    def _assert_single_email(
        self,
        *,
        to,
        subject,
        from_email=None,
        reply_to=None,
        body=None,
        alternatives=None,
    ):
        self.assertEqual(len(mail.outbox), 1)
        outbound = mail.outbox[0]
        self.assertEqual(outbound.to, to)
        self.assertEqual(outbound.subject, subject)
        if from_email is not None:
            self.assertEqual(outbound.from_email, from_email)
        if reply_to is not None:
            self.assertEqual(outbound.reply_to, reply_to)
        if body is not None:
            self.assertEqual(outbound.body, body)
        if alternatives is not None:
            self.assertEqual(outbound.alternatives, alternatives)

    @tag(BATCH_TAG)
    def test_get_includes_turnstile_widget(self):
        response = self.client.get(reverse("proprietary:support"))

        self.assertContains(response, "cf-turnstile")
        self.assertContains(response, "turnstile/v0/api.js")
        self.assertNotContains(response, "routed through our Intercom support inbox")

    @tag(BATCH_TAG)
    def test_get_shows_intercom_copy_when_flag_enabled(self):
        with override_flag(SUPPORT_INTERCOM, active=True):
            response = self.client.get(reverse("proprietary:support"))

        self.assertContains(response, "routed through our Intercom support inbox")

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
        self._assert_single_email(
            to=["support@example.com"],
            subject="Support Request: Need help",
            from_email=settings.DEFAULT_FROM_EMAIL,
        )

    @tag(BATCH_TAG)
    @patch("turnstile.fields.TurnstileField.validate", return_value=None)
    def test_support_intercom_flag_routes_to_intercom_with_user_sender(self, _mock_validate):
        with override_flag(SUPPORT_INTERCOM, active=True):
            response = self.client.post(reverse("proprietary:support"), self._payload(include_turnstile=True))

        self.assertEqual(response.status_code, 200)
        self._assert_single_email(
            to=["help@gobii.ai"],
            subject="Need help",
            from_email="Test User <user@example.com>",
            reply_to=["Test User <user@example.com>"],
            body="Please assist.",
            alternatives=[],
        )

    @tag(BATCH_TAG)
    @patch("turnstile.fields.TurnstileField.validate", return_value=None)
    def test_support_intercom_authenticated_flag_rollout_routes_anonymous_submissions(self, _mock_validate):
        Flag.objects.update_or_create(
            name=SUPPORT_INTERCOM,
            defaults={
                "everyone": None,
                "percent": 0,
                "superusers": False,
                "staff": False,
                "authenticated": True,
            },
        )

        response = self.client.post(reverse("proprietary:support"), self._payload(include_turnstile=True))

        self.assertEqual(response.status_code, 200)
        self._assert_single_email(to=["help@gobii.ai"], subject="Need help")

    @tag(BATCH_TAG)
    @patch("turnstile.fields.TurnstileField.validate", return_value=None)
    def test_support_intercom_flag_falls_back_to_default_sender_on_rejection(self, _mock_validate):
        attempted_senders = []
        original_send = EmailMultiAlternatives.send

        def send_with_sender_rejection(message, fail_silently=False):
            attempted_senders.append(message.from_email)
            if message.from_email != settings.DEFAULT_FROM_EMAIL:
                raise SMTPException("Sender rejected")
            return original_send(message, fail_silently=fail_silently)

        with (
            override_flag(SUPPORT_INTERCOM, active=True),
            patch(
                "proprietary.views.EmailMultiAlternatives.send",
                autospec=True,
                side_effect=send_with_sender_rejection,
            ),
        ):
            response = self.client.post(reverse("proprietary:support"), self._payload(include_turnstile=True))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(attempted_senders), 2)
        self.assertIn("user@example.com", attempted_senders[0])
        self.assertEqual(attempted_senders[1], settings.DEFAULT_FROM_EMAIL)
        self._assert_single_email(
            to=["help@gobii.ai"],
            subject="Need help",
            from_email=settings.DEFAULT_FROM_EMAIL,
            reply_to=["Test User <user@example.com>"],
            body="Please assist.",
            alternatives=[],
        )

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
        self._assert_single_email(
            to=["contact@example.com"],
            subject="Contact Request: Need help",
            from_email=settings.DEFAULT_FROM_EMAIL,
        )

    @tag(BATCH_TAG)
    @patch("turnstile.fields.TurnstileField.validate", return_value=None)
    def test_support_intercom_flag_does_not_affect_contact_path(self, _mock_validate):
        with override_flag(SUPPORT_INTERCOM, active=True):
            response = self.client.post(reverse("proprietary:contact"), self._payload(include_turnstile=True))

        self.assertEqual(response.status_code, 200)
        self._assert_single_email(
            to=["contact@example.com"],
            subject="Contact Request: Need help",
            from_email=settings.DEFAULT_FROM_EMAIL,
            reply_to=[],
        )

    @tag(BATCH_TAG)
    @override_settings(SUPPORT_EMAIL=None)
    @patch("turnstile.fields.TurnstileField.validate", return_value=None)
    def test_support_post_with_no_recipient_email_fails(self, _mock_validate):
        response = self.client.post(reverse("proprietary:support"), self._payload(include_turnstile=True))
        self.assertEqual(response.status_code, 500)
        self.assertIn("Support email is not configured.", response.content.decode())

    @tag(BATCH_TAG)
    @override_settings(PUBLIC_CONTACT_EMAIL=None)
    @patch("turnstile.fields.TurnstileField.validate", return_value=None)
    def test_contact_post_with_no_recipient_email_fails(self, _mock_validate):
        response = self.client.post(reverse("proprietary:contact"), self._payload(include_turnstile=True))
        self.assertEqual(response.status_code, 500)
        self.assertIn("Contact email is not configured.", response.content.decode())
