from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.models import (
    AgentPeerLink,
    BrowserUseAgent,
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentConversation,
    PersistentAgentMessage,
)
from console.agent_chat.timeline import serialize_message_event


@tag("batch_agent_chat")
class TimelinePeerPayloadTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(
            username="peer-payload-card@example.test",
            email="peer-payload-card@example.test",
        )
        browser_a = BrowserUseAgent.objects.create(user=user, name="browser-a")
        browser_b = BrowserUseAgent.objects.create(user=user, name="browser-b")
        self.agent = PersistentAgent.objects.create(
            user=user,
            name="Relay",
            charter="Relay records.",
            browser_use_agent=browser_a,
        )
        self.peer = PersistentAgent.objects.create(
            user=user,
            name="Ledger",
            charter="Store records.",
            browser_use_agent=browser_b,
        )
        link = AgentPeerLink.objects.create(
            agent_a=self.agent,
            agent_b=self.peer,
            created_by=user,
        )
        endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.OTHER,
            address=f"agent://{self.agent.id}",
        )
        self.conversation = PersistentAgentConversation.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.OTHER,
            address=f"peer://{link.id}",
            is_peer_dm=True,
            peer_link=link,
        )
        self.endpoint = endpoint

    def test_peer_payload_reaches_live_chat_without_requiring_body(self):
        structured_payload = {
            "record_id": "rec-17",
            "status": "ready",
            "items": [{"sku": "A-1", "quantity": 2}],
        }
        message = PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            peer_agent=self.peer,
            from_endpoint=self.endpoint,
            conversation=self.conversation,
            is_outbound=True,
            body="",
            raw_payload={
                "_source": "agent_peer_dm",
                "structured_payload": structured_payload,
            },
        )

        payload = serialize_message_event(message)["message"]

        self.assertEqual(payload["bodyText"], "")
        self.assertTrue(payload["isPeer"])
        self.assertEqual(payload["structuredPayload"], structured_payload)

    def test_legacy_peer_message_has_no_structured_payload(self):
        message = PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            peer_agent=self.peer,
            from_endpoint=self.endpoint,
            conversation=self.conversation,
            is_outbound=True,
            body="Plain handoff",
            raw_payload={"_source": "agent_peer_dm"},
        )

        payload = serialize_message_event(message)["message"]

        self.assertIsNone(payload["structuredPayload"])
