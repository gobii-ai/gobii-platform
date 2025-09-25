from __future__ import annotations

import itertools

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.urls import reverse

from api.models import (
    BrowserUseAgent,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentMessage,
    PersistentAgentStep,
    CommsChannel,
    DeliveryStatus,
)
from api.agent.events import get_agent_event_stream_key
from tests.mocks.fake_redis import FakeRedis
from unittest.mock import patch


@tag("batch_agent_event_stream")
class AgentEventStreamTests(TestCase):
    def setUp(self):
        super().setUp()
        self.fake_redis = FakeRedis()
        self.events_patch = patch("api.agent.events.get_redis_client", return_value=self.fake_redis)
        self.views_patch = patch("api.views.get_redis_client", return_value=self.fake_redis)
        self.events_patch.start()
        self.views_patch.start()
        self.addCleanup(self.events_patch.stop)
        self.addCleanup(self.views_patch.stop)

        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="owner",
            email="owner@example.com",
            password="password123",
        )
        self.browser_agent = BrowserUseAgent.objects.create(user=self.user, name="Browser")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Helper",
            charter="Assist",
            browser_use_agent=self.browser_agent,
        )
        self.endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="agent@example.com",
            is_primary=True,
        )
        self.recipient_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.EMAIL,
            address="recipient@example.com",
        )

    def test_step_creation_emits_stream_event(self):
        stream_key = get_agent_event_stream_key(self.agent.id)

        with self.captureOnCommitCallbacks(execute=True):
            PersistentAgentStep.objects.create(agent=self.agent, description="Ran tool")

        records = self.fake_redis.xread({stream_key: "0-0"})
        self.assertEqual(len(records), 1)
        _, entries = records[0]
        self.assertEqual(len(entries), 1)
        entry_id, payload = entries[0]
        self.assertTrue(entry_id)
        self.assertEqual(payload["kind"], "step.created")
        self.assertEqual(payload["agent_id"], str(self.agent.id))
        self.assertEqual(payload["resource_id"], str(self.agent.steps.first().id))

    def test_sse_stream_delivers_new_events(self):
        url = reverse("api:agent-events-stream", args=[self.agent.id])
        self.client.force_login(self.user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        stream = iter(response.streaming_content)
        first_chunk = next(stream)
        first_text = first_chunk.decode() if isinstance(first_chunk, bytes) else first_chunk
        self.assertIn(": heartbeat", first_text)

        with self.captureOnCommitCallbacks(execute=True):
            PersistentAgentStep.objects.create(agent=self.agent, description="Follow-up")

        event_data = ""
        for chunk in itertools.islice(stream, 0, 10):
            event_data += chunk.decode() if isinstance(chunk, bytes) else chunk
            if event_data.endswith("\n\n") and "event:" in event_data:
                break

        self.assertIn("event: step.created", event_data)
        self.assertIn(str(self.agent.id), event_data)

    def test_message_status_change_notifies(self):
        stream_key = get_agent_event_stream_key(self.agent.id)

        with self.captureOnCommitCallbacks(execute=True):
            message = PersistentAgentMessage.objects.create(
                owner_agent=self.agent,
                from_endpoint=self.endpoint,
                to_endpoint=self.recipient_endpoint,
                is_outbound=True,
                body="Hello",
            )

        # Clear initial message.created event for clarity
        self.fake_redis._streams[stream_key].clear()

        with self.captureOnCommitCallbacks(execute=True):
            message.latest_status = DeliveryStatus.SENT
            message.save(update_fields=["latest_status"])

        records = self.fake_redis.xread({stream_key: "0-0"})
        self.assertEqual(len(records), 1)
        _, entries = records[0]
        self.assertEqual(entries[0][1]["kind"], "message.status_changed")
        self.assertEqual(entries[0][1]["resource_id"], str(message.id))
