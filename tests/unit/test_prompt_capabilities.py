from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag

from api.agent.core.prompt_context import (
    _build_agent_capabilities_sections,
    _get_sandbox_prompt_summary,
    build_prompt_context,
)
from api.agent.tools.run_command import get_run_command_tool
from api.agent.tools.spawn_web_task import get_spawn_web_task_tool
from api.agent.tools.web_chat_sender import get_send_chat_tool
from api.models import BrowserUseAgent, CommsAllowlistEntry, PersistentAgent
from api.services.web_sessions import start_web_session
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
        self.assertIn("/app/billing", plan_info)
        self.assertNotIn(f"/console/agents/{self.agent.id}/", plan_info)

        self.assertIn("Agent add-ons:", agent_addons)
        self.assertIn("Task pack: adds extra task credits", agent_addons)
        self.assertIn("Contact pack: increases the per-agent contact cap", agent_addons)
        self.assertIn("Browser task pack: increases the per-agent daily browser task limit", agent_addons)
        self.assertIn("Advanced CAPTCHA resolution: enables CapSolver-powered CAPTCHA solving", agent_addons)

        self.assertIn(f"/app/agents/{self.agent.id}/settings", agent_settings)
        self.assertIn("The agent settings UI is a single page.", agent_settings)
        self.assertIn("Do not invent subpage links", agent_settings)
        self.assertIn("Only use explicitly listed URLs", agent_settings)
        self.assertIn(f"/app/agents/{self.agent.id}/secrets", agent_settings)
        self.assertIn(f"/app/agents/{self.agent.id}/email", agent_settings)
        self.assertIn("Agent email settings", email_settings)
        self.assertIn("SMTP (outbound)", email_settings)
        self.assertIn("IMAP (inbound)", email_settings)
        self.assertIn(f"/app/agents/{self.agent.id}/email", email_settings)

    @patch("api.agent.core.prompt_context.ensure_steps_compacted")
    @patch("api.agent.core.prompt_context.ensure_comms_compacted")
    def test_build_prompt_context_includes_secret_request_guidance(self, _mock_comms, _mock_steps):
        context, _, _ = build_prompt_context(self.agent)
        contents = "\n".join(message["content"] for message in context)

        self.assertIn("domain-scoped credentials for `http_request`", contents)
        self.assertIn("login credentials for `spawn_web_task`", contents)
        self.assertIn("`secret_type='env_var'`", contents)
        self.assertIn("`os.environ`", contents)
        self.assertIn("Avoid 2FA/MFA unless the user explicitly asks for it", contents)
        self.assertIn("those flows may hit system limitations", contents)

    @patch("api.agent.core.prompt_context.sandbox_compute_enabled_for_agent", return_value=True)
    @patch("api.agent.core.prompt_context.ensure_steps_compacted")
    @patch("api.agent.core.prompt_context.ensure_comms_compacted")
    def test_build_prompt_context_omits_custom_tool_playbook_until_skill_enabled(
        self,
        _mock_comms,
        _mock_steps,
        _mock_sandbox,
    ):
        context, _, _ = build_prompt_context(self.agent)
        contents = "\n".join(message["content"] for message in context)

        self.assertIn("`create_custom_tool` is available through `search_tools`", contents)
        self.assertNotIn("System Skill: Custom Tool Development", contents)
        self.assertNotIn("Current custom-tool state:", contents)
        self.assertNotIn("PHILOSOPHY:", contents)
        self.assertNotIn("Default mode for repetitive or bulk work", contents)

    @patch("api.agent.core.prompt_context.ensure_steps_compacted")
    @patch("api.agent.core.prompt_context.ensure_comms_compacted")
    def test_build_prompt_context_discourages_internal_progress_narration(self, _mock_comms, _mock_steps):
        context, _, _ = build_prompt_context(self.agent)
        contents = "\n".join(message["content"] for message in context)

        self.assertIn("never narrate internal reasoning", contents)
        self.assertIn("tool sequencing", contents)
        self.assertIn("User-facing question, blocker, config change, or finding", contents)
        self.assertNotIn("Progress update?", contents)

    @patch("api.agent.core.prompt_context.ensure_steps_compacted")
    @patch("api.agent.core.prompt_context.ensure_comms_compacted")
    def test_build_prompt_context_says_final_send_stops(self, _mock_comms, _mock_steps):
        context, _, _ = build_prompt_context(self.agent)
        contents = "\n".join(message["content"] for message in context)

        self.assertIn(
            "This tool sends the final answer/report and no work remains after it → will_continue_work=false",
            contents,
        )
        self.assertIn(
            "Send your final report to the user with will_continue_work=false on that final send tool",
            contents,
        )
        self.assertIn(
            "If you still need to mark the plan done after the report is already sent, call update_plan with will_continue_work=false",
            contents,
        )
        self.assertIn("Plain text is invisible and update_plan is not delivery", contents)
        self.assertIn("no extra turn, no announcement or confirmation message", contents)
        self.assertNotIn(
            "Need to send the user your answer, summary, or final report → will_continue_work=true",
            contents,
        )

    @patch("api.agent.core.prompt_context.ensure_steps_compacted")
    @patch("api.agent.core.prompt_context.ensure_comms_compacted")
    def test_implied_send_prompt_keeps_working_silent(self, _mock_comms, _mock_steps):
        start_web_session(self.agent, self.user)

        context, _, _ = build_prompt_context(self.agent)
        contents = "\n".join(message["content"] for message in context)

        self.assertIn("Your response text is a user message", contents)
        self.assertIn("monitoring targets/scope before setup", contents)
        self.assertIn("never search for it or refetch the same successful URL", contents)
        self.assertIn("update your ongoing charter/schedule", contents)
        self.assertIn("While working, respond with tool calls and no text", contents)
        self.assertIn("never status narration", contents)

    def test_web_chat_tool_description_discourages_internal_narration(self):
        tool = get_send_chat_tool()
        description = tool["function"]["description"]
        will_continue_description = tool["function"]["parameters"]["properties"]["will_continue_work"]["description"]

        self.assertIn("non-blocking context, config changes, or findings", description)
        self.assertIn("Do not use this for questions that block the task", description)
        self.assertIn("use request_human_input so the question is tracked", description)
        self.assertIn("Do not narrate what you will do next", description)
        self.assertIn("Never send a message solely to justify continuing work", will_continue_description)

    def test_run_command_tool_description_distinguishes_shell_paths(self):
        tool = get_run_command_tool()
        description = tool["function"]["description"]

        self.assertIn("Gobii filespace paths like /tools/foo.py", description)
        self.assertIn("are for Gobii tool arguments, not shell paths", description)
        self.assertIn("use relative paths from the workspace root such as tools/foo.py", description)
        self.assertIn("absolute shell paths like /workspace/tools/foo.py", description)
        self.assertIn("Do not run /tools/foo.py", description)

    def test_spawn_web_task_description_requires_browser_only_need(self):
        tool = get_spawn_web_task_tool(self.agent)
        description = tool["function"]["description"]

        self.assertIn("search/scrape/structured-data/API tools cannot answer", description)
        self.assertIn("rendered page state, JavaScript, login, or user interaction", description)

    @patch("api.agent.core.prompt_context.sandbox_compute_enabled_for_agent", return_value=True)
    def test_sandbox_summary_mentions_custom_tool_discovery_for_bulk_work(self, _mock_sandbox):
        summary = _get_sandbox_prompt_summary(self.agent)

        self.assertIn("`create_custom_tool` is available through `search_tools`", summary)
        self.assertIn("repetitive, paginated, bulk, deterministic", summary)
        self.assertIn("MCP/API fan-out", summary)
        self.assertIn("reusable Python tool", summary)
        self.assertNotIn("source_path='/tools/name.py'", summary)
        self.assertNotIn("retry create_custom_tool, not create_file", summary)

    @patch("api.agent.core.prompt_context.sandbox_compute_enabled_for_agent", return_value=True)
    def test_sandbox_summary_distinguishes_tool_paths_from_shell_paths(self, _mock_sandbox):
        summary = _get_sandbox_prompt_summary(self.agent)

        self.assertIn("Gobii tool arguments use filespace paths", summary)
        self.assertIn("filespace paths like `/tools/foo.py`", summary)
        self.assertIn("shell commands use workspace paths", summary)
        self.assertIn("workspace paths like `tools/foo.py`", summary)
        self.assertIn("`/workspace/tools/foo.py`", summary)
        self.assertIn("`secure_credentials_request(secret_type='env_var')`", summary)
