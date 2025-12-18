from unittest.mock import patch
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag

from api.agent.core.prompt_context import _build_agent_capabilities_block
from api.models import BrowserUseAgent, PersistentAgent
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
    @patch("api.agent.core.prompt_context.TaskCreditService.get_owner_task_credits_used")
    @patch("api.agent.core.prompt_context.TaskCreditService.get_tasks_entitled_for_owner")
    @patch("api.agent.core.prompt_context.get_owner_plan")
    def test_capabilities_block_includes_plan_addons_and_links(
        self,
        plan_mock,
        entitled_mock,
        used_mock,
        uplift_mock,
        _dedicated_mock,
    ):
        plan_mock.return_value = {
            "id": "startup",
            "name": "Pro",
            "max_contacts_per_agent": 20,
        }
        uplift_mock.return_value = AddonUplift(task_credits=2000, contact_cap=10)
        entitled_mock.return_value = Decimal("10")
        used_mock.return_value = Decimal("3.5")

        # Seed contact usage
        from api.models import CommsAllowlistEntry

        CommsAllowlistEntry.objects.create(
            agent=self.agent,
            channel="email",
            address="a@example.com",
            is_active=True,
            allow_inbound=True,
            allow_outbound=True,
        )

        block = _build_agent_capabilities_block(self.agent)

        self.assertIn("Plan: Pro", block)
        self.assertIn("Available plans", block)
        self.assertIn("Intelligence selection available", block)
        self.assertIn("Add-ons: +2000 credits; +10 contacts.", block)
        self.assertIn("Per-agent contact cap: 30 (base 20", block)
        self.assertIn("Contact usage: 1/30", block)
        self.assertIn("Account task credits: 3.5/10", block)
        self.assertIn("Dedicated IPs purchased: 2", block)
        self.assertIn(f"/console/agents/{self.agent.id}/", block)
        self.assertIn("/console/billing/", block)
