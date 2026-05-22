from django.test import TestCase, override_settings, tag
from unittest.mock import patch

from api.tasks.sms_tasks import sync_twilio_numbers


class OssIntegrationGuardsTests(TestCase):
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
