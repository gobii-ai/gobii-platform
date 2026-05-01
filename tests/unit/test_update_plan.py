from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.agent.tools.plan import execute_update_plan
from api.models import (
    BrowserUseAgent,
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentConversation,
    PersistentAgentKanbanCard,
    PersistentAgentMessage,
    PersistentAgentPlanDeliverable,
)


@tag("batch_sqlite")
class UpdatePlanToolTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="update-plan@example.com",
            email="update-plan@example.com",
            password="secret",
        )
        self.browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="Plan Browser",
        )
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Plan Agent",
            charter="Plan charter",
            browser_use_agent=self.browser_agent,
        )
        self.endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=None,
            channel=CommsChannel.WEB,
            address="web:test",
        )
        self.conversation = PersistentAgentConversation.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.WEB,
            address="web:test",
        )
        self.message = PersistentAgentMessage.objects.create(
            is_outbound=True,
            from_endpoint=self.endpoint,
            conversation=self.conversation,
            body="Delivered report",
            owner_agent=self.agent,
        )

    def test_update_plan_replaces_steps_and_persists_deliverables(self):
        result = execute_update_plan(
            self.agent,
            {
                "plan": [
                    {"step": "Research sources", "status": "doing"},
                    {"step": "Deliver report", "status": "todo"},
                ],
                "files": [{"path": "/exports/report.csv", "label": "Report CSV"}],
                "messages": [{"message_id": str(self.message.id), "label": "Report message"}],
            },
        )

        self.assertEqual(result["status"], "ok")
        cards = list(PersistentAgentKanbanCard.objects.filter(assigned_agent=self.agent).order_by("-priority"))
        self.assertEqual([card.title for card in cards], ["Research sources", "Deliver report"])
        self.assertEqual(cards[0].status, PersistentAgentKanbanCard.Status.DOING)
        self.assertEqual(cards[1].status, PersistentAgentKanbanCard.Status.TODO)
        deliverables = list(PersistentAgentPlanDeliverable.objects.filter(agent=self.agent).order_by("position"))
        self.assertEqual(len(deliverables), 2)
        self.assertEqual(deliverables[0].path, "/exports/report.csv")
        self.assertEqual(deliverables[0].label, "Report CSV")
        self.assertEqual(deliverables[1].message_id, self.message.id)

    def test_update_plan_rejects_multiple_doing_without_changing_state(self):
        existing = PersistentAgentKanbanCard.objects.create(
            assigned_agent=self.agent,
            title="Existing step",
            status=PersistentAgentKanbanCard.Status.TODO,
        )

        result = execute_update_plan(
            self.agent,
            {
                "plan": [
                    {"step": "First", "status": "doing"},
                    {"step": "Second", "status": "doing"},
                ],
            },
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("At most one plan step may be doing.", result["errors"])
        self.assertEqual(
            list(PersistentAgentKanbanCard.objects.filter(assigned_agent=self.agent).order_by("created_at").values_list("id", flat=True)),
            [existing.id],
        )

    def test_update_plan_matches_by_normalized_step_text_and_deletes_missing(self):
        keep = PersistentAgentKanbanCard.objects.create(
            assigned_agent=self.agent,
            title="  Research   sources ",
            status=PersistentAgentKanbanCard.Status.TODO,
            priority=1,
        )
        remove = PersistentAgentKanbanCard.objects.create(
            assigned_agent=self.agent,
            title="Remove me",
            status=PersistentAgentKanbanCard.Status.TODO,
            priority=2,
        )

        result = execute_update_plan(
            self.agent,
            {
                "plan": [
                    {"step": "research sources", "status": "done"},
                    {"step": "Send report", "status": "todo"},
                ],
            },
        )

        self.assertEqual(result["status"], "ok")
        keep.refresh_from_db()
        self.assertEqual(keep.title, "research sources")
        self.assertEqual(keep.status, PersistentAgentKanbanCard.Status.DONE)
        self.assertIsNotNone(keep.completed_at)
        self.assertFalse(PersistentAgentKanbanCard.objects.filter(id=remove.id).exists())
        self.assertTrue(PersistentAgentKanbanCard.objects.filter(assigned_agent=self.agent, title="Send report").exists())
