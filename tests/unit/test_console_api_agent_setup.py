import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.urls import reverse
from django.utils import timezone

from api.models import BrowserUseAgent, PersistentAgent, UserPhoneNumber


User = get_user_model()


@tag("batch_console_api")
class AgentSetupApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="agent-owner",
            email="owner@example.com",
            password="password123",
        )
        self.client.force_login(self.user)

    @patch("util.sms.check_verification", return_value=True)
    @patch("util.sms.start_verification", return_value="sid-123")
    def test_phone_add_verify_and_resend(self, mock_start_verification, mock_check_verification):
        add_response = self.client.post(
            "/console/api/user/phone/",
            data=json.dumps({"phone_number": "+16502530000"}),
            content_type="application/json",
        )
        self.assertEqual(add_response.status_code, 200)
        payload = add_response.json()
        self.assertIsNotNone(payload.get("phone"))
        self.assertFalse(payload["phone"]["isVerified"])

        phone = UserPhoneNumber.objects.get(user=self.user, is_primary=True)
        phone.last_verification_attempt = timezone.now() - timedelta(seconds=120)
        phone.save(update_fields=["last_verification_attempt", "updated_at"])

        resend_response = self.client.post(
            "/console/api/user/phone/resend/",
            data=json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(resend_response.status_code, 200)
        self.assertEqual(mock_start_verification.call_count, 2)

        verify_response = self.client.post(
            "/console/api/user/phone/verify/",
            data=json.dumps({"verification_code": "123456"}),
            content_type="application/json",
        )
        self.assertEqual(verify_response.status_code, 200)
        verify_payload = verify_response.json()
        self.assertTrue(verify_payload["phone"]["isVerified"])

        phone.refresh_from_db()
        self.assertTrue(phone.is_verified)

    @patch("console.agent_creation.process_agent_events_task.delay")
    @patch("console.agent_creation.sms.send_sms")
    @patch("console.agent_creation.find_unused_number")
    def test_agent_sms_enable(self, mock_find_unused_number, _mock_send_sms, _mock_task_delay):
        phone = UserPhoneNumber.objects.create(
            user=self.user,
            phone_number="+16502530000",
            is_verified=True,
            is_primary=True,
        )
        browser = BrowserUseAgent.objects.create(user=self.user, name="Browser Agent")
        agent = PersistentAgent.objects.create(
            user=self.user,
            name="Console Tester",
            charter="Do useful things",
            browser_use_agent=browser,
        )

        mock_find_unused_number.return_value = SimpleNamespace(
            phone_number="+16502530001",
            provider="twilio",
        )

        response = self.client.post(
            reverse("console_agent_sms_enable", kwargs={"agent_id": agent.id}),
            data=json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["agentSms"]["number"], "+16502530001")
        self.assertEqual(payload["preferredContactMethod"], "sms")
        self.assertEqual(payload["userPhone"]["number"], phone.phone_number)
