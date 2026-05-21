import json
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.urls import reverse
from django.utils import timezone

from api.models import (
    BrowserUseAgent,
    PersistentAgent,
    PersistentAgentTemplate,
    PublicProfile,
    TaskCredit,
    UserPhoneNumber,
)


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
        self.personal_access_patch = patch(
            "console.agent_chat.access.can_user_use_personal_agents_and_api",
            return_value=True,
        )
        self.personal_chat_access_patch = patch(
            "console.agent_chat.access.can_user_access_personal_agent_chat",
            return_value=True,
        )
        self.personal_access_patch.start()
        self.personal_chat_access_patch.start()
        self.addCleanup(self.personal_access_patch.stop)
        self.addCleanup(self.personal_chat_access_patch.stop)

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

    @patch("console.agent_creation.find_unused_number")
    def test_agent_sms_enable_rejects_admin_disabled_agent(self, mock_find_unused_number):
        UserPhoneNumber.objects.create(
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
            sms_disabled=True,
        )

        response = self.client.post(
            reverse("console_agent_sms_enable", kwargs={"agent_id": agent.id}),
            data=json.dumps({}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "SMS has been disabled for this agent.")
        mock_find_unused_number.assert_not_called()

    def _create_agent(self, *, daily_credit_limit=Decimal("10")):
        browser = BrowserUseAgent.objects.create(user=self.user, name="Browser Agent")
        return PersistentAgent.objects.create(
            user=self.user,
            name="Console Tester",
            charter="Do useful things",
            browser_use_agent=browser,
            daily_credit_limit=daily_credit_limit,
        )

    def _create_task_credit(self, *, credits=Decimal("100"), credits_used=Decimal("25")):
        now = timezone.now()
        return TaskCredit.objects.create(
            user=self.user,
            credits=credits,
            credits_used=credits_used,
            granted_date=now - timedelta(days=1),
            expiration_date=now + timedelta(days=30),
        )

    @patch("console.insight_views.get_agent_daily_credit_state")
    def test_insights_usage_metadata_includes_today_and_month_usage(self, mock_daily_state):
        agent = self._create_agent()
        self._create_task_credit()
        mock_daily_state.return_value = {
            "burn_rate_per_hour": Decimal("1.2"),
            "used": Decimal("3"),
            "hard_limit": Decimal("20"),
            "soft_target": Decimal("10"),
        }

        response = self.client.get(reverse("console_agent_insights", kwargs={"agent_id": agent.id}))

        self.assertEqual(response.status_code, 200)
        insights = response.json()["insights"]
        usage = next(insight for insight in insights if insight["insightType"] == "burn_rate")
        metadata = usage["metadata"]
        self.assertEqual(metadata["todayUsage"]["used"], 3.0)
        self.assertEqual(metadata["todayUsage"]["limit"], 20.0)
        self.assertEqual(metadata["todayUsage"]["percentUsed"], 15.0)
        self.assertFalse(metadata["todayUsage"]["unlimited"])
        self.assertEqual(metadata["monthUsage"]["used"], 25.0)
        self.assertEqual(metadata["monthUsage"]["limit"], 100.0)
        self.assertEqual(metadata["monthUsage"]["percentUsed"], 25.0)
        self.assertEqual(metadata["usageUrl"], reverse("usage"))

    @patch("console.insight_views.get_agent_daily_credit_state")
    def test_insights_usage_metadata_handles_unlimited_daily_credits(self, mock_daily_state):
        agent = self._create_agent(daily_credit_limit=Decimal("0"))
        self._create_task_credit(credits=Decimal("50"), credits_used=Decimal("10"))
        mock_daily_state.return_value = {
            "burn_rate_per_hour": Decimal("0"),
            "used": Decimal("7.25"),
            "hard_limit": None,
            "soft_target": None,
        }

        response = self.client.get(reverse("console_agent_insights", kwargs={"agent_id": agent.id}))

        self.assertEqual(response.status_code, 200)
        usage = next(insight for insight in response.json()["insights"] if insight["insightType"] == "burn_rate")
        today_usage = usage["metadata"]["todayUsage"]
        self.assertEqual(today_usage["used"], 7.25)
        self.assertIsNone(today_usage["limit"])
        self.assertIsNone(today_usage["percentUsed"])
        self.assertTrue(today_usage["unlimited"])

    @patch("console.api_views.generate_handle_suggestion", return_value="shared-agent")
    def test_agent_template_share_info_returns_suggested_handle(self, _mock_suggestion):
        agent = self._create_agent()

        response = self.client.get(reverse("console_agent_template_clone", kwargs={"agent_id": agent.id}))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["canShare"])
        self.assertEqual(payload["suggestedHandle"], "shared-agent")
        self.assertIsNone(payload["templateUrl"])

    def test_agent_template_share_info_returns_existing_template(self):
        agent = self._create_agent()
        profile = PublicProfile.objects.create(user=self.user, handle="owner-handle")
        template = PersistentAgentTemplate.objects.create(
            code="console-tester-template",
            public_profile=profile,
            slug="console-tester",
            source_agent=agent,
            created_by=self.user,
            display_name="Console Tester",
            tagline="Useful work",
            description="A public template.",
            charter="Do useful things.",
            category="Operations",
        )

        response = self.client.get(reverse("console_agent_template_clone", kwargs={"agent_id": agent.id}))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["canShare"])
        self.assertEqual(payload["publicProfileHandle"], profile.handle)
        self.assertIsNone(payload["suggestedHandle"])
        self.assertEqual(payload["templateSlug"], template.slug)
        self.assertEqual(payload["displayName"], template.display_name)
        self.assertIn("/library/", payload["templateUrl"])
