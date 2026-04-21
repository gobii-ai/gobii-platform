from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag
from django.urls import reverse

from api.agent.core.prompt_context import _get_system_instruction
from api.agent.tools.planning import execute_end_planning
from api.agent.tools.static_tools import get_static_tool_definitions
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

    def test_direct_agents_default_skipped_but_provisioning_starts_planning(self):
        self.assertEqual(self.agent.planning_state, PersistentAgent.PlanningState.SKIPPED)

        result = PersistentAgentProvisioningService.provision(
            user=self.user,
            name="Provisioned Planning Agent",
            charter="Research product leads",
        )

        self.assertEqual(result.agent.planning_state, PersistentAgent.PlanningState.PLANNING)

    def test_planning_static_tools_keep_normal_tools_and_add_end_planning(self):
        self.agent.planning_state = PersistentAgent.PlanningState.PLANNING
        self.agent.save(update_fields=["planning_state", "updated_at"])

        with patch("api.agent.tools.static_tools.sandbox_compute_enabled_for_agent", return_value=False):
            names = _tool_names(get_static_tool_definitions(self.agent))

        self.assertIn("end_planning", names)
        self.assertIn("request_human_input", names)
        self.assertIn("spawn_web_task", names)
        self.assertIn("send_chat_message", names)

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
        with patch("api.agent.tools.static_tools.sandbox_compute_enabled_for_agent", return_value=False):
            self.assertNotIn("end_planning", _tool_names(get_static_tool_definitions(self.agent)))

    def test_planning_prompt_replaces_first_run_prompt(self):
        self.agent.planning_state = PersistentAgent.PlanningState.PLANNING
        self.agent.save(update_fields=["planning_state", "updated_at"])

        prompt = _get_system_instruction(
            self.agent,
            is_first_run=True,
            implied_send_context={"display_name": "Matt"},
        )

        self.assertIn("Planning Mode", prompt)
        self.assertIn("end_planning", prompt)
        self.assertIn("Skip Planning", prompt)
        self.assertIn("`requests` parameter", prompt)
        self.assertIn("each item contains exactly one question", prompt)
        self.assertIn("`will_continue_work=false` on request_human_input", prompt)
        self.assertNotIn("Your very first action must be sending a welcome message", prompt)

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

        with patch("console.api_views.process_agent_events_task.delay") as delay_mock:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    reverse("console_agent_planning_skip", kwargs={"agent_id": self.agent.id})
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["planning_state"], PersistentAgent.PlanningState.SKIPPED)
        self.assertEqual(payload["pending_action_requests"], [])
        delay_mock.assert_called_once_with(str(self.agent.id))

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
