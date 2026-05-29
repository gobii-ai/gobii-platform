import json
from smtplib import SMTPException
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.mail import EmailMultiAlternatives
from django.test import TestCase, override_settings, tag
from django.test.utils import modify_settings
from django.urls import reverse
from constants.feature_flags import SUPPORT_INTERCOM
from util.analytics import AnalyticsEvent
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

    @staticmethod
    def _prequalify_payload(include_turnstile=False):
        payload = {
            "name": "Test User",
            "email": "user@example.com",
            "company": "Test Company",
            "role": "Operations Lead",
            "team_size": "6-20",
            "monthly_volume": "250_1000",
            "budget_range": "500_2000",
            "timeline": "this_quarter",
            "use_case": "Automate inbound lead qualification.",
            "website": "https://example.com",
            "notes": "Important account.",
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
        self.assertContains(response, "Support requests move faster through our dedicated support page.")
        self.assertContains(response, reverse("proprietary:support"))

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

    @tag(BATCH_TAG)
    @patch("turnstile.fields.TurnstileField.validate", return_value=None)
    def test_prequalify_post_uses_public_contact_email(self, _mock_validate):
        response = self.client.post(
            reverse("proprietary:prequalify"),
            self._prequalify_payload(include_turnstile=True),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("We will review and follow up within 1-2 business days.", response.content.decode())
        self._assert_single_email(
            to=["contact@example.com"],
            subject="Pre-qualification request: Test Company",
            from_email=settings.DEFAULT_FROM_EMAIL,
            reply_to=["user@example.com"],
        )

    def _login_support_user(self):
        user = get_user_model().objects.create_user(
            username="support-user",
            email="support-user@example.com",
            password="password123",
        )
        self.client.force_login(user)
        return user

    @tag(BATCH_TAG)
    @patch("console.api_views.Analytics.track_event")
    def test_console_support_request_sends_context_email_and_tracks_analytics(self, mock_track_event):
        user = self._login_support_user()
        payload = {
            "message": "The chat sidebar is not responding.",
            "pageUrl": "https://gobii.test/app/agents/agent-1",
            "agentId": "agent-1",
            "agentName": "Research Agent",
            "workspaceContext": {
                "type": "organization",
                "id": "org-1",
                "name": "Acme Ops",
            },
        }

        response = self.client.post(
            reverse("console_app_support_request"),
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})
        self._assert_single_email(
            to=["support@example.com"],
            subject="Gobii app support request",
            from_email=settings.DEFAULT_FROM_EMAIL,
            reply_to=[user.email],
        )
        body = mail.outbox[0].body
        self.assertIn("ID: " + str(user.id), body)
        self.assertIn("Email: support-user@example.com", body)
        self.assertIn("https://gobii.test/app/agents/agent-1", body)
        self.assertIn("ID: agent-1", body)
        self.assertIn("Name: Research Agent", body)
        self.assertIn("type=organization, id=org-1, name=Acme Ops", body)
        self.assertIn("The chat sidebar is not responding.", body)
        mock_track_event.assert_called_once()
        self.assertEqual(mock_track_event.call_args.kwargs.get("event"), AnalyticsEvent.SUPPORT_REQUEST_SUBMITTED)
        self.assertEqual(mock_track_event.call_args.kwargs.get("user_id"), str(user.id))
        props = mock_track_event.call_args.kwargs["properties"]
        self.assertEqual(props.get("message_length"), len(payload["message"]))
        self.assertEqual(props.get("page_url"), payload["pageUrl"])
        self.assertEqual(props.get("agent_id"), payload["agentId"])
        self.assertEqual(props.get("workspace_context_type"), "organization")
        self.assertEqual(props.get("workspace_context_id"), "org-1")

    @tag(BATCH_TAG)
    def test_console_support_request_rejects_empty_message(self):
        self._login_support_user()

        response = self.client.post(
            reverse("console_app_support_request"),
            data=json.dumps({"message": "   "}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"ok": False, "message": "Message is required."})
        self.assertEqual(mail.outbox, [])

    @tag(BATCH_TAG)
    def test_console_support_request_rejects_non_object_payload(self):
        self._login_support_user()

        response = self.client.post(
            reverse("console_app_support_request"),
            data=json.dumps(["Please help."]),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"ok": False, "message": "JSON object expected"})
        self.assertEqual(mail.outbox, [])

    @tag(BATCH_TAG)
    def test_console_support_request_rejects_overlong_message(self):
        self._login_support_user()

        response = self.client.post(
            reverse("console_app_support_request"),
            data=json.dumps({"message": "x" * 4001}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("4000 characters or fewer", response.json()["message"])
        self.assertEqual(mail.outbox, [])

    @tag(BATCH_TAG)
    @override_settings(SUPPORT_EMAIL=None)
    def test_console_support_request_with_no_recipient_email_fails(self):
        self._login_support_user()

        response = self.client.post(
            reverse("console_app_support_request"),
            data=json.dumps({"message": "Please help."}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"ok": False, "message": "Support email is not configured."})
        self.assertEqual(mail.outbox, [])

    @tag(BATCH_TAG)
    def test_console_support_request_send_failure_returns_structured_error(self):
        self._login_support_user()

        with patch("console.support_requests.EmailMultiAlternatives.send", side_effect=SMTPException("nope")):
            response = self.client.post(
                reverse("console_app_support_request"),
                data=json.dumps({"message": "Please help."}),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            response.json(),
            {"ok": False, "message": "Unable to send support request. Please try again later."},
        )
        self.assertEqual(mail.outbox, [])

    @tag(BATCH_TAG)
    def test_console_support_request_requires_login(self):
        response = self.client.post(
            reverse("console_app_support_request"),
            data=json.dumps({"message": "Please help."}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"error": "Authentication required"})
