from unittest.mock import patch

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag
from django.urls import reverse
from waffle.testutils import override_flag

from api.agent.core.processing_flags import get_human_inbound_generation
from api.agent.core.prompt_context import _get_system_instruction, build_prompt_context
from api.agent.tools.planning import execute_end_planning, get_end_planning_tool
from api.agent.tools.request_human_input import get_request_human_input_tool
from api.agent.tools.schedule_updater import execute_update_schedule
from api.agent.tools.static_tools import PLANNING_MODE_DISABLED_TOOL_NAMES, get_static_tool_definitions
from api.agent.tools.tool_runtime import execute_runtime_tool_call
from constants.feature_flags import PERSISTENT_AGENT_PLANNING_MODE
from api.models import (
    BrowserUseAgent,
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentConversation,
    PersistentAgentHumanInputRequest,
    build_web_agent_address,
    build_web_user_address,
)
from api.serializers import PersistentAgentListSerializer, PersistentAgentSerializer
from api.services.persistent_agents import PersistentAgentProvisioningService
from api.services.tool_blacklist import invalidate_tool_blacklist_cache
from tests.utils.llm_seed import get_intelligence_tier


def _tool_names(tools: list[dict]) -> set[str]:
    names: set[str] = set()
    for tool in tools:
        function = tool.get("function")
        if isinstance(function, dict) and isinstance(function.get("name"), str):
            names.add(function["name"])
    return names


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
        EmailAddress.objects.create(
            user=self.user,
            email=self.user.email,
            verified=True,
            primary=True,
        )

    def test_direct_agents_default_skipped_but_provisioning_starts_planning_by_default(self):
        self.assertEqual(self.agent.planning_state, PersistentAgent.PlanningState.SKIPPED)

        result = PersistentAgentProvisioningService.provision(
            user=self.user,
            name="Provisioned Planning Agent",
            charter="Research product leads",
        )

        self.assertEqual(result.agent.planning_state, PersistentAgent.PlanningState.PLANNING)

    def test_provisioning_skips_planning_when_flag_off(self):
        with override_flag(PERSISTENT_AGENT_PLANNING_MODE, active=False):
            result = PersistentAgentProvisioningService.provision(
                user=self.user,
                name="Provisioned Nonplanning Agent",
                charter="Research product leads",
            )

        self.assertEqual(result.agent.planning_state, PersistentAgent.PlanningState.SKIPPED)

    def test_explicit_planning_state_overrides_planning_flag(self):
        with override_flag(PERSISTENT_AGENT_PLANNING_MODE, active=True):
            result = PersistentAgentProvisioningService.provision(
                user=self.user,
                name="Explicitly Skipped Planning Agent",
                charter="Research product leads",
                planning_state=PersistentAgent.PlanningState.SKIPPED,
            )

        self.assertEqual(result.agent.planning_state, PersistentAgent.PlanningState.SKIPPED)

    def test_planning_static_tools_hide_execution_tools_and_add_end_planning(self):
        self.agent.planning_state = PersistentAgent.PlanningState.PLANNING
        self.agent.save(update_fields=["planning_state", "updated_at"])

        names = _tool_names(get_static_tool_definitions(self.agent))

        self.assertIn("end_planning", names)
        self.assertIn("request_human_input", names)
        self.assertIn("search_tools", names)
        self.assertNotIn("spawn_web_task", names)
        self.assertIn("send_chat_message", names)
        self.assertTrue(PLANNING_MODE_DISABLED_TOOL_NAMES.isdisjoint(names))

    def test_sms_disabled_agents_do_not_receive_send_sms_tool(self):
        self.agent.sms_disabled = True
        self.agent.save(update_fields=["sms_disabled", "updated_at"])

        names = _tool_names(get_static_tool_definitions(self.agent))

        self.assertIn("send_email", names)
        self.assertNotIn("send_sms", names)

    def test_planning_runtime_rejects_disallowed_tools(self):
        self.agent.planning_state = PersistentAgent.PlanningState.PLANNING
        self.agent.save(update_fields=["planning_state", "updated_at"])

        for tool_name in PLANNING_MODE_DISABLED_TOOL_NAMES:
            result, updated_tools = execute_runtime_tool_call(self.agent, tool_name=tool_name, exec_params={})

            self.assertIsNone(updated_tools)
            self.assertEqual(result["status"], "error")
            self.assertIn("planning mode", result["message"])

    def test_end_planning_tool_description_requires_planning_to_finish_before_work(self):
        tool = get_end_planning_tool()
        function = tool["function"]

        self.assertIn(
            "For clear one-off research, factual answers, or execute-now requests",
            function["description"],
        )
        self.assertIn(
            "call this before search_tools, web/search tools, or result delivery",
            function["description"],
        )
        self.assertIn(
            "before work begins",
            function["parameters"]["properties"]["full_plan"]["description"],
        )

    def test_request_human_input_tool_schema_warns_planning_questions_must_be_tracked(self):
        tool = get_request_human_input_tool()
        function = tool["function"]

        self.assertIn("In Planning Mode, planning questions must use this tool", function["description"])
        self.assertIn("chat/email/SMS-only questions are not tracked", function["description"])

    def test_end_planning_replaces_charter_and_removes_planning_tool(self):
        self.agent.planning_state = PersistentAgent.PlanningState.PLANNING
        self.agent.save(update_fields=["planning_state", "updated_at"])
        full_plan = "Goal: find qualified leads weekly. Delivery: send a Friday summary."

        with patch("api.services.agent_planning._schedule_charter_metadata") as schedule_mock:
            response = execute_end_planning(self.agent, {"full_plan": full_plan})

        self.agent.refresh_from_db()
        self.assertEqual(response["status"], "ok")
        schedule_mock.assert_called_once()
        self.assertEqual(self.agent.planning_state, PersistentAgent.PlanningState.COMPLETED)
        self.assertEqual(self.agent.planning_plan, full_plan)
        self.assertEqual(self.agent.charter, full_plan)
        self.assertIsNotNone(self.agent.planning_completed_at)
        self.assertNotIn("end_planning", _tool_names(get_static_tool_definitions(self.agent)))

    def test_planning_prompt_is_inserted_before_first_run_welcome(self):
        self._set_email_welcome_target()
        self.agent.planning_state = PersistentAgent.PlanningState.PLANNING
        self.agent.save(update_fields=["planning_state", "updated_at"])

        prompt = _get_system_instruction(
            self.agent,
            is_first_run=True,
            implied_send_context={"display_name": "Matt"},
        )

        self.assertIn("You are a persistent AI agent.", prompt)
        self.assertIn("## Durable Config", prompt)
        self.assertIn("search_tools(", prompt)
        self.assertNotIn("search_tools(will_continue_work=true)", prompt)
        self.assertIn("## Planning Mode", prompt)
        self.assertIn("If there is no concrete task to do yet, your first action should be one concise welcome message", prompt)
        self.assertIn(f"Contact channel: email at {self.user.email}", prompt)
        self.assertIn("Your welcome message should", prompt)
        self.assertIn("After the welcome, continue Planning Mode", prompt)
        self.assertIn("call end_planning in the same response as any welcome", prompt)
        self.assertIn("never send a welcome-only", prompt)
        self.assertIn("Be warm and adventurous", prompt)
        self.assertIn("### R1: Greeting (first impression)", prompt)
        self.assertIn("## Then Planning Mode: clarify before main work", prompt)
        self.assertIn("clear plain-language brief before doing the work", prompt)
        self.assertIn("Keep planning non-technical and focused on what the user wants", prompt)
        self.assertIn("If timing changes the shape of the work itself", prompt)
        self.assertNotIn("Start your response with a brief welcome message to Matt", prompt)
        self.assertIn("After the welcome, continue Planning Mode", prompt)
        self.assertIn("move planning forward or call end_planning, not start the deliverable work", prompt)
        self.assertIn("Stay in planning only until planning is completed or skipped", prompt)
        self.assertIn("or otherwise start doing the task before calling end_planning", prompt)
        self.assertIn("If the welcome asks planning questions by email or SMS", prompt)
        self.assertIn("call request_human_input in the same", prompt)
        self.assertIn("If the task is clear enough, call end_planning instead", prompt)
        self.assertIn("skip those questions and get right to work", prompt)
        self.assertIn("end_planning", prompt)
        self.assertIn("Skip Planning", prompt)
        self.assertIn("`requests` parameter", prompt)
        self.assertIn("each item contains exactly one question", prompt)
        self.assertIn("`will_continue_work=false` on request_human_input", prompt)
        self.assertIn("Use request_human_input for every planning question or blocker", prompt)
        self.assertIn("never use send_chat_message/email/SMS as the question itself", prompt)
        self.assertIn("untracked and do not count", prompt)
        self.assertIn("questions are visible in web chat", prompt)
        self.assertIn("reference pending questions", prompt)
        self.assertIn(
            "Do not ask planning questions about communication channels, delivery methods, integrations, accounts, or implementation approach unless the user explicitly asks to configure or choose them",
            prompt,
        )
        self.assertIn(
            "Do not ask which communication channel or delivery method to use for planning when this welcome target or other prompt context already gives you a current or preferred setup",
            prompt,
        )
        self.assertIn("Keep planning questions focused on the user's need, scope, and desired outcome", prompt)
        self.assertIn("Planning Mode overrides normal execution-oriented instructions", prompt)
        self.assertIn("Stay in planning only until you call end_planning(full_plan=...)", prompt)
        self.assertIn("Only planning-safe tools are available", prompt)
        self.assertIn("update_plan, request_contact_permission", prompt)
        self.assertNotIn("spawn_agent", prompt)
        self.assertNotIn("Normal tools are available", prompt)
        self.assertIn("Use read-only research during planning only when the scope is unclear", prompt)
        self.assertIn("do not fetch, parse, or summarize sources to answer a clear task before end_planning", prompt)
        self.assertIn("Named integration setup/use: if no enabled tool fits, call search_tools before asking how to connect", prompt)
        self.assertIn("Do not do substantive task execution before planning ends", prompt)
        self.assertNotIn("no research for the deliverable", prompt)
        self.assertIn("no implementation", prompt)
        self.assertIn("Do not update __agent_config.charter directly as a substitute", prompt)
        self.assertIn("Do not update the runtime plan, schedule/__agent_config.schedule, or begin deliverable work", prompt)
        self.assertIn("treat that instruction as applying only after Planning Mode is completed or skipped", prompt)
        self.assertIn("call end_planning first and only begin the work after planning has ended", prompt)
        self.assertIn("Do not start doing the task while planning mode is still active", prompt)
        self.assertIn("Planning ends when you call this tool; the actual work starts only after that", prompt)
        self.assertIn("Only ask about timing or timezone if it changes the scope of the work itself", prompt)
        self.assertNotIn("delivery format", prompt)
        self.assertNotIn("delivery cadence", prompt)
        self.assertNotIn("integrations or accounts", prompt)
        self.assertNotIn("how results should be delivered", prompt)
        self.assertNotIn("delivery expectations", prompt)
        self.assertNotIn("## Then charter + runtime plan + everything else", prompt)
        self.assertNotIn("### Execution Template", prompt)

        normal_prompt_index = prompt.index("You are a persistent AI agent.")
        planning_index = prompt.index("## Planning Mode")
        welcome_index = prompt.index("This is your first run.")
        self.assertLess(normal_prompt_index, planning_index)
        self.assertLess(planning_index, welcome_index)

    def test_planning_prompt_keeps_normal_context_without_first_run(self):
        self.agent.planning_state = PersistentAgent.PlanningState.PLANNING
        self.agent.save(update_fields=["planning_state", "updated_at"])

        prompt = _get_system_instruction(
            self.agent,
            is_first_run=False,
            continuation_notice="Resume the pending planning turn.",
        )

        self.assertIn("You are a persistent AI agent.", prompt)
        self.assertIn("## Durable Config", prompt)
        self.assertIn("## Planning Mode", prompt)
        self.assertIn("Resume the pending planning turn.", prompt)
        self.assertIn(
            "Do not ask planning questions about communication channels, delivery methods, integrations, accounts, or implementation approach unless the user explicitly asks to configure or choose them",
            prompt,
        )
        self.assertIn("Keep planning non-technical and focused on what the user wants", prompt)
        self.assertIn("Use read-only research during planning only when the scope is unclear", prompt)
        self.assertIn("Named integration setup/use: if no enabled tool fits, call search_tools before asking how to connect", prompt)
        self.assertIn("Use request_human_input for every planning question or blocker", prompt)
        self.assertIn("call end_planning first and only begin the work after planning has ended", prompt)
        self.assertEqual(prompt.count("Resume the pending planning turn."), 1)
        self.assertNotIn("REQUIRED: First-Run Welcome", prompt)
        self.assertNotIn("You control your schedule. Update __agent_config.schedule via sqlite_batch when needed", prompt)
        self.assertNotIn("make it weekly", prompt)
        self.assertNotIn("check every hour", prompt)
        self.assertNotIn("Ask about timezone if relevant", prompt)
        self.assertIn("schedule/__agent_config.schedule", prompt)
        self.assertIn("Only ask about timing or timezone if it changes the scope of the work itself", prompt)
        self.assertIn("Do not update the runtime plan, schedule/__agent_config.schedule, or begin deliverable work until planning is completed", prompt)

    def test_planning_prompt_context_surfaces_pending_human_input_requests(self):
        self.agent.planning_state = PersistentAgent.PlanningState.PLANNING
        self.agent.save(update_fields=["planning_state", "updated_at"])
        conversation = self._create_web_conversation()
        PersistentAgentHumanInputRequest.objects.create(
            agent=self.agent,
            conversation=conversation,
            question="What locations should I search?",
            requested_via_channel=CommsChannel.WEB,
        )

        with patch("api.agent.core.prompt_context.ensure_steps_compacted"), patch(
            "api.agent.core.prompt_context.ensure_comms_compacted"
        ):
            context, _, _ = build_prompt_context(self.agent, is_first_run=False)

        content = "\n".join(message["content"] for message in context)
        self.assertIn("Pending human input requests", content)
        self.assertIn("Treat these as open questions", content)
        self.assertIn("What locations should I search?", content)
        self.assertIn("Do not assume they are answered unless a newer inbound message directly answers them", content)

    def test_planning_prompt_context_avoids_schedule_setup_guidance(self):
        self.agent.planning_state = PersistentAgent.PlanningState.PLANNING
        self.agent.schedule = "@daily"
        self.agent.save(update_fields=["planning_state", "schedule", "updated_at"])

        with patch("api.agent.core.prompt_context.ensure_steps_compacted"), patch(
            "api.agent.core.prompt_context.ensure_comms_compacted"
        ):
            context, _, _ = build_prompt_context(self.agent, is_first_run=False)

        system_message = next((m for m in context if m["role"] == "system"), None)
        user_message = next((m for m in context if m["role"] == "user"), None)

        self.assertIsNotNone(system_message)
        self.assertIsNotNone(user_message)
        self.assertNotIn("⚠️ NO SCHEDULE SET.", user_message["content"])
        self.assertNotIn("UPDATE YOUR SCHEDULE if the timing no longer matches the job", user_message["content"])
        self.assertIn("Planning Mode is active; schedule changes are deferred until planning ends", user_message["content"])
        self.assertNotIn("To update your charter or schedule", user_message["content"])
        self.assertIn("defer __agent_config mutations until after end_planning", user_message["content"])
        self.assertIn("Planning questions must use request_human_input", user_message["content"])
        self.assertNotIn("You control your schedule.", system_message["content"])
        self.assertNotIn("check every hour", system_message["content"])
        self.assertNotIn("weekly on Fridays", system_message["content"])
        self.assertNotIn("Ask about timezone if relevant", system_message["content"])
        self.assertIn("Only ask about timing or timezone if it changes the scope of the work itself", system_message["content"])

    def test_update_schedule_is_blocked_during_planning(self):
        self.agent.planning_state = PersistentAgent.PlanningState.PLANNING
        self.agent.schedule = "@daily"
        self.agent.save(update_fields=["planning_state", "schedule", "updated_at"])

        response = execute_update_schedule(self.agent, {"new_schedule": "0 12 * * *"})

        self.assertEqual(response["status"], "error")
        self.assertIn("planning mode", response["message"].lower())
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.schedule, "@daily")

    def test_non_planning_first_run_keeps_existing_work_prompt(self):
        self._set_email_welcome_target()

        prompt = _get_system_instruction(self.agent, is_first_run=True)

        self.assertIn("## Then calibrate setup to the task", prompt)
        self.assertIn("### Execution Template", prompt)
        self.assertIn("search_tools('{domain}')", prompt)
        self.assertNotIn("search_tools(will_continue_work=true)", prompt)
        self.assertIn("Use sqlite_batch for durable analysis data", prompt)
        self.assertIn("update __agent_config only if", prompt)
        self.assertNotIn("## Planning Mode", prompt)

    def test_skip_endpoint_cancels_pending_questions_and_exposes_payloads(self):
        self.agent.planning_state = PersistentAgent.PlanningState.PLANNING
        self.agent.save(update_fields=["planning_state", "updated_at"])
        conversation = self._create_web_conversation()
        pending_request = PersistentAgentHumanInputRequest.objects.create(
            agent=self.agent,
            conversation=conversation,
            question="What locations should I search?",
            requested_via_channel=CommsChannel.WEB,
        )
        self.client.force_login(self.user)
        before_generation = get_human_inbound_generation(self.agent.id)
        expected_generation = before_generation + 1

        with patch("console.api_views.process_agent_events_task.delay") as delay_mock:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    reverse("console_agent_planning_skip", kwargs={"agent_id": self.agent.id})
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["planning_state"], PersistentAgent.PlanningState.SKIPPED)
        self.assertEqual(payload["pending_action_requests"], [])
        self.assertEqual(get_human_inbound_generation(self.agent.id), expected_generation)
        delay_mock.assert_called_once_with(
            str(self.agent.id),
            inbound_generation=expected_generation,
        )

        self.agent.refresh_from_db()
        pending_request.refresh_from_db()
        self.assertEqual(self.agent.planning_state, PersistentAgent.PlanningState.SKIPPED)
        self.assertEqual(self.agent.charter, "Initial charter")
        self.assertEqual(pending_request.status, PersistentAgentHumanInputRequest.Status.CANCELLED)

        timeline_response = self.client.get(
            reverse("console_agent_timeline", kwargs={"agent_id": self.agent.id})
        )
        self.assertEqual(timeline_response.status_code, 200)
        self.assertEqual(timeline_response.json()["planning_state"], PersistentAgent.PlanningState.SKIPPED)

        roster_response = self.client.get(reverse("console_agent_roster"))
        self.assertEqual(roster_response.status_code, 200)
        roster_agent = next(
            item for item in roster_response.json()["agents"] if item["id"] == str(self.agent.id)
        )
        self.assertEqual(roster_agent["planning_state"], PersistentAgent.PlanningState.SKIPPED)

        detail_payload = PersistentAgentSerializer(self.agent).data
        list_payload = PersistentAgentListSerializer(self.agent).data
        self.assertEqual(detail_payload["planning_state"], PersistentAgent.PlanningState.SKIPPED)
        self.assertEqual(list_payload["planning_state"], PersistentAgent.PlanningState.SKIPPED)

    def _create_web_conversation(self) -> PersistentAgentConversation:
        user_address = build_web_user_address(self.user.id, self.agent.id)
        PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.WEB,
            address=build_web_agent_address(self.agent.id),
            is_primary=True,
        )
        PersistentAgentCommsEndpoint.objects.create(
            owner_agent=None,
            channel=CommsChannel.WEB,
            address=user_address,
        )
        return PersistentAgentConversation.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.WEB,
            address=user_address,
        )

    def _set_email_welcome_target(self) -> None:
        contact_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=None,
            channel=CommsChannel.EMAIL,
            address=self.user.email,
        )
        self.agent.preferred_contact_endpoint = contact_endpoint
        self.agent.save(update_fields=["preferred_contact_endpoint", "updated_at"])
