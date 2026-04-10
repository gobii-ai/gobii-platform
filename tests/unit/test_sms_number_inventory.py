from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings, tag
from django.urls import reverse
from django.utils import timezone

from django.contrib.auth import get_user_model

from api.models import BrowserUseAgent, CommsChannel, PersistentAgent, PersistentAgentCommsEndpoint, SmsNumber
from api.services.sms_number_inventory import release_sms_number, retire_sms_number
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
        TWILIO_ENABLED=True,
        TWILIO_ACCOUNT_SID="AC00000000000000000000000000000000",
        TWILIO_AUTH_TOKEN="test-token",
        TWILIO_MESSAGING_SERVICE_SID="MG00000000000000000000000000000000",
    )
    @patch("api.services.sms_number_inventory.Client")
    def test_release_sms_number_detaches_endpoint_retires_locally_and_releases_in_twilio(self, mock_client_cls):
        user = get_user_model().objects.create_user(
            email="sms-release-owner@example.com",
            username="sms-release-owner",
            password="password123",
        )
        browser_agent = BrowserUseAgent.objects.create(user=user, name="SMS Release Browser")
        agent = PersistentAgent.objects.create(
            user=user,
            name="SMS Release Agent",
            charter="c",
            browser_use_agent=browser_agent,
        )
        sms_number = SmsNumber.objects.create(
            sid="PN000000000000000000000000000015",
            phone_number="+15550000015",
            country="US",
            messaging_service_sid="MG00000000000000000000000000000000",
        )
        endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=agent,
            channel=CommsChannel.SMS,
            address=sms_number.phone_number,
        )

        mock_client = Mock()
        mock_service_phone = Mock()
        mock_service_phone.delete.return_value = True
        mock_service = Mock()
        mock_service.phone_numbers.return_value = mock_service_phone
        mock_client.messaging = Mock()
        mock_client.messaging.v1 = Mock()
        mock_client.messaging.v1.services.return_value = mock_service
        mock_incoming_phone = Mock()
        mock_incoming_phone.delete.return_value = True
        mock_client.incoming_phone_numbers.return_value = mock_incoming_phone
        mock_client_cls.return_value = mock_client

        result = release_sms_number(sms_number)

        sms_number.refresh_from_db()
        endpoint.refresh_from_db()
        self.assertTrue(result.succeeded)
        self.assertEqual(result.detached_endpoint_count, 1)
        self.assertTrue(result.retired_locally)
        self.assertTrue(result.twilio_released)
        self.assertIsNone(endpoint.owner_agent_id)
        self.assertFalse(sms_number.is_active)
        self.assertIsNotNone(sms_number.released_at)
        mock_client.messaging.v1.services.assert_called_once_with(sms_number.messaging_service_sid)
        mock_service.phone_numbers.assert_called_once_with(sms_number.sid)
        mock_client.incoming_phone_numbers.assert_called_once_with(sms_number.sid)

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

    def test_sms_number_admin_search_does_not_crash(self):
        admin_user = get_user_model().objects.create_superuser(
            email="sms-admin@example.com",
            username="sms-admin",
            password="password123",
        )
        self.client.force_login(admin_user)
        SmsNumber.objects.create(
            sid="PN000000000000000000000000000010",
            phone_number="+12075550123",
            country="US",
        )

        response = self.client.get(
            reverse("admin:api_smsnumber_changelist"),
            {"q": "207"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "207")

    def test_sms_number_admin_summary_excludes_released_numbers_from_inventory_counts(self):
        admin_user = get_user_model().objects.create_superuser(
            email="sms-summary-admin@example.com",
            username="sms-summary-admin",
            password="password123",
        )
        self.client.force_login(admin_user)
        in_use_number = SmsNumber.objects.create(
            sid="PN000000000000000000000000000011",
            phone_number="+15550000011",
            country="US",
        )
        SmsNumber.objects.create(
            sid="PN000000000000000000000000000012",
            phone_number="+15550000012",
            country="US",
        )
        released_with_history = SmsNumber.objects.create(
            sid="PN000000000000000000000000000013",
            phone_number="+15550000013",
            country="US",
            is_active=False,
            released_at=timezone.now(),
        )
        SmsNumber.objects.create(
            sid="PN000000000000000000000000000014",
            phone_number="+15550000014",
            country="US",
            is_active=False,
            released_at=timezone.now(),
        )

        PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.SMS,
            address=in_use_number.phone_number,
        )
        PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.SMS,
            address=released_with_history.phone_number,
        )

        response = self.client.get(reverse("admin:api_smsnumber_changelist"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["in_use_count"], 1)
        self.assertEqual(response.context["inventory_count"], 2)
        self.assertEqual(response.context["released_count"], 2)
        self.assertContains(response, "1 of 2 SMS Numbers in Use")
        self.assertContains(response, "2 SMS Numbers Released")

    @override_settings(
        TWILIO_ENABLED=True,
        TWILIO_ACCOUNT_SID="AC00000000000000000000000000000000",
        TWILIO_AUTH_TOKEN="test-token",
        TWILIO_MESSAGING_SERVICE_SID="MG00000000000000000000000000000000",
    )
    @patch("api.services.sms_number_inventory.Client")
    def test_sms_number_admin_release_view_releases_inventory_numbers_and_skips_unknown_numbers(
        self,
        mock_client_cls,
    ):
        admin_user = get_user_model().objects.create_superuser(
            email="sms-release-admin@example.com",
            username="sms-release-admin",
            password="password123",
        )
        self.client.force_login(admin_user)
        user = get_user_model().objects.create_user(
            email="sms-release-view-owner@example.com",
            username="sms-release-view-owner",
            password="password123",
        )
        browser_agent = BrowserUseAgent.objects.create(user=user, name="SMS Release View Browser")
        agent = PersistentAgent.objects.create(
            user=user,
            name="SMS Release View Agent",
            charter="c",
            browser_use_agent=browser_agent,
        )
        sms_number = SmsNumber.objects.create(
            sid="PN000000000000000000000000000016",
            phone_number="+15550000016",
            country="US",
            messaging_service_sid="MG00000000000000000000000000000000",
        )
        pool_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=agent,
            channel=CommsChannel.SMS,
            address=sms_number.phone_number,
        )
        customer_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=agent,
            channel=CommsChannel.SMS,
            address="+15550000017",
        )

        mock_client = Mock()
        mock_service_phone = Mock()
        mock_service_phone.delete.return_value = True
        mock_service = Mock()
        mock_service.phone_numbers.return_value = mock_service_phone
        mock_client.messaging = Mock()
        mock_client.messaging.v1 = Mock()
        mock_client.messaging.v1.services.return_value = mock_service
        mock_incoming_phone = Mock()
        mock_incoming_phone.delete.return_value = True
        mock_client.incoming_phone_numbers.return_value = mock_incoming_phone
        mock_client_cls.return_value = mock_client

        response = self.client.post(
            reverse("admin:smsnumber_release"),
            {"phone_numbers": f"{sms_number.phone_number}\n{customer_endpoint.address}"},
            follow=True,
        )

        sms_number.refresh_from_db()
        pool_endpoint.refresh_from_db()
        customer_endpoint.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(pool_endpoint.owner_agent_id)
        self.assertEqual(customer_endpoint.owner_agent_id, agent.id)
        self.assertFalse(sms_number.is_active)
        self.assertIsNotNone(sms_number.released_at)
        self.assertContains(response, "Released 1 SMS number(s) in Twilio")
        self.assertContains(response, "Detached 1 SMS endpoint(s) from agents before release.")
        self.assertContains(response, "Skipped 1 number(s) not found in SMS inventory")
