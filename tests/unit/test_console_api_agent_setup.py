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
    CommsChannel,
    Organization,
    OrganizationMembership,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentTemplate,
    PublicProfile,
    TaskCredit,
    UserPhoneNumber,
)
from console.phone_utils import PhoneVerificationSendError
from constants.phone_countries import serialize_supported_phone_regions
from console.insight_views import _build_agent_setup_metadata
from constants.plans import PlanNames


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
        self.assertIsNone(payload.get("phone"))
        self.assertIsNotNone(payload.get("pendingPhone"))
        self.assertFalse(payload["pendingPhone"]["isVerified"])

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

    @patch("console.api_views.send_phone_verification", side_effect=PhoneVerificationSendError("raw provider text"))
    def test_phone_add_send_failure_returns_safe_error_and_removes_pending_phone(self, mock_send_verification):
        response = self.client.post(
            reverse("console_user_phone"),
            data=json.dumps({"phone_number": "+16502530000"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "Unable to send verification code. Please try again.")
        self.assertNotIn("raw provider text", response.content.decode())
        self.assertFalse(UserPhoneNumber.objects.filter(user=self.user, phone_number="+16502530000").exists())
        mock_send_verification.assert_called_once()

    @patch("console.api_views.send_phone_verification", side_effect=PhoneVerificationSendError("raw provider text"))
    def test_phone_add_replacement_send_failure_preserves_existing_pending_phone(self, mock_send_verification):
        current_phone = UserPhoneNumber.objects.create(
            user=self.user,
            phone_number="+16502530000",
            is_verified=True,
            is_primary=True,
            verified_at=timezone.now(),
        )
        existing_pending = UserPhoneNumber.objects.create(
            user=self.user,
            phone_number="+16502530002",
            is_verified=False,
            is_primary=False,
            last_verification_attempt=timezone.now() - timedelta(seconds=120),
            verification_sid="old-sid",
        )

        response = self.client.post(
            reverse("console_user_phone"),
            data=json.dumps({"phone_number": "+16502530003"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "Unable to send verification code. Please try again.")
        self.assertTrue(UserPhoneNumber.objects.filter(pk=current_phone.pk).exists())
        existing_pending.refresh_from_db()
        self.assertEqual(existing_pending.phone_number, "+16502530002")
        self.assertEqual(existing_pending.verification_sid, "old-sid")
        self.assertFalse(UserPhoneNumber.objects.filter(user=self.user, phone_number="+16502530003").exists())
        mock_send_verification.assert_called_once()

    @patch("console.api_views.send_phone_verification", side_effect=PhoneVerificationSendError("raw provider text"))
    def test_phone_resend_send_failure_returns_safe_error_without_updating_attempt(self, mock_send_verification):
        last_attempt = timezone.now() - timedelta(seconds=120)
        phone = UserPhoneNumber.objects.create(
            user=self.user,
            phone_number="+16502530000",
            is_verified=False,
            is_primary=True,
            last_verification_attempt=last_attempt,
            verification_sid="old-sid",
        )

        response = self.client.post(
            reverse("console_user_phone_resend"),
            data=json.dumps({}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "Unable to send verification code. Please try again.")
        self.assertNotIn("raw provider text", response.content.decode())
        phone.refresh_from_db()
        self.assertEqual(phone.last_verification_attempt, last_attempt)
        self.assertEqual(phone.verification_sid, "old-sid")
        mock_send_verification.assert_called_once_with(phone)

    @patch("util.sms.start_verification", return_value="sid-replacement")
    def test_phone_add_replacement_keeps_verified_primary(self, mock_start_verification):
        current_phone = UserPhoneNumber.objects.create(
            user=self.user,
            phone_number="+16502530000",
            is_verified=True,
            is_primary=True,
            verified_at=timezone.now(),
        )

        response = self.client.post(
            "/console/api/user/phone/",
            data=json.dumps({"phone_number": "+16502530002"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["phone"]["number"], current_phone.phone_number)
        self.assertEqual(payload["pendingPhone"]["number"], "+16502530002")
        self.assertFalse(payload["pendingPhone"]["isVerified"])
        self.assertTrue(UserPhoneNumber.objects.get(pk=current_phone.pk).is_primary)
        self.assertFalse(UserPhoneNumber.objects.get(phone_number="+16502530002").is_primary)
        mock_start_verification.assert_called_once_with(phone_number="+16502530002")

    def test_phone_cancel_rejects_pending_phone_during_cooldown(self):
        current_phone = UserPhoneNumber.objects.create(
            user=self.user,
            phone_number="+16502530000",
            is_verified=True,
            is_primary=True,
            verified_at=timezone.now(),
        )
        pending_phone = UserPhoneNumber.objects.create(
            user=self.user,
            phone_number="+16502530002",
            is_verified=False,
            is_primary=False,
            last_verification_attempt=timezone.now(),
        )

        response = self.client.post(
            reverse("console_user_phone_cancel"),
            data=json.dumps({}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertTrue(UserPhoneNumber.objects.filter(pk=current_phone.pk).exists())
        self.assertTrue(UserPhoneNumber.objects.filter(pk=pending_phone.pk).exists())

    def test_phone_cancel_removes_only_pending_phone_after_cooldown(self):
        current_phone = UserPhoneNumber.objects.create(
            user=self.user,
            phone_number="+16502530000",
            is_verified=True,
            is_primary=True,
            verified_at=timezone.now(),
        )
        pending_phone = UserPhoneNumber.objects.create(
            user=self.user,
            phone_number="+16502530002",
            is_verified=False,
            is_primary=False,
            last_verification_attempt=timezone.now() - timedelta(seconds=120),
        )

        response = self.client.post(
            reverse("console_user_phone_cancel"),
            data=json.dumps({}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["phone"]["number"], current_phone.phone_number)
        self.assertIsNone(payload["pendingPhone"])
        self.assertTrue(UserPhoneNumber.objects.filter(pk=current_phone.pk).exists())
        self.assertFalse(UserPhoneNumber.objects.filter(pk=pending_phone.pk).exists())

    @patch("util.sms.check_verification", return_value=True)
    def test_phone_verify_replacement_promotes_and_updates_preferred_sms_endpoint(self, _mock_check_verification):
        current_phone = UserPhoneNumber.objects.create(
            user=self.user,
            phone_number="+16502530000",
            is_verified=True,
            is_primary=True,
            verified_at=timezone.now(),
        )
        pending_phone = UserPhoneNumber.objects.create(
            user=self.user,
            phone_number="+16502530002",
            is_verified=False,
            is_primary=False,
            last_verification_attempt=timezone.now() - timedelta(seconds=120),
        )
        old_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.SMS,
            address=current_phone.phone_number,
            owner_agent=None,
        )
        browser = BrowserUseAgent.objects.create(user=self.user, name="Browser Agent")
        agent = PersistentAgent.objects.create(
            user=self.user,
            name="Console Tester",
            charter="Do useful things",
            browser_use_agent=browser,
            preferred_contact_endpoint=old_endpoint,
        )

        response = self.client.post(
            "/console/api/user/phone/verify/",
            data=json.dumps({"verification_code": "123456"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["phone"]["number"], pending_phone.phone_number)
        self.assertIsNone(payload["pendingPhone"])
        self.assertFalse(UserPhoneNumber.objects.filter(pk=current_phone.pk).exists())
        promoted_phone = UserPhoneNumber.objects.get(pk=pending_phone.pk)
        self.assertTrue(promoted_phone.is_primary)
        self.assertTrue(promoted_phone.is_verified)
        agent.refresh_from_db()
        self.assertEqual(agent.preferred_contact_endpoint.channel, CommsChannel.SMS)
        self.assertEqual(agent.preferred_contact_endpoint.address, pending_phone.phone_number)

    @patch("util.sms.check_verification", return_value=True)
    def test_phone_verify_replacement_updates_manageable_org_agent_sms_endpoint(self, _mock_check_verification):
        other_user = User.objects.create_user(
            username="org-agent-owner",
            email="org-owner@example.com",
            password="password123",
        )
        org = Organization.objects.create(name="Acme", slug="acme", created_by=other_user)
        billing = org.billing
        billing.subscription = PlanNames.ORG_TEAM
        billing.purchased_seats = 2
        billing.save(update_fields=["subscription", "purchased_seats"])
        OrganizationMembership.objects.create(
            org=org,
            user=self.user,
            role=OrganizationMembership.OrgRole.MEMBER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        current_phone = UserPhoneNumber.objects.create(
            user=self.user,
            phone_number="+16502530000",
            is_verified=True,
            is_primary=True,
            verified_at=timezone.now(),
        )
        pending_phone = UserPhoneNumber.objects.create(
            user=self.user,
            phone_number="+16502530002",
            is_verified=False,
            is_primary=False,
            last_verification_attempt=timezone.now() - timedelta(seconds=120),
        )
        old_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.SMS,
            address=current_phone.phone_number,
            owner_agent=None,
        )
        browser = BrowserUseAgent.objects.create(user=other_user, name="Org Browser Agent")
        agent = PersistentAgent.objects.create(
            user=other_user,
            organization=org,
            name="Org Console Tester",
            charter="Do useful org things",
            browser_use_agent=browser,
            preferred_contact_endpoint=old_endpoint,
        )

        response = self.client.post(
            "/console/api/user/phone/verify/",
            data=json.dumps({"verification_code": "123456"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        agent.refresh_from_db()
        self.assertEqual(agent.preferred_contact_endpoint.channel, CommsChannel.SMS)
        self.assertEqual(agent.preferred_contact_endpoint.address, pending_phone.phone_number)

    @patch("console.agent_creation.enqueue_interactive_process_agent_events")
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

    def test_agent_sms_disable_clears_preferred_endpoint_without_deleting_numbers(self):
        phone = UserPhoneNumber.objects.create(
            user=self.user,
            phone_number="+16502530000",
            is_verified=True,
            is_primary=True,
            verified_at=timezone.now(),
        )
        browser = BrowserUseAgent.objects.create(user=self.user, name="Browser Agent")
        agent = PersistentAgent.objects.create(
            user=self.user,
            name="Console Tester",
            charter="Do useful things",
            browser_use_agent=browser,
        )
        PersistentAgentCommsEndpoint.objects.create(
            owner_agent=agent,
            channel=CommsChannel.SMS,
            address="+16502530001",
            is_primary=True,
        )
        user_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=None,
            channel=CommsChannel.SMS,
            address=phone.phone_number,
            is_primary=False,
        )
        agent.preferred_contact_endpoint = user_endpoint
        agent.save(update_fields=["preferred_contact_endpoint"])

        response = self.client.post(
            reverse("console_agent_sms_disable", kwargs={"agent_id": agent.id}),
            data=json.dumps({}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["agentSms"]["number"], "+16502530001")
        self.assertEqual(payload["userPhone"]["number"], phone.phone_number)
        self.assertIsNone(payload["preferredContactMethod"])
        self.assertTrue(UserPhoneNumber.objects.filter(pk=phone.pk).exists())
        self.assertTrue(PersistentAgentCommsEndpoint.objects.filter(owner_agent=agent, channel=CommsChannel.SMS).exists())
        agent.refresh_from_db()
        self.assertIsNone(agent.preferred_contact_endpoint_id)

    def test_insights_sms_enabled_requires_preferred_user_sms_endpoint(self):
        phone = UserPhoneNumber.objects.create(
            user=self.user,
            phone_number="+16502530000",
            is_verified=True,
            is_primary=True,
            verified_at=timezone.now(),
        )
        browser = BrowserUseAgent.objects.create(user=self.user, name="Browser Agent")
        agent = PersistentAgent.objects.create(
            user=self.user,
            name="Console Tester",
            charter="Do useful things",
            browser_use_agent=browser,
        )
        PersistentAgentCommsEndpoint.objects.create(
            owner_agent=agent,
            channel=CommsChannel.SMS,
            address="+16502530001",
            is_primary=True,
        )

        request = self.client.get(reverse("console_agent_insights", kwargs={"agent_id": agent.id})).wsgi_request

        metadata = _build_agent_setup_metadata(request, agent, None)

        self.assertFalse(metadata["sms"]["enabled"])
        self.assertEqual(metadata["sms"]["userPhone"]["number"], phone.phone_number)

    def test_profile_and_agent_setup_metadata_include_supported_phone_regions(self):
        expected_countries = serialize_supported_phone_regions()
        browser = BrowserUseAgent.objects.create(user=self.user, name="Browser Agent")
        agent = PersistentAgent.objects.create(
            user=self.user,
            name="Console Tester",
            charter="Do useful things",
            browser_use_agent=browser,
        )

        profile_response = self.client.get(reverse("console_user_profile"))
        request = self.client.get(reverse("console_agent_insights", kwargs={"agent_id": agent.id})).wsgi_request
        metadata = _build_agent_setup_metadata(request, agent, None)

        self.assertEqual(profile_response.status_code, 200)
        self.assertEqual(profile_response.json()["supportedPhoneRegions"], expected_countries)
        self.assertEqual(metadata["sms"]["supportedPhoneRegions"], expected_countries)

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

    def _create_agent(self, *, daily_credit_limit=Decimal("10"), organization=None):
        browser = BrowserUseAgent.objects.create(user=self.user, name="Browser Agent")
        return PersistentAgent.objects.create(
            user=self.user,
            name="Console Tester",
            charter="Do useful things",
            browser_use_agent=browser,
            daily_credit_limit=daily_credit_limit,
            organization=organization,
        )

    def _create_task_credit(self, *, credits=Decimal("100"), credits_used=Decimal("25")):
        now = timezone.now()
        task_credit = TaskCredit.objects.filter(
            user=self.user,
            organization__isnull=True,
            plan=PlanNames.FREE,
            additional_task=False,
            voided=False,
        ).first()
        if task_credit is None:
            return TaskCredit.objects.create(
                user=self.user,
                credits=credits,
                credits_used=credits_used,
                granted_date=now - timedelta(days=1),
                expiration_date=now + timedelta(days=30),
            )

        task_credit.credits = credits
        task_credit.credits_used = credits_used
        task_credit.granted_date = now - timedelta(days=1)
        task_credit.expiration_date = now + timedelta(days=30)
        task_credit.save(update_fields=["credits", "credits_used", "granted_date", "expiration_date"])
        return task_credit

    def _create_org(self):
        org = Organization.objects.create(name="Acme", slug="acme", created_by=self.user)
        billing = org.billing
        billing.subscription = PlanNames.ORG_TEAM
        billing.purchased_seats = 3
        billing.max_extra_tasks = 0
        billing.save(update_fields=["subscription", "purchased_seats", "max_extra_tasks"])
        OrganizationMembership.objects.create(
            org=org,
            user=self.user,
            role=OrganizationMembership.OrgRole.OWNER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        session = self.client.session
        session["context_type"] = "organization"
        session["context_id"] = str(org.id)
        session["context_name"] = org.name
        session.save()
        return org

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
        self.assertEqual(metadata["usageUrl"], "/app/usage")

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

    @patch("console.insight_views.get_agent_daily_credit_state")
    def test_insights_org_month_usage_uses_entitlement_without_credit_rows(self, mock_daily_state):
        org = self._create_org()
        agent = self._create_agent(organization=org)
        mock_daily_state.return_value = {
            "burn_rate_per_hour": Decimal("0"),
            "used": Decimal("0"),
            "hard_limit": Decimal("20"),
            "soft_target": Decimal("10"),
        }

        response = self.client.get(reverse("console_agent_insights", kwargs={"agent_id": agent.id}))

        self.assertEqual(response.status_code, 200)
        usage = next(insight for insight in response.json()["insights"] if insight["insightType"] == "burn_rate")
        month_usage = usage["metadata"]["monthUsage"]
        self.assertEqual(month_usage["used"], 0.0)
        self.assertEqual(month_usage["limit"], 3000.0)
        self.assertEqual(month_usage["percentUsed"], 0.0)
        self.assertFalse(month_usage["unlimited"])

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
