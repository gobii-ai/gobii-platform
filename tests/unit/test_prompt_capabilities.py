from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag

from api.agent.core.prompt_context import (
    _build_agent_capabilities_sections,
    build_prompt_context,
)
from api.models import BrowserUseAgent, CommsAllowlistEntry, PersistentAgent
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
        self.assertIn("Use their name occasionally to build rapport", contents)
        self.assertNotIn("profile name is not set", contents)
        self.assertNotIn("owner's name is unknown", contents)

    def test_owner_identity_prompt_handles_braces_in_profile_first_name(self):
        contents = self._build_prompt_text_for_owner(
            email="braces@example.com",
            first_name="{Ada}",
        )

        self.assertIn("The owner's name is {Ada}.", contents)
        self.assertIn("Hey {Ada}, found it!", contents)
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

                self.assertIn("The owner's name is unknown.", contents)
                self.assertIn("Do not infer a first name, last name, or preferred form of address", contents)

    def test_owner_identity_prompt_does_not_use_last_name_as_call_name(self):
        contents = self._build_prompt_text_for_owner(
            email="jane.doe@example.com",
            last_name="Lovelace",
        )

        self.assertIn("The owner's name is unknown.", contents)
        self.assertIn("Do not infer a first name, last name, or preferred form of address", contents)
        self.assertNotIn("The owner's name is Lovelace", contents)

    @override_settings(PUBLIC_SITE_URL="https://app.test")
    @patch("api.agent.core.prompt_context.DedicatedProxyService.allocated_count", return_value=2)
    @patch("api.agent.core.prompt_context.AddonEntitlementService.get_uplift")
    @patch("api.agent.core.prompt_context.get_owner_plan")
    def test_capabilities_block_includes_plan_addons_and_links(
        self,
        plan_mock,
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

        sections = _build_agent_capabilities_sections(self.agent)
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
        self.assertIn("Dedicated IPs purchased: 2", plan_info)
        self.assertIn("/app/billing", plan_info)
        self.assertNotIn(f"/console/agents/{self.agent.id}/", plan_info)

        self.assertIn("Agent add-ons:", agent_addons)

        self.assertIn(f"/app/agents/{self.agent.id}/settings", agent_settings)
        self.assertIn(f"/app/agents/{self.agent.id}/secrets", agent_settings)
        self.assertIn(f"/app/agents/{self.agent.id}/email", agent_settings)
        self.assertIn("Agent email settings", email_settings)
        self.assertIn(f"/app/agents/{self.agent.id}/email", email_settings)
