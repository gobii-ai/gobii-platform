from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag

from api.agent.core.prompt_context import (
    _build_agent_capabilities_sections,
    _latest_inbound_requests_billing_catalog,
    _message_requests_billing_catalog,
    _remaining_user_prompt_budget,
    build_prompt_context,
)
from api.agent.core.promptree import PromptBudgetExceededError
from api.models import (
    BrowserUseAgent,
    CommsAllowlistEntry,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentMessage,
)
from billing.addons import AddonUplift


@tag("batch_promptree")
class AgentCapabilitiesPromptTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user(
            username="cap-user",
            email="cap-user@example.com",
            password="pass1234",
        )
        browser_agent = BrowserUseAgent.objects.create(
            user=cls.user,
            name="Browser Agent",
        )
        cls.agent = PersistentAgent.objects.create(
            user=cls.user,
            name="Capability Agent",
            browser_use_agent=browser_agent,
        )

    def _build_prompt_text_for_owner(
        self,
        *,
        email: str = "owner@example.com",
        first_name: str = "",
        last_name: str = "",
    ) -> str:
        User = get_user_model()
        suffix = User.objects.count()
        user = User.objects.create_user(
            username=f"identity-owner-{suffix}",
            email=email,
            password="pass1234",
            first_name=first_name,
            last_name=last_name,
        )
        browser_agent = BrowserUseAgent.objects.create(
            user=user,
            name=f"Identity Browser Agent {suffix}",
        )
        agent = PersistentAgent.objects.create(
            user=user,
            name=f"Identity Agent {suffix}",
            browser_use_agent=browser_agent,
        )

        with patch("api.agent.core.prompt_context.ensure_steps_compacted"), patch(
            "api.agent.core.prompt_context.ensure_comms_compacted"
        ):
            context, _, _ = build_prompt_context(agent)
        return "\n".join(message["content"] for message in context)

    def test_owner_identity_prompt_uses_profile_first_name(self):
        contents = self._build_prompt_text_for_owner(
            email="mattworkpro@example.com",
            first_name="Ada",
            last_name="Lovelace",
        )

        self.assertIn("The owner's name is Ada.", contents)
        self.assertIn("Use it occasionally when natural", contents)
        self.assertNotIn("profile name is not set", contents)
        self.assertNotIn("owner's name is unknown", contents)

    def test_remaining_user_prompt_budget_accounts_for_system_prompt(self):
        self.assertEqual(_remaining_user_prompt_budget(100, 35), 65)

    def test_system_prompt_cannot_exhaust_total_prompt_budget(self):
        with self.assertRaises(PromptBudgetExceededError) as raised:
            _remaining_user_prompt_budget(100, 100)

        self.assertEqual(raised.exception.budget, 100)
        self.assertEqual(raised.exception.required, 100)

    def test_owner_identity_prompt_handles_braces_in_profile_first_name(self):
        contents = self._build_prompt_text_for_owner(
            email="braces@example.com",
            first_name="{Ada}",
        )

        self.assertIn("The owner's name is {Ada}.", contents)
        self.assertIn("Use it occasionally when natural", contents)
        self.assertNotIn("owner's name is unknown", contents)

    def test_owner_identity_prompt_marks_missing_profile_name_unknown(self):
        for email in [
            "jane@example.com",
            "jane.doe@example.com",
            "jane_doe@example.com",
            "jane-doe@example.com",
            "mattworkpro@example.com",
            "jane123@example.com",
            "support@example.com",
            "hello+test@example.com",
            "",
        ]:
            with self.subTest(email=email):
                contents = self._build_prompt_text_for_owner(email=email)

                self.assertIn("The owner's name is unknown;", contents)
                self.assertIn("do not infer it from an email or username", contents)

    def test_owner_identity_prompt_does_not_use_last_name_as_call_name(self):
        contents = self._build_prompt_text_for_owner(
            email="jane.doe@example.com",
            last_name="Lovelace",
        )

        self.assertIn("The owner's name is unknown;", contents)
        self.assertIn("do not infer it from an email or username", contents)
        self.assertNotIn("The owner's name is Lovelace", contents)

    @override_settings(PUBLIC_SITE_URL="https://app.test")
    @patch("api.agent.core.prompt_context.DedicatedProxyService.allocated_count", return_value=2)
    @patch("api.agent.core.prompt_context.AddonEntitlementService.get_uplift")
    @patch("api.agent.core.prompt_context.get_user_max_contacts_per_agent", return_value=30)
    @patch("api.agent.core.prompt_context.get_owner_plan")
    def test_capabilities_block_includes_plan_addons_and_links(
        self,
        plan_mock,
        _contact_cap_mock,
        uplift_mock,
        _dedicated_mock,
    ):
        plan_mock.return_value = {
            "id": "startup",
            "name": "Pro",
            "max_contacts_per_agent": 20,
        }
        uplift_mock.return_value = AddonUplift(
            task_credits=2000,
            contact_cap=10,
            browser_task_daily=5,
            advanced_captcha_resolution=1,
        )

        CommsAllowlistEntry.objects.create(
            agent=self.agent,
            channel="email",
            address="a@example.com",
            is_active=True,
            allow_inbound=True,
            allow_outbound=True,
        )

        with patch(
            "api.agent.core.prompt_context.AddonEntitlementService.get_price_options",
            return_value=[],
        ):
            sections = _build_agent_capabilities_sections(
                self.agent,
                include_billing_catalog=True,
            )
        plan_info = sections.get("plan_info", "")
        agent_addons = sections.get("agent_addons", "")
        agent_settings = sections.get("agent_settings", "")
        email_settings = sections.get("agent_email_settings", "")

        self.assertIn("Plan: Pro", plan_info)
        self.assertIn(
            "Add-ons: +2000 credits; +10 contacts; +5 browser tasks/day; Advanced CAPTCHA resolution enabled.",
            plan_info,
        )
        self.assertIn("Per-agent contact cap: 30 (20 included in plan + add-ons", plan_info)
        self.assertIn("Contact usage: 1/30", plan_info)
        self.assertIn("Dedicated IPs: 2", plan_info)
        self.assertIn("/app/billing", plan_info)
        self.assertNotIn(f"/console/agents/{self.agent.id}/", plan_info)

        self.assertIn("Available plans:", agent_addons)
        self.assertIn("Pro: $50/mo, 500 credits/mo, 20 contacts/agent", agent_addons)
        self.assertIn("Available add-ons:", agent_addons)
        self.assertIn("Current eligibility and checkout price:", agent_addons)
        self.assertIn("/app/billing", agent_addons)

        self.assertIn(f"/app/agents/{self.agent.id}/settings", agent_settings)
        self.assertIn(f"/app/agents/{self.agent.id}/secrets", agent_settings)
        self.assertIn(f"/app/agents/{self.agent.id}/email", agent_settings)
        self.assertEqual(email_settings, "")

    def test_billing_catalog_is_only_requested_for_relevant_messages(self):
        self.assertTrue(_message_requests_billing_catalog("What plans are available?"))
        self.assertTrue(_message_requests_billing_catalog("Can I buy a browser task add-on?"))
        self.assertTrue(_message_requests_billing_catalog("How much does an upgrade cost?"))
        self.assertFalse(_message_requests_billing_catalog("Plan a vendor research project for me"))
        self.assertFalse(_message_requests_billing_catalog("Summarize today's alerts"))

    def test_answered_billing_message_does_not_leak_catalog_into_later_wakes(self):
        agent_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel="email",
            address="capability-agent@example.com",
            is_primary=True,
        )
        user_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel="email",
            address="billing-user@example.com",
        )
        PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            is_outbound=False,
            from_endpoint=user_endpoint,
            to_endpoint=agent_endpoint,
            body="What plans and add-ons are available?",
        )
        self.assertTrue(_latest_inbound_requests_billing_catalog(self.agent))

        PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            is_outbound=True,
            from_endpoint=agent_endpoint,
            to_endpoint=user_endpoint,
            body="Here are the current options.",
        )
        self.assertFalse(_latest_inbound_requests_billing_catalog(self.agent))

    def test_pending_nonconfiguring_request_gets_system_priority_guard(self):
        agent_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel="email",
            address="capability-agent-guard@example.com",
            is_primary=True,
        )
        contact_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel="email",
            address="readonly-contact@example.com",
        )
        CommsAllowlistEntry.objects.create(
            agent=self.agent,
            channel="email",
            address=contact_endpoint.address,
            is_active=True,
            allow_inbound=True,
            allow_outbound=True,
            can_configure=False,
        )
        PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            is_outbound=False,
            from_endpoint=contact_endpoint,
            to_endpoint=agent_endpoint,
            body="Change your weekly briefing format.",
        )

        with patch("api.agent.core.prompt_context.ensure_steps_compacted"), patch(
            "api.agent.core.prompt_context.ensure_comms_compacted"
        ):
            context, _, _ = build_prompt_context(self.agent)

        self.assertIn("## Active requester authority", context[0]["content"])
        self.assertIn("do not call `sqlite_batch` on `__agent_config`", context[0]["content"])
        self.assertIn("do not infer owner status", context[0]["content"])

    @override_settings(PUBLIC_SITE_URL="https://app.test")
    @patch("api.agent.core.prompt_context.DedicatedProxyService.allocated_count", return_value=0)
    @patch("api.agent.core.prompt_context.AddonEntitlementService.get_uplift")
    @patch("api.agent.core.prompt_context.get_user_max_contacts_per_agent", return_value=100)
    @patch("api.agent.core.prompt_context.get_owner_plan")
    def test_capabilities_block_uses_effective_contact_cap(
        self,
        plan_mock,
        _contact_cap_mock,
        uplift_mock,
        _dedicated_mock,
    ):
        plan_mock.return_value = {
            "id": "free",
            "name": "Free",
            "max_contacts_per_agent": 3,
        }
        uplift_mock.return_value = AddonUplift()

        sections = _build_agent_capabilities_sections(self.agent)
        plan_info = sections.get("plan_info", "")

        self.assertIn("Per-agent contact cap: 100 (effective account limit).", plan_info)
