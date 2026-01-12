from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag

from api.agent.core.prompt_context import _build_agent_capabilities_sections
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
        capabilities_note = sections.get("agent_capabilities_note", "")
        plan_info = sections.get("plan_info", "")
        agent_settings = sections.get("agent_settings", "")
        email_settings = sections.get("agent_email_settings", "")

        self.assertIn("plan/subscription info", capabilities_note)
        self.assertIn("Gobii account", capabilities_note)
        self.assertIn("agent settings available to the user", capabilities_note)
        self.assertIn("Plan: Pro", plan_info)
        self.assertIn("Available plans", plan_info)
        self.assertIn("Intelligence selection available", plan_info)
        self.assertIn(
            "Add-ons: +2000 credits; +10 contacts; +5 browser tasks/day; Advanced CAPTCHA resolution enabled.",
            plan_info,
        )
        self.assertIn("Per-agent contact cap: 30 (20 included in plan + add-ons", plan_info)
        self.assertIn("Contact usage: 1/30", plan_info)
        self.assertIn("Dedicated IPs purchased: 2", plan_info)
        self.assertIn("/console/billing/", plan_info)
        self.assertNotIn(f"/console/agents/{self.agent.id}/", plan_info)

        self.assertIn(f"/console/agents/{self.agent.id}/", agent_settings)
        self.assertIn("Agent email settings", email_settings)
        self.assertIn("SMTP (outbound)", email_settings)
        self.assertIn("IMAP (inbound)", email_settings)
        self.assertIn(f"/console/agents/{self.agent.id}/email/", email_settings)
