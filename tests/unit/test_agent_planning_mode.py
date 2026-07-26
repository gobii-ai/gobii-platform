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
        self.assertIn("send one brief same-channel acknowledgment as the entire first response", prompt)
        self.assertIn("text beside work tools is not delivery", prompt)
        self.assertIn("investment diligence, multi-entity comparisons", prompt)
        self.assertIn("the first useful result you will bring back", prompt)
        self.assertIn("continues after a meaningful evidence batch", prompt)
        self.assertIn("strongest finding and what remains", prompt)
        self.assertIn("Never announce phases", prompt)
        self.assertIn("Work Updates require explicit", prompt)
        self.assertIn("FIRST-RUN INTAKE OVERRIDES ACKNOWLEDGMENT", prompt)
        self.assertIn("no kickoff, fifth call, SQLite, config, deliverable, or prose question", prompt)
        self.assertIn("email=send_email in-thread", prompt)
        self.assertIn("repair rejected/wrong channel first", prompt)
        self.assertIn(
            "first response calls only this tool",
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

        self.assertIn("Broad ongoing/substantial first work", prompt)
        self.assertIn("at most four read-only public calls in two rounds", prompt)
        self.assertIn("questions were requested first", prompt)
        self.assertIn("No prose substitute", prompt)
        self.assertIn("next and only action is the highest-leverage question", prompt)
        self.assertIn("Use the current inbound channel", prompt)
        self.assertIn("native request_human_input card", prompt)
        self.assertIn("2-3 evidence-informed choices", prompt)
        self.assertIn("It stays pending if they leave", prompt)
        self.assertIn("follow the result guidance", prompt)
        self.assertIn("mirror the exact choices there", prompt)
        self.assertIn("send the same numbered choices there", prompt)
        self.assertIn("do no other work", prompt)
        self.assertIn("Otherwise start the task", prompt)

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
