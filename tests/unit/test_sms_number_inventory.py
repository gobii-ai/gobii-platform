from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings, tag
from django.utils import timezone

from django.contrib.auth import get_user_model

from api.models import BrowserUseAgent, CommsChannel, PersistentAgent, PersistentAgentCommsEndpoint, SmsNumber
from api.services.sms_number_inventory import retire_sms_number
from api.tasks.sms_tasks import sync_twilio_numbers
from util.sms import find_unused_number


@tag("batch_sms")
class SmsNumberInventoryTests(TestCase):
    def test_find_unused_number_skips_retired_inactive_disabled_and_in_use_numbers(self):
        SmsNumber.objects.create(
            sid="PN000000000000000000000000000001",
            phone_number="+15550000001",
            country="US",
            is_active=False,
        )
        SmsNumber.objects.create(
            sid="PN000000000000000000000000000002",
            phone_number="+15550000002",
            country="US",
            is_active=False,
            released_at=timezone.now(),
        )
        SmsNumber.objects.create(
            sid="PN000000000000000000000000000003",
            phone_number="+15550000003",
            country="US",
            is_sms_enabled=False,
        )
        in_use = SmsNumber.objects.create(
            sid="PN000000000000000000000000000004",
            phone_number="+15550000004",
            country="US",
        )
        available = SmsNumber.objects.create(
            sid="PN000000000000000000000000000005",
            phone_number="+15550000005",
            country="US",
        )

        PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.SMS,
            address=in_use.phone_number,
        )

        selected = find_unused_number()

        self.assertEqual(selected.pk, available.pk)

    def test_retire_sms_number_marks_number_released(self):
        sms_number = SmsNumber.objects.create(
            sid="PN000000000000000000000000000006",
            phone_number="+15550000006",
            country="US",
        )

        changed = retire_sms_number(sms_number)

        sms_number.refresh_from_db()
        self.assertTrue(changed)
        self.assertFalse(sms_number.is_active)
        self.assertIsNotNone(sms_number.released_at)

    def test_retire_sms_number_rejects_numbers_still_in_use(self):
        user = get_user_model().objects.create_user(
            email="sms-owner@example.com",
            username="sms-owner",
            password="password123",
        )
        browser_agent = BrowserUseAgent.objects.create(user=user, name="SMS Owner Browser")
        agent = PersistentAgent.objects.create(
            user=user,
            name="SMS Owner Agent",
            charter="c",
            browser_use_agent=browser_agent,
        )
        sms_number = SmsNumber.objects.create(
            sid="PN000000000000000000000000000007",
            phone_number="+15550000007",
            country="US",
        )
        PersistentAgentCommsEndpoint.objects.create(
            owner_agent=agent,
            channel=CommsChannel.SMS,
            address=sms_number.phone_number,
        )

        with self.assertRaises(ValidationError):
            retire_sms_number(sms_number)

        sms_number.refresh_from_db()
        self.assertTrue(sms_number.is_active)
        self.assertIsNone(sms_number.released_at)

    def test_retire_sms_number_allows_historical_unowned_endpoint(self):
        sms_number = SmsNumber.objects.create(
            sid="PN000000000000000000000000000009",
            phone_number="+15550000009",
            country="US",
        )
        PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.SMS,
            address=sms_number.phone_number,
        )

        changed = retire_sms_number(sms_number)

        sms_number.refresh_from_db()
        self.assertTrue(changed)
        self.assertFalse(sms_number.is_active)
        self.assertIsNotNone(sms_number.released_at)

    @override_settings(
        TWILIO_ACCOUNT_SID="AC00000000000000000000000000000000",
        TWILIO_AUTH_TOKEN="test-token",
        TWILIO_MESSAGING_SERVICE_SID="MG00000000000000000000000000000000",
    )
    @patch("api.tasks.sms_tasks.twilio_status", return_value=SimpleNamespace(enabled=True, reason=None))
    @patch("api.tasks.sms_tasks.Client")
    def test_sync_twilio_numbers_preserves_locally_retired_number(self, mock_client_cls, _mock_status):
        released_at = timezone.now()
        sms_number = SmsNumber.objects.create(
            sid="PN000000000000000000000000000008",
            phone_number="+15550000008",
            country="US",
            is_active=False,
            released_at=released_at,
            friendly_name="Original Name",
        )

        remote_phone_number = SimpleNamespace(
            sid=sms_number.sid,
            phone_number=sms_number.phone_number,
            friendly_name="Twilio Name",
            country_code="US",
            region="CA",
            capabilities={"SMS": True, "MMS": True},
        )

        mock_client = Mock()
        mock_phone_numbers = Mock()
        mock_phone_numbers.list.return_value = [remote_phone_number]
        mock_service = Mock(phone_numbers=mock_phone_numbers)
        mock_client.messaging.services.return_value = mock_service
        mock_client_cls.return_value = mock_client

        sync_twilio_numbers()

        sms_number.refresh_from_db()
        self.assertFalse(sms_number.is_active)
        self.assertEqual(sms_number.released_at, released_at)
        self.assertEqual(sms_number.friendly_name, "Twilio Name")
