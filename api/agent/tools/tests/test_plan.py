from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, tag

from api.agent.tools.plan import (
    build_redundant_research_plan_skip_result,
    execute_update_plan,
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

        self.assertIn("substantial final deliveries", messages_description)
        self.assertIn("exact message_id", messages_description)
        self.assertIn("never a peer message", messages_description)
        self.assertIn("will_continue_work=true", messages_description)
        self.assertIn("Exact user-facing send-tool message_id", message_id_description)
        self.assertIn("no placeholders", message_id_description)

    def test_tool_description_guides_plan_reset_for_new_iterations(self):
        tool = get_update_plan_tool()
        description = tool["function"]["description"]

        self.assertIn("replaces all plan and deliverable entries", description)
        self.assertIn("3-6 current, verifiable steps", description)
        self.assertIn("omit stale work", description)
        self.assertIn("recurrence-by-recurrence entries", description)

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
        self.assertIn("substantial final deliveries", result["message"])
        self.assertIn("exact message_id from a user-facing send tool", result["message"])
        self.assertIn("never a peer message", result["message"])
        self.assertIn("will_continue_work=true", result["message"])


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
