from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.urls import reverse

from api.models import UserEmail
from util.analytics import AnalyticsSource


@tag("batch_console_api")
class StaffUserEmailTriggerTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.staff_user = user_model.objects.create_user(
            username="staff@example.com",
            email="staff@example.com",
            password="testpass123",
            is_staff=True,
        )
        self.target_user = user_model.objects.create_user(
            username="target@example.com",
            email="target@example.com",
            password="testpass123",
        )
        self.active_email = UserEmail.objects.create(
            name="Activation Nudge",
            event_name="Activation Nudge Requested",
        )
        self.inactive_email = UserEmail.objects.create(
            name="Legacy Nudge",
            event_name="Legacy Nudge Requested",
            is_active=False,
        )
        self.client.force_login(self.staff_user)

    def test_detail_includes_active_user_email_triggers(self):
        response = self.client.get(reverse("staff-user-detail-api", args=[self.target_user.id]))

        self.assertEqual(response.status_code, 200)
        triggers = response.json()["userEmails"]["triggers"]
        self.assertEqual(
            triggers,
            [
                {
                    "id": self.active_email.id,
                    "name": "Activation Nudge",
                    "eventName": "Activation Nudge Requested",
                }
            ],
        )

    @patch("console.api_views.Analytics.track")
    def test_send_user_email_trigger_tracks_configured_event(self, mock_track):
        response = self.client.post(
            reverse(
                "staff-user-email-trigger-send",
                args=[self.target_user.id, self.active_email.id],
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["userEmail"]["id"], self.active_email.id)
        mock_track.assert_called_once()
        self.assertEqual(mock_track.call_args.kwargs["user_id"], self.target_user.id)
        self.assertEqual(mock_track.call_args.kwargs["event"], "Activation Nudge Requested")
        properties = mock_track.call_args.kwargs["properties"]
        self.assertEqual(properties["medium"], str(AnalyticsSource.CONSOLE))
        self.assertEqual(properties["triggered_from"], "staff_user_page")
        self.assertEqual(properties["user_email_trigger_id"], str(self.active_email.id))
        self.assertEqual(properties["user_email_trigger_name"], "Activation Nudge")
        self.assertEqual(properties["triggered_by_staff_user_id"], str(self.staff_user.id))
        self.assertEqual(properties["triggered_by_staff_user_email"], "staff@example.com")
        self.assertEqual(properties["target_user_id"], str(self.target_user.id))
        self.assertEqual(properties["target_user_email"], "target@example.com")

    @patch("console.api_views.Analytics.track")
    def test_send_inactive_user_email_trigger_returns_not_found(self, mock_track):
        response = self.client.post(
            reverse(
                "staff-user-email-trigger-send",
                args=[self.target_user.id, self.inactive_email.id],
            )
        )

        self.assertEqual(response.status_code, 404)
        mock_track.assert_not_called()

    @patch("console.api_views.Analytics.track")
    def test_send_user_email_trigger_requires_staff(self, mock_track):
        self.client.force_login(self.target_user)
        response = self.client.post(
            reverse(
                "staff-user-email-trigger-send",
                args=[self.target_user.id, self.active_email.id],
            )
        )

        self.assertEqual(response.status_code, 403)
        mock_track.assert_not_called()
