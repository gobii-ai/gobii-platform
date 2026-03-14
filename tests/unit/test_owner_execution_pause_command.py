from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, tag
from django.utils import timezone

from api.models import Organization
from api.services.owner_execution_pause import (
    pause_owner_execution as real_pause_owner_execution,
    resume_owner_execution as real_resume_owner_execution,
)
from util.analytics import AnalyticsEvent, AnalyticsSource


@tag("batch_owner_billing")
class OwnerExecutionPauseCommandTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="pause-owner@example.com",
            email="pause-owner@example.com",
            password="password123",
        )
        self.org = Organization.objects.create(
            name="Pause Test Org",
            slug="pause-test-org",
            plan="free",
            created_by=self.user,
        )

    def test_show_outputs_current_pause_state_for_user(self):
        billing = self.user.billing
        billing.execution_paused = True
        billing.execution_pause_reason = "billing_delinquency"
        billing.execution_paused_at = timezone.now()
        billing.save(
            update_fields=[
                "execution_paused",
                "execution_pause_reason",
                "execution_paused_at",
            ]
        )

        out = StringIO()
        call_command("owner_execution_pause", "show", "--user-email", self.user.email, stdout=out)

        output = out.getvalue()
        self.assertIn(f"user:{self.user.id}:{self.user.email}", output)
        self.assertIn("paused=True", output)
        self.assertIn("reason=billing_delinquency", output)

    @patch("api.services.owner_execution_pause.Analytics.track_event")
    def test_pause_updates_user_state(self, mock_track_event):
        out = StringIO()

        with patch(
            "api.management.commands.owner_execution_pause.pause_owner_execution",
            wraps=real_pause_owner_execution,
        ) as mock_pause:
            call_command(
                "owner_execution_pause",
                "pause",
                "--user-email",
                self.user.email,
                "--reason",
                "manual_local_test",
                "--skip-cleanup",
                stdout=out,
            )

        self.user.billing.refresh_from_db()
        self.assertTrue(self.user.billing.execution_paused)
        self.assertEqual(self.user.billing.execution_pause_reason, "manual_local_test")
        self.assertIsNotNone(self.user.billing.execution_paused_at)
        self.assertEqual(mock_pause.call_args.kwargs["trigger_agent_cleanup"], False)
        self.assertIn("paused=True", out.getvalue())
        mock_track_event.assert_called_once()
        self.assertEqual(mock_track_event.call_args.kwargs["event"], AnalyticsEvent.ACCOUNT_EXECUTION_PAUSED)
        self.assertEqual(mock_track_event.call_args.kwargs["source"], AnalyticsSource.NA)

    def test_resume_updates_org_state(self):
        billing = self.org.billing
        billing.execution_paused = True
        billing.execution_pause_reason = "billing_delinquency"
        billing.execution_paused_at = timezone.now()
        billing.save(
            update_fields=[
                "execution_paused",
                "execution_pause_reason",
                "execution_paused_at",
            ]
        )
        out = StringIO()

        with patch(
            "api.management.commands.owner_execution_pause.resume_owner_execution",
            wraps=real_resume_owner_execution,
        ) as mock_resume:
            call_command(
                "owner_execution_pause",
                "resume",
                "--org-slug",
                self.org.slug,
                "--skip-enqueue",
                stdout=out,
            )

        billing.refresh_from_db()
        self.assertFalse(billing.execution_paused)
        self.assertEqual(billing.execution_pause_reason, "")
        self.assertIsNone(billing.execution_paused_at)
        self.assertEqual(mock_resume.call_args.kwargs["enqueue_agent_resume"], False)
        self.assertIn(f"organization:{self.org.id}:{self.org.slug}", out.getvalue())
        self.assertIn("paused=False", out.getvalue())
