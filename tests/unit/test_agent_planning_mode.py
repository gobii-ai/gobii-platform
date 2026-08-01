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
        self.assertIn("Substantial multi-round work: one brief same-channel kickoff(true)", prompt)
        self.assertIn("With any tool call, leave response content empty", prompt)
        self.assertIn("Response content with tool calls is user-facing", prompt)
        self.assertIn("investment diligence, multi-entity comparisons", prompt)
        self.assertIn("the first useful result you will bring back", prompt)
        self.assertIn("continues after a meaningful evidence batch", prompt)
        self.assertIn("strongest finding and what remains", prompt)
        self.assertIn("A decision-ready result ends the work", prompt)
        self.assertIn("Never announce phases", prompt)
        self.assertIn("explicit sends for Work Updates", prompt)
        self.assertIn("First-run intake and executable work are mutually exclusive", prompt)
        self.assertIn("First-run intake wins", prompt)
        self.assertIn("Prior-action question", prompt)
        self.assertIn("create/start nothing", prompt)
        self.assertIn("exact API endpoint + http_request", prompt)
        self.assertIn("Specific action/current-batch tool available", prompt)
        self.assertIn("Meaningful shared win/repeated failure", prompt)
        self.assertIn("Campaign/bulk review", prompt)
        self.assertIn("jointly verify copy", prompt)
        self.assertIn("email=send_email in-thread", prompt)
        self.assertIn("repair rejected/wrong channel first", prompt)
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

    def test_first_run_prompt_asks_only_when_role_boundaries_materially_change_work(self):
        prompt = _get_first_run_welcome_message_instruction(
            welcome_target=_FirstRunWelcomeTarget(
                channel=CommsChannel.WEB,
                address="web:user:1",
                send_tool_name="send_chat_message",
            )
        )

        self.assertIn("Choose one route before acting", prompt)
        self.assertIn("Broad substantial work missing a material audience", prompt)
        self.assertIn("make exactly one focused read-only public lookup", prompt)
        self.assertIn("Any result ends orientation", prompt)
        self.assertIn("no second lookup or sequential top-up", prompt)
        self.assertIn("A failed or irrelevant result becomes an interpretation/next-path choice", prompt)
        self.assertIn("Count across the whole first-run cycle", prompt)
        self.assertIn("never a reason to keep searching for certainty", prompt)
        self.assertIn("never authorizes silently deciding a missing boundary", prompt)
        self.assertIn("names no entity/source worth orienting on", prompt)
        self.assertIn("make exactly one request_human_input tool call", prompt)
        self.assertIn("Put all cards in that call's requests array", prompt)
        self.assertIn("never emit several request_human_input tool calls", prompt)
        self.assertIn("one card for each unresolved independent decision", prompt)
        self.assertIn("First decompose the task into independently answerable decisions", prompt)
        self.assertIn("the right count may be none, one, several, or more than three", prompt)
        self.assertIn("Never pad to a quota", prompt)
        self.assertIn("Each card records one choice", prompt)
        self.assertIn("each initial-intake card must contain at least 2 non-empty options", prompt)
        self.assertIn("every option object must have a non-empty title", prompt)
        self.assertIn("a non-empty one-sentence description", prompt)
        self.assertIn("Never mix free-text fields into this batch", prompt)
        self.assertIn("8 is the hard tool limit", prompt)
        self.assertIn("turn that ambiguity into choices", prompt)
        self.assertIn("They stay pending if the user leaves", prompt)
        self.assertIn("mirror every exact question and choice", prompt)
        self.assertIn("keep the card call continuing, send the mirror next", prompt)
        self.assertIn("gets the same numbered questions and choices", prompt)
        self.assertIn("Otherwise: start the task", prompt)

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
