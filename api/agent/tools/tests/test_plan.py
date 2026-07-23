from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, tag

from api.agent.tools.plan import (
    build_redundant_research_plan_skip_result,
    execute_update_plan,
    format_current_plan_for_prompt,
    get_update_plan_tool,
)
from api.models import (
    BrowserUseAgent,
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentConversation,
    PersistentAgentKanbanCard,
    PersistentAgentMessage,
)


@tag("batch_agent_tools")
class UpdatePlanValidationTests(SimpleTestCase):
    agent = SimpleNamespace(id="agent-123")

    def test_message_deliverable_schema_excludes_peer_messages(self):
        tool = get_update_plan_tool()
        messages_description = tool["function"]["parameters"]["properties"]["messages"]["description"]
        message_id_description = (
            tool["function"]["parameters"]["properties"]["messages"]["items"]["properties"]["message_id"]["description"]
        )

        self.assertIn("send_email", messages_description)
        self.assertIn("send_sms", messages_description)
        self.assertIn("send_chat_message", messages_description)
        self.assertIn("not for every quick answer", messages_description)
        self.assertIn("send it first with will_continue_work=true", messages_description)
        self.assertIn("then call update_plan after the send tool returns", messages_description)
        self.assertIn("send the final answer with will_continue_work=false", messages_description)
        self.assertIn("Do not include peer messages", messages_description)
        self.assertIn("Exact UUID", message_id_description)
        self.assertIn("never use placeholders", message_id_description)

    def test_tool_description_guides_plan_reset_for_new_iterations(self):
        tool = get_update_plan_tool()
        description = tool["function"]["description"]

        self.assertIn("full current active plan", description)
        self.assertIn("usually 3-6 active steps", description)
        self.assertIn("omit stale prior-task or prior-run steps", description)
        self.assertIn("new scheduled run", description)
        self.assertIn("do not create one step per day, hour, or recurrence slot", description)
        self.assertIn("represent the current run with compact reusable phases", description)

    def test_invalid_message_deliverable_feedback_explains_user_facing_only(self):
        result = execute_update_plan(
            self.agent,
            {
                "plan": [{"step": "Send update", "status": "done"}],
                "messages": [
                    {
                        "label": "Peer update",
                        "message_id": "peer://51ee7718-e3a4-43b6-a88d-67d0a8bd346c::agent-123",
                    }
                ],
                "will_continue_work": False,
            },
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("messages[0].message_id must be a valid UUID", result["message"])
        self.assertIn("Use messages only for substantial final deliverables", result["message"])
        self.assertIn("send_email", result["message"])
        self.assertIn("send_sms", result["message"])
        self.assertIn("send_chat_message", result["message"])
        self.assertIn("send it first with will_continue_work=true", result["message"])
        self.assertIn("send the final answer with will_continue_work=false", result["message"])
        self.assertIn("Do not include peer messages from send_agent_message", result["message"])


@tag("batch_agent_tools")
class UpdatePlanResearchSuppressionTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="plan_research_suppression_user")
        self.browser_agent = BrowserUseAgent.objects.create(user=self.user, name="Plan Research Browser")
        self.agent = PersistentAgent.objects.create(
            name="Plan Research Agent",
            user=self.user,
            browser_use_agent=self.browser_agent,
            charter="Test agent.",
        )

    def test_unfinished_plan_prompt_places_cleanup_next_to_plan(self):
        PersistentAgentKanbanCard.objects.create(
            assigned_agent=self.agent,
            title="Deliver the final report",
            status=PersistentAgentKanbanCard.Status.TODO,
            priority=1,
        )

        prompt = format_current_plan_for_prompt(self.agent)

        self.assertIn("send the final delivery with true", prompt)
        self.assertIn("finish/defer all Doing/Todo via update_plan false", prompt)

    def test_planning_mode_does_not_prompt_for_final_delivery(self):
        self.agent.planning_state = PersistentAgent.PlanningState.PLANNING
        self.agent.save(update_fields=["planning_state"])
        PersistentAgentKanbanCard.objects.create(
            assigned_agent=self.agent,
            title="Deliver the final report",
            status=PersistentAgentKanbanCard.Status.TODO,
            priority=1,
        )

        prompt = format_current_plan_for_prompt(self.agent)

        self.assertNotIn("send the final delivery", prompt)

    def test_redundant_research_progress_update_is_skipped(self):
        PersistentAgentKanbanCard.objects.create(
            assigned_agent=self.agent,
            title="Research source set",
            status=PersistentAgentKanbanCard.Status.DOING,
            priority=2,
        )
        PersistentAgentKanbanCard.objects.create(
            assigned_agent=self.agent,
            title="Synthesize investment memo",
            status=PersistentAgentKanbanCard.Status.TODO,
            priority=1,
        )

        result = build_redundant_research_plan_skip_result(
            self.agent,
            {
                "plan": [
                    {"step": "Research source set", "status": "doing"},
                    {"step": "Synthesize investment memo", "status": "todo"},
                ],
                "will_continue_work": True,
            },
        )

        self.assertIsNotNone(result)
        self.assertTrue(result["skipped"])
        self.assertFalse(result["auto_sleep_ok"])

    def test_research_progress_update_with_retitled_steps_is_not_skipped(self):
        PersistentAgentKanbanCard.objects.create(
            assigned_agent=self.agent,
            title="Research the market and competitors",
            status=PersistentAgentKanbanCard.Status.DOING,
            priority=2,
        )
        PersistentAgentKanbanCard.objects.create(
            assigned_agent=self.agent,
            title="Write investment memo",
            status=PersistentAgentKanbanCard.Status.TODO,
            priority=1,
        )

        result = build_redundant_research_plan_skip_result(
            self.agent,
            {
                "plan": [
                    {"step": "Research source set", "status": "done"},
                    {"step": "Synthesize investment memo", "status": "todo"},
                ],
                "will_continue_work": True,
            },
        )

        self.assertIsNone(result)

    def test_old_outbound_message_does_not_turn_redundant_plan_skip_into_sleep(self):
        agent_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.WEB,
            address="plan-research-agent",
            is_primary=True,
        )
        user_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.WEB,
            address="plan-research-user",
        )
        conversation = PersistentAgentConversation.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.WEB,
            address=user_endpoint.address,
        )
        PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            conversation=conversation,
            from_endpoint=user_endpoint,
            is_outbound=False,
            body="Previous run request.",
        )
        PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            conversation=conversation,
            from_endpoint=agent_endpoint,
            is_outbound=True,
            body="Previous run final report.",
        )
        PersistentAgentKanbanCard.objects.create(
            assigned_agent=self.agent,
            title="Research source set",
            status=PersistentAgentKanbanCard.Status.DOING,
            priority=2,
        )
        PersistentAgentKanbanCard.objects.create(
            assigned_agent=self.agent,
            title="Synthesize investment memo",
            status=PersistentAgentKanbanCard.Status.TODO,
            priority=1,
        )

        result = build_redundant_research_plan_skip_result(
            self.agent,
            {
                "plan": [
                    {"step": "Research source set", "status": "doing"},
                    {"step": "Synthesize investment memo", "status": "todo"},
                ],
                "will_continue_work": True,
            },
        )

        self.assertIsNotNone(result)
        self.assertTrue(result["skipped"])
        self.assertFalse(result["auto_sleep_ok"])

    def test_all_done_update_for_unfinished_research_plan_is_not_skipped(self):
        PersistentAgentKanbanCard.objects.create(
            assigned_agent=self.agent,
            title="Research source set",
            status=PersistentAgentKanbanCard.Status.DOING,
            priority=2,
        )
        PersistentAgentKanbanCard.objects.create(
            assigned_agent=self.agent,
            title="Synthesize investment memo",
            status=PersistentAgentKanbanCard.Status.TODO,
            priority=1,
        )

        result = build_redundant_research_plan_skip_result(
            self.agent,
            {
                "plan": [
                    {"step": "Research source set", "status": "done"},
                    {"step": "Synthesize investment memo", "status": "done"},
                ],
                "will_continue_work": False,
            },
        )

        self.assertIsNone(result)

    def test_changed_research_plan_is_not_skipped(self):
        PersistentAgentKanbanCard.objects.create(
            assigned_agent=self.agent,
            title="Research source set",
            status=PersistentAgentKanbanCard.Status.DOING,
            priority=1,
        )

        result = build_redundant_research_plan_skip_result(
            self.agent,
            {
                "plan": [
                    {"step": "Research source set", "status": "done"},
                    {"step": "Compare competitors", "status": "todo"},
                ],
                "will_continue_work": True,
            },
        )

        self.assertIsNone(result)
