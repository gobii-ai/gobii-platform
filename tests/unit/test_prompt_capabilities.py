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

        self.assertIn("Use enabled `create_custom_tool` directly", contents)
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
            "Plan-aware termination sequence",
            contents,
        )
        self.assertIn(
            "Send the final report with will_continue_work=false only if no current plan items remain todo/doing",
            contents,
        )
        self.assertIn(
            "send with will_continue_work=true, then call update_plan with every finished/deferred item resolved and will_continue_work=false",
            contents,
        )
        self.assertIn(
            "After the final send and final plan update, stop with no extra message",
            contents,
        )
        self.assertIn("Plain text is invisible and update_plan is not delivery", contents)
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

        self.assertIn("context, config changes, findings, or finals.", description)
        self.assertIn("Use request_human_input instead when the agent has been blocked repeatedly", description)
        self.assertIn("needs a tracked answer", description)
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

        self.assertIn("prefer search/scrape/structured-data/API tools", description)
        self.assertIn("webpage screenshots", description)
        self.assertIn("save pages as PDFs", description)
        self.assertIn("persisted filespace paths", description)
        self.assertIn("if the user asks for a screenshot or visual proof of a webpage, use this tool", description)

    @patch("api.agent.core.prompt_context.sandbox_compute_enabled_for_agent", return_value=True)
    def test_sandbox_summary_mentions_custom_tool_discovery_for_bulk_work(self, _mock_sandbox):
        summary = _get_sandbox_prompt_summary(self.agent)

        self.assertIn("Use enabled `create_custom_tool` directly", summary)
        self.assertIn("repetitive, paginated, bulk, deterministic", summary)
        self.assertIn("MCP/API fan-out", summary)
        self.assertIn("use `search_tools` only if create_custom_tool is missing", summary)
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
