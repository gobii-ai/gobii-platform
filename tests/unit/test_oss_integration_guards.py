from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag
from django.urls import reverse
from unittest.mock import patch

from api.tasks.sms_tasks import sync_twilio_numbers


class OssIntegrationGuardsTests(TestCase):
    @tag('oss_readiness_batch')
    @override_settings(STRIPE_ENABLED=False, STRIPE_DISABLED_REASON="disabled for test")
    def test_billing_view_returns_404_when_stripe_disabled(self):
        User = get_user_model()
        user = User.objects.create_user(
            email="oss-tester@example.com",
            username="oss-tester",
            password="password123",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("billing"))

        self.assertEqual(response.status_code, 404)

    @tag('oss_readiness_batch')
    @override_settings(
        TWILIO_ENABLED=False,
        TWILIO_DISABLED_REASON="disabled for test",
        TWILIO_ACCOUNT_SID="",
        TWILIO_AUTH_TOKEN="",
        TWILIO_MESSAGING_SERVICE_SID="",
    )
    def test_sync_twilio_numbers_skips_when_disabled(self):
        with patch("api.tasks.sms_tasks.Client") as mock_client:
            sync_twilio_numbers()

        mock_client.assert_not_called()
