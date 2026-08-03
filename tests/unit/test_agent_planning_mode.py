from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag

from api.agent.core.prompt_context import (
    _FirstRunWelcomeTarget,
    _get_first_run_welcome_message_instruction,
    _get_system_instruction,
)
from api.agent.tools.planning import execute_end_planning
from api.agent.tools.schedule_updater import execute_update_schedule
from api.agent.tools.static_tools import (
    _agent_has_sms_endpoint,
    get_static_tool_definitions,
)
from api.models import (
    BrowserUseAgent,
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
)
from api.services.persistent_agents import PersistentAgentProvisioningService
from api.services.tool_blacklist import invalidate_tool_blacklist_cache
from tests.utils.llm_seed import get_intelligence_tier


def _tool_names(tools: list[dict]) -> set[str]:
    return {
        function["name"]
        for tool in tools
        if isinstance((function := tool.get("function")), dict)
        and isinstance(function.get("name"), str)
    }


@tag("batch_agent_chat")
@override_settings(PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=False)
class PersistentAgentPlanningModeTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="planning-owner",
            email="planning-owner@example.com",
            password="password123",
        )
        self.browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="Planning Browser",
        )
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Planning Agent",
            charter="Initial charter",
            browser_use_agent=self.browser_agent,
        )
        tier = get_intelligence_tier("standard")
        tier.blacklisted_tools = []
        tier.save(update_fields=["blacklisted_tools"])
        invalidate_tool_blacklist_cache()

    def test_provisioning_skips_legacy_planning_mode(self):
        default_result = PersistentAgentProvisioningService.provision(
            user=self.user,
            name="Default Agent",
            charter="Research product leads",
        )
        legacy_result = PersistentAgentProvisioningService.provision(
            user=self.user,
            name="Legacy Planning Agent",
            charter="Research product leads",
            planning_state=PersistentAgent.PlanningState.PLANNING,
        )

        self.assertEqual(default_result.agent.planning_state, PersistentAgent.PlanningState.SKIPPED)
        self.assertEqual(legacy_result.agent.planning_state, PersistentAgent.PlanningState.SKIPPED)

    def test_legacy_planning_state_keeps_normal_tools(self):
        self.agent.planning_state = PersistentAgent.PlanningState.PLANNING
        self.agent.save(update_fields=["planning_state", "updated_at"])

        names = _tool_names(get_static_tool_definitions(self.agent))

        self.assertNotIn("end_planning", names)
        self.assertTrue(
            {
                "apply_patch",
                "request_human_input",
                "search_tools",
                "secure_credentials_request",
                "spawn_web_task",
                "update_plan",
            }.issubset(names)
        )

    def test_legacy_planning_state_does_not_block_schedule_updates(self):
        self.agent.planning_state = PersistentAgent.PlanningState.PLANNING
        self.agent.schedule = "@daily"
        self.agent.save(update_fields=["planning_state", "schedule", "updated_at"])

        response = execute_update_schedule(self.agent, {"new_schedule": "0 12 * * *"})

        self.assertEqual(response["status"], "ok")
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.schedule, "0 12 * * *")

    def test_stale_end_planning_preserves_durable_charter(self):
        self.agent.planning_state = PersistentAgent.PlanningState.PLANNING
        self.agent.save(update_fields=["planning_state", "updated_at"])
        full_plan = "Goal: find qualified leads weekly. Delivery: send a Friday summary."

        response = execute_end_planning(self.agent, {"full_plan": full_plan})

        self.agent.refresh_from_db()
        self.assertEqual(response["status"], "ok")
        self.assertEqual(self.agent.planning_state, PersistentAgent.PlanningState.COMPLETED)
        self.assertEqual(self.agent.planning_plan, full_plan)
        self.assertEqual(self.agent.charter, "Initial charter")
        self.assertIsNotNone(self.agent.planning_completed_at)

    def test_stale_end_planning_is_a_noop_after_migration(self):
        response = execute_end_planning(self.agent, {"full_plan": "An obsolete generated plan"})

        self.agent.refresh_from_db()
        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["planning_state"], PersistentAgent.PlanningState.SKIPPED)
        self.assertEqual(self.agent.planning_state, PersistentAgent.PlanningState.SKIPPED)
        self.assertEqual(self.agent.planning_plan, "")
        self.assertEqual(self.agent.charter, "Initial charter")

    def test_legacy_planning_state_uses_normal_prompt(self):
        self.agent.planning_state = PersistentAgent.PlanningState.PLANNING
        self.agent.save(update_fields=["planning_state", "updated_at"])

        prompt = _get_system_instruction(self.agent, is_first_run=False)

        self.assertNotIn("## Planning Mode", prompt)
        self.assertNotIn("end_planning", prompt)
        self.assertIn("Use `update_plan` only for substantial multi-step work", prompt)

    def test_normal_prompt_distinguishes_first_assignment_discovery_from_clear_work(self):
        prompt = _get_system_instruction(
            self.agent,
            is_first_run=False,
            implied_send_context={
                "display_name": "Owner",
                "tool_example": "send_chat_message(body=..., will_continue_work=...)",
            },
        )
        definitions = get_static_tool_definitions(self.agent)
        send_chat = next(
            tool for tool in definitions if tool["function"]["name"] == "send_chat_message"
        )

        self.assertIn("Outside that first-assignment rule", prompt)
        self.assertIn("New substantial or explicit deep research", prompt)
        self.assertIn("With any tool call, leave response content empty", prompt)
        self.assertIn("Response content with tool calls is user-facing", prompt)
        self.assertIn("diligence, multi-entity comparison", prompt)
        self.assertIn("send one brief kickoff", prompt)
        self.assertIn("continues after a meaningful evidence batch", prompt)
        self.assertIn("strongest finding and what remains", prompt)
        self.assertIn("decision-ready result", prompt)
        self.assertIn("Never announce phases", prompt)
        self.assertIn("explicit sends for Work Updates", prompt)
        self.assertIn("Guided intake and executable work are mutually exclusive", prompt)
        self.assertIn("question about prior action", prompt)
        self.assertIn("do not create new state", prompt)
        self.assertIn("exact API endpoint + http_request", prompt)
        self.assertIn("execute it next without pre-reading or copying its source", prompt)
        self.assertIn("Named enabled tool: call it directly", prompt)
        self.assertIn("two or more tool results must be compared", prompt)
        self.assertIn("meaningful shared win or repeated failure", prompt)
        self.assertIn("Campaign/bulk work", prompt)
        self.assertIn("qa_issues(issue, record, evidence)", prompt)
        self.assertIn("unsafe or unqualified recipient", prompt)
        self.assertIn("email=send_email in-thread", prompt)
        self.assertIn(
            "Follow Work Updates for later milestone timing",
            send_chat["function"]["description"],
        )
        self.assertIn(
            "send one brief kickoff(true) first",
            send_chat["function"]["description"],
        )
        self.assertIn(
            "at most one concise orientation note",
            send_chat["function"]["description"],
        )
        self.assertNotIn(
            "pre-work status for short/finite work",
            send_chat["function"]["parameters"]["properties"]["body"]["description"],
        )

    def test_implied_send_system_prefix_is_stable_across_requesters(self):
        first = _get_system_instruction(
            self.agent,
            is_first_run=False,
            implied_send_context={
                "display_name": "Owner One",
                "tool_example": 'send_chat_message(to_address="web://user/1", body="...")',
            },
        )
        second = _get_system_instruction(
            self.agent,
            is_first_run=False,
            implied_send_context={
                "display_name": "Owner Two",
                "tool_example": 'send_chat_message(to_address="web://user/2", body="...")',
            },
        )

        self.assertEqual(first, second)
        self.assertIn("Implied Send → latest web chat requester", first)
        self.assertNotIn("web://user/", first)

    def test_first_run_prompt_asks_only_when_role_boundaries_materially_change_work(self):
        prompt = _get_first_run_welcome_message_instruction(
            welcome_target=_FirstRunWelcomeTarget(
                channel=CommsChannel.WEB,
                address="web:user:1",
                send_tool_name="send_chat_message",
            )
        )

        self.assertIn("Choose one route", prompt)
        self.assertIn("Broad task missing a material scope", prompt)
        self.assertIn("exactly one focused read-only lookup", prompt)
        self.assertIn("A failed lookup still ends orientation", prompt)
        self.assertIn("call request_human_input exactly once", prompt)
        self.assertIn("every card in its `requests` array", prompt)
        self.assertIn("one per unresolved material decision", prompt)
        self.assertIn("2-3 distinct options", prompt)
        self.assertIn("No padding or catch-all questions", prompt)
        self.assertIn("cannot decide missing scope", prompt)
        self.assertIn("named company or product is the seller", prompt)
        self.assertIn("company, product, brand, or internal project", prompt)
        self.assertIn("mirrored to email or SMS", prompt)
        self.assertLess(len(prompt), 2200)
        self.assertIn("Clear task: start it", prompt)

    @patch("api.agent.core.prompt_context.has_verified_email", return_value=True)
    def test_first_run_prompt_places_active_intake_after_stable_core(self, _has_verified_email):
        endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.WEB,
            address="web://user/1/agent/planning-agent",
        )
        self.agent.preferred_contact_endpoint = endpoint
        self.agent.save(update_fields=["preferred_contact_endpoint", "updated_at"])

        prompt = _get_system_instruction(self.agent, is_first_run=True)

        self.assertLess(prompt.index("<sqlite_guidance>"), prompt.index("This is your first run."))
        self.assertNotIn("### R2: Charter Construction", prompt)
        self.assertNotIn("### R3: Schedule Selection", prompt)

    def test_agents_without_sms_endpoint_do_not_receive_send_sms_tool(self):
        names = _tool_names(get_static_tool_definitions(self.agent))

        self.assertIn("send_email", names)
        self.assertNotIn("send_sms", names)

    def test_static_tools_without_agent_do_not_include_send_sms(self):
        names = _tool_names(get_static_tool_definitions(None))

        self.assertNotIn("send_sms", names)

    def test_sms_enabled_agents_with_sms_endpoint_receive_send_sms_tool(self):
        PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.SMS,
            address="+15555550123",
            is_primary=True,
        )

        names = _tool_names(get_static_tool_definitions(self.agent))

        self.assertIn("send_sms", names)

    def test_sms_endpoint_check_is_cached_on_agent_instance(self):
        PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.SMS,
            address="+15555550123",
            is_primary=True,
        )

        with self.assertNumQueries(1):
            self.assertTrue(_agent_has_sms_endpoint(self.agent))
            self.assertTrue(_agent_has_sms_endpoint(self.agent))

    def test_sms_disabled_agents_with_sms_endpoint_do_not_receive_send_sms_tool(self):
        PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.SMS,
            address="+15555550123",
            is_primary=True,
        )
        self.agent.sms_disabled = True
        self.agent.save(update_fields=["sms_disabled", "updated_at"])

        names = _tool_names(get_static_tool_definitions(self.agent))

        self.assertIn("send_email", names)
        self.assertNotIn("send_sms", names)
