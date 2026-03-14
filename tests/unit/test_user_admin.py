from types import SimpleNamespace
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import Client, RequestFactory, TestCase, tag
from django.urls import reverse

from api.admin import CustomUserAdmin
from api.models import ExecutionPauseReasonChoices
from util.analytics import AnalyticsEvent


@tag("batch_owner_billing")
class UserAdminExecutionPauseTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.factory = RequestFactory()
        User = get_user_model()
        self.admin_user = User.objects.create_superuser(
            username="admin@example.com",
            email="admin@example.com",
            password="testpass123",
        )
        self.target_user = User.objects.create_user(
            username="pause-target@example.com",
            email="pause-target@example.com",
            password="testpass123",
        )
        self.client.force_login(self.admin_user)
        self.user_admin = CustomUserAdmin(User, admin.site)

    def _change_url(self):
        meta = self.target_user._meta
        return reverse(f"admin:{meta.app_label}_{meta.model_name}_change", args=[self.target_user.pk])

    def test_user_change_form_renders_execution_controls(self):
        response = self.client.get(self._change_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Execution Control")
        self.assertContains(response, 'name="execution_paused_admin"')
        self.assertContains(response, 'name="execution_pause_reason_admin"')
        self.assertContains(response, f'value="{ExecutionPauseReasonChoices.ADMIN_MANUAL_PAUSE}"')
        self.assertContains(response, f'value="{ExecutionPauseReasonChoices.BILLING_DELINQUENCY}"')

    @patch("api.services.owner_execution_pause.Analytics.track_event")
    def test_save_model_can_pause_user_execution(self, mock_track_event):
        request = self.factory.post(self._change_url())
        request.user = self.admin_user
        form = SimpleNamespace(
            cleaned_data={
                "execution_paused_admin": True,
                "execution_pause_reason_admin": ExecutionPauseReasonChoices.ADMIN_MANUAL_PAUSE,
            }
        )

        self.user_admin.save_model(request, self.target_user, form, change=True)

        self.target_user.billing.refresh_from_db()
        self.assertTrue(self.target_user.billing.execution_paused)
        self.assertEqual(
            self.target_user.billing.execution_pause_reason,
            ExecutionPauseReasonChoices.ADMIN_MANUAL_PAUSE,
        )
        self.assertIsNotNone(self.target_user.billing.execution_paused_at)
        mock_track_event.assert_called_once()
        self.assertEqual(mock_track_event.call_args.kwargs["event"], AnalyticsEvent.ACCOUNT_EXECUTION_PAUSED)

    def test_save_model_can_resume_user_execution(self):
        billing = self.target_user.billing
        billing.execution_paused = True
        billing.execution_pause_reason = ExecutionPauseReasonChoices.BILLING_DELINQUENCY
        billing.execution_paused_at = self.target_user.date_joined
        billing.save(
            update_fields=[
                "execution_paused",
                "execution_pause_reason",
                "execution_paused_at",
            ]
        )
        request = self.factory.post(self._change_url())
        request.user = self.admin_user
        form = SimpleNamespace(
            cleaned_data={
                "execution_paused_admin": False,
                "execution_pause_reason_admin": "",
            }
        )

        self.user_admin.save_model(request, self.target_user, form, change=True)

        billing.refresh_from_db()
        self.assertFalse(billing.execution_paused)
        self.assertEqual(billing.execution_pause_reason, "")
        self.assertIsNone(billing.execution_paused_at)
