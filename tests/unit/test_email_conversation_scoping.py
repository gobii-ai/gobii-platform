from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.agent.comms.adapters import ParsedMessage
from api.agent.comms.message_service import ingest_inbound_message
from api.models import (
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentConversation,
    PersistentAgentConversationParticipant,
    BrowserUseAgent,
    CommsChannel,
)
from config import settings


User = get_user_model()


@tag("batch_email")
class EmailConversationScopingTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="owner",
            email="owner@example.com",
            password="pw",
        )
        self.browser_agent_a = BrowserUseAgent.objects.create(user=self.owner, name="AgentBrowserA")
        self.browser_agent_b = BrowserUseAgent.objects.create(user=self.owner, name="AgentBrowserB")

        self.agent_a = PersistentAgent.objects.create(
            user=self.owner,
            name="Agent A",
            charter="charter a",
            browser_use_agent=self.browser_agent_a,
        )
        self.agent_b = PersistentAgent.objects.create(
            user=self.owner,
            name="Agent B",
            charter="charter b",
            browser_use_agent=self.browser_agent_b,
        )

        default_domain = settings.DEFAULT_AGENT_EMAIL_DOMAIN
        self.agent_a_email = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent_a,
            channel=CommsChannel.EMAIL,
            address=f"agent-a@{default_domain}",
            is_primary=True,
        )
        self.agent_b_email = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent_b,
            channel=CommsChannel.EMAIL,
            address=f"agent-b@{default_domain}",
            is_primary=True,
        )
        self.sender = self.owner.email

    @tag("batch_email")
    @patch("api.agent.tasks.process_agent_events_task.delay")
    @patch("tasks.services.TaskCreditService.calculate_available_tasks", return_value=10)
    def test_conversations_scope_by_target_endpoint(self, mock_calc, mock_delay):
        first = ParsedMessage(
            sender=self.sender,
            recipient=self.agent_a_email.address,
            subject="Status",
            body="Hello agent A",
            attachments=[],
            raw_payload={"provider": "test"},
            msg_channel=CommsChannel.EMAIL,
        )
        second = ParsedMessage(
            sender=self.sender,
            recipient=self.agent_b_email.address,
            subject="Status",
            body="Hello agent B",
            attachments=[],
            raw_payload={"provider": "test"},
            msg_channel=CommsChannel.EMAIL,
        )

        ingest_inbound_message(CommsChannel.EMAIL, first)
        ingest_inbound_message(CommsChannel.EMAIL, second)

        conversations = PersistentAgentConversation.objects.filter(
            channel=CommsChannel.EMAIL,
            address=self.sender,
        )
        self.assertEqual(conversations.count(), 2)

        conv_a = conversations.filter(owner_agent=self.agent_a).first()
        conv_b = conversations.filter(owner_agent=self.agent_b).first()

        self.assertIsNotNone(conv_a)
        self.assertIsNotNone(conv_b)

        agent_participants_a = set(
            conv_a.participants.filter(
                role=PersistentAgentConversationParticipant.ParticipantRole.AGENT
            ).values_list("endpoint_id", flat=True)
        )
        agent_participants_b = set(
            conv_b.participants.filter(
                role=PersistentAgentConversationParticipant.ParticipantRole.AGENT
            ).values_list("endpoint_id", flat=True)
        )

        self.assertEqual(agent_participants_a, {self.agent_a_email.id})
        self.assertEqual(agent_participants_b, {self.agent_b_email.id})

        user_endpoints_a = set(
            PersistentAgentCommsEndpoint.objects.filter(
                conversation_memberships__conversation__owner_agent=self.agent_a,
                owner_agent__isnull=True,
            ).values_list("address", flat=True)
        )
        self.assertIn(self.sender, user_endpoints_a)
        self.assertNotIn(self.agent_b_email.address, user_endpoints_a)

        self.assertEqual(mock_delay.call_count, 2)
