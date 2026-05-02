from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag

from api.agent.comms.outbound_delivery import deliver_agent_sms
from api.models import (
    BrowserUseAgent,
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentMessage,
    UserPhoneNumber,
)
from util import sms


User = get_user_model()


@tag("batch_sms")
@override_settings(
    TWILIO_ENABLED=True,
    TWILIO_ACCOUNT_SID="AC00000000000000000000000000000000",
    TWILIO_AUTH_TOKEN="test-token",
    TWILIO_MESSAGING_SERVICE_SID="MG00000000000000000000000000000000",
)
class TwilioRiskCheckTests(TestCase):
    def _mock_twilio_client(self, mock_client_cls):
        client = Mock()
        message = Mock()
        message.sid = "SM123"
        client.messages.create.return_value = message
        mock_client_cls.return_value = client
        return client

    @patch("util.sms.Client")
    def test_send_sms_disables_risk_check_for_verified_us_owner_number(self, mock_client_cls):
        client = self._mock_twilio_client(mock_client_cls)
        user = User.objects.create_user(username="owner", email="owner@example.com")
        UserPhoneNumber.objects.create(
            user=user,
            phone_number="+14155552671",
            is_verified=True,
        )

        result = sms.send_sms(
            to_number="+14155552671",
            from_number="+12025550123",
            body="Hello",
            owner_user=user,
        )

        self.assertEqual(result, "SM123")
        kwargs = client.messages.create.call_args.kwargs
        self.assertEqual(kwargs["messaging_service_sid"], "MG00000000000000000000000000000000")
        self.assertEqual(kwargs["risk_check"], sms.TWILIO_RISK_CHECK_DISABLE)

    @patch("config.settings.TWILIO_ACCOUNT_SID", "")
    @patch("config.settings.TWILIO_AUTH_TOKEN", "")
    @patch("util.sms.Client")
    def test_send_sms_uses_django_settings_overrides_for_twilio_credentials(self, mock_client_cls):
        client = self._mock_twilio_client(mock_client_cls)
        user = User.objects.create_user(username="owner", email="owner@example.com")
        UserPhoneNumber.objects.create(
            user=user,
            phone_number="+14155552671",
            is_verified=True,
        )

        result = sms.send_sms(
            to_number="+14155552671",
            from_number="+12025550123",
            body="Hello",
            owner_user=user,
        )

        self.assertEqual(result, "SM123")
        kwargs = client.messages.create.call_args.kwargs
        self.assertEqual(kwargs["risk_check"], sms.TWILIO_RISK_CHECK_DISABLE)

    @patch("util.sms.Client")
    def test_send_sms_disables_risk_check_for_formatted_verified_us_owner_number(self, mock_client_cls):
        client = self._mock_twilio_client(mock_client_cls)
        user = User.objects.create_user(username="owner", email="owner@example.com")
        UserPhoneNumber.objects.create(
            user=user,
            phone_number="+14155552671",
            is_verified=True,
        )

        sms.send_sms(
            to_number="+1 (415) 555-2671",
            from_number="+12025550123",
            body="Hello",
            owner_user=user,
        )

        kwargs = client.messages.create.call_args.kwargs
        self.assertEqual(kwargs["risk_check"], sms.TWILIO_RISK_CHECK_DISABLE)

    @patch("util.sms.Client")
    def test_send_sms_keeps_risk_check_default_for_unregistered_us_number(self, mock_client_cls):
        client = self._mock_twilio_client(mock_client_cls)
        user = User.objects.create_user(username="owner", email="owner@example.com")

        sms.send_sms(
            to_number="+14155552671",
            from_number="+12025550123",
            body="Hello",
            owner_user=user,
        )

        kwargs = client.messages.create.call_args.kwargs
        self.assertNotIn("risk_check", kwargs)

    @patch("util.sms.Client")
    def test_send_sms_keeps_risk_check_default_for_non_us_owner_number(self, mock_client_cls):
        client = self._mock_twilio_client(mock_client_cls)
        user = User.objects.create_user(username="owner", email="owner@example.com")
        UserPhoneNumber.objects.create(
            user=user,
            phone_number="+442071838750",
            is_verified=True,
        )

        sms.send_sms(
            to_number="+442071838750",
            from_number="+12025550123",
            body="Hello",
            owner_user=user,
        )

        kwargs = client.messages.create.call_args.kwargs
        self.assertNotIn("risk_check", kwargs)

    @patch("util.sms.Client")
    def test_send_sms_normalizes_body_before_twilio_send(self, mock_client_cls):
        client = self._mock_twilio_client(mock_client_cls)

        result = sms.send_sms(
            to_number="+14155552671",
            from_number="+12025550123",
            body="Quick update — done 😊",
        )

        self.assertEqual(result, "SM123")
        kwargs = client.messages.create.call_args.kwargs
        self.assertEqual(kwargs["body"], "Quick update - done :)")

    @patch("api.agent.comms.outbound_delivery.Analytics.track_event")
    @patch("api.agent.comms.outbound_delivery.sms.send_sms", return_value="SM123")
    def test_deliver_agent_sms_passes_owner_user_to_twilio_send(
        self,
        mock_send_sms,
        _mock_track_event,
    ):
        user = User.objects.create_user(username="owner", email="owner@example.com")
        with patch.object(BrowserUseAgent, "select_random_proxy", return_value=None):
            browser_agent = BrowserUseAgent.objects.create(user=user, name="Browser")
        agent = PersistentAgent.objects.create(
            user=user,
            name="SMS Agent",
            charter="Test charter",
            browser_use_agent=browser_agent,
        )
        from_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=agent,
            channel=CommsChannel.SMS,
            address="+12025550123",
            is_primary=True,
        )
        to_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.SMS,
            address="+14155552671",
        )
        message = PersistentAgentMessage.objects.create(
            owner_agent=agent,
            from_endpoint=from_endpoint,
            to_endpoint=to_endpoint,
            is_outbound=True,
            body="Hello",
            raw_payload={},
        )

        deliver_agent_sms(message)

        mock_send_sms.assert_called_once()
        self.assertEqual(mock_send_sms.call_args.kwargs["owner_user"], user)
