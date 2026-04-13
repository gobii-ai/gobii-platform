from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag

from api.agent.core.prompt_context import (
    _build_agent_capabilities_sections,
    _get_sandbox_prompt_summary,
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
        agent_addons = sections.get("agent_addons", "")
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

        self.assertIn("Agent add-ons:", agent_addons)
        self.assertIn("Task pack: adds extra task credits", agent_addons)
        self.assertIn("Contact pack: increases the per-agent contact cap", agent_addons)
        self.assertIn("Browser task pack: increases the per-agent daily browser task limit", agent_addons)
        self.assertIn("Advanced CAPTCHA resolution: enables CapSolver-powered CAPTCHA solving", agent_addons)

        self.assertIn(f"/console/agents/{self.agent.id}/", agent_settings)
        self.assertIn("The agent settings UI is a single page.", agent_settings)
        self.assertIn("Do not invent subpage links", agent_settings)
        self.assertIn("Only use explicitly listed URLs", agent_settings)
        self.assertIn(f"/console/agents/{self.agent.id}/secrets/", agent_settings)
        self.assertIn(f"/console/agents/{self.agent.id}/email/", agent_settings)
        self.assertIn("Agent email settings", email_settings)
        self.assertIn("SMTP (outbound)", email_settings)
        self.assertIn("IMAP (inbound)", email_settings)
        self.assertIn(f"/console/agents/{self.agent.id}/email/", email_settings)

    @patch("api.agent.core.prompt_context.ensure_steps_compacted")
    @patch("api.agent.core.prompt_context.ensure_comms_compacted")
    def test_build_prompt_context_includes_secret_request_guidance(self, _mock_comms, _mock_steps):
        context, _, _ = build_prompt_context(self.agent)
        contents = "\n".join(message["content"] for message in context)

        self.assertIn("domain-scoped credentials for `http_request`", contents)
        self.assertIn("login credentials for `spawn_web_task`", contents)
        self.assertIn("`secret_type='env_var'`", contents)
        self.assertIn("`os.environ`", contents)

    @patch("api.agent.core.prompt_context.sandbox_compute_enabled_for_agent", return_value=True)
    def test_sandbox_summary_biases_toward_custom_tools_for_bulk_work(self, _mock_sandbox):
        summary = _get_sandbox_prompt_summary(self.agent)

        self.assertIn("Default mode for repetitive, paginated, or bulk work", summary)
        self.assertIn("Prefer a small custom tool for repetitive, paginated, or bulk work", summary)
        self.assertIn("bulk MCP/API fan-out", summary)
        self.assertIn("bulk SQLite writes", summary)
        self.assertIn("Those triggers are not exhaustive", summary)
        self.assertIn("err on the side of creating and using one", summary)
        self.assertIn("especially strong trigger", summary)
        self.assertIn("even if the user did not explicitly ask for a custom tool or mention SQLite", summary)
        self.assertIn("Prefer `ALL_PROXY` as the canonical proxy path", summary)
        self.assertIn("direct HTTPS tunneling", summary)
        self.assertIn("ctx.requests_proxies()", summary)
        self.assertIn("ctx.proxy_url()", summary)
        self.assertIn("not bare `requests`/`httpx`", summary)
        self.assertIn("`secure_credentials_request` using `secret_type='env_var'`", summary)
