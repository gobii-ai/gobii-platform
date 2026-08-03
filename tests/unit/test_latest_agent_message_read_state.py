"""Behaviour of the per-agent latest-message lookup used by the chat roster.

This had no direct coverage while carrying non-obvious exclusions -- hidden messages and peer DMs --
and it drives what every roster row shows. The tests below pin the contract so the query underneath
can be rewritten for performance without changing what it selects.
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.utils import timezone

from api.agent.comms.message_reads import build_latest_agent_message_read_state
from api.models import (
    BrowserUseAgent,
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentConversation,
    PersistentAgentMessage,
)


@tag("batch_agent_chat")
class LatestAgentMessageReadStateTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="roster-latest@example.test", email="roster-latest@example.test"
        )
        self.agent = self._agent("Alpha")
        self.other_agent = self._agent("Bravo")

    def _agent(self, name: str) -> PersistentAgent:
        browser_agent = BrowserUseAgent.objects.create(user=self.user, name=name)
        return PersistentAgent.objects.create(
            user=self.user, name=name, charter="c", browser_use_agent=browser_agent
        )

    def _message(
        self,
        agent: PersistentAgent,
        *,
        minutes_ago: int,
        outbound: bool = True,
        hidden: bool = False,
        peer_agent: PersistentAgent | None = None,
        body: str = "hello",
    ) -> PersistentAgentMessage:
        endpoint, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
            owner_agent=agent, channel=CommsChannel.EMAIL, address=f"{agent.name.lower()}@example.test"
        )
        conversation, _ = PersistentAgentConversation.objects.get_or_create(
            channel=CommsChannel.EMAIL,
            address=f"conv-{agent.id}@example.test",
            defaults={"display_name": "Conv"},
        )
        message = PersistentAgentMessage.objects.create(
            owner_agent=agent,
            from_endpoint=endpoint,
            conversation=conversation,
            is_outbound=outbound,
            body=body,
            raw_payload={"hide_in_chat": True} if hidden else {},
            peer_agent=peer_agent,
        )
        PersistentAgentMessage.objects.filter(pk=message.pk).update(
            timestamp=timezone.now() - timezone.timedelta(minutes=minutes_ago)
        )
        message.refresh_from_db()
        return message

    def _latest_id(self, agent: PersistentAgent):
        state = build_latest_agent_message_read_state([str(agent.id)], self.user)
        value = state[str(agent.id)]["latest_agent_message_id"]
        return str(value) if value is not None else None

    def test_picks_the_most_recent_outbound_message(self):
        self._message(self.agent, minutes_ago=30, body="older")
        newest = self._message(self.agent, minutes_ago=1, body="newest")

        self.assertEqual(self._latest_id(self.agent), str(newest.id))

    def test_ignores_inbound_messages(self):
        outbound = self._message(self.agent, minutes_ago=30)
        self._message(self.agent, minutes_ago=1, outbound=False, body="inbound")

        self.assertEqual(self._latest_id(self.agent), str(outbound.id))

    def test_ignores_messages_hidden_from_chat(self):
        visible = self._message(self.agent, minutes_ago=30, body="visible")
        self._message(self.agent, minutes_ago=1, hidden=True, body="hidden")

        self.assertEqual(self._latest_id(self.agent), str(visible.id))

    def test_ignores_peer_dms_by_peer_agent(self):
        visible = self._message(self.agent, minutes_ago=30)
        self._message(self.agent, minutes_ago=1, peer_agent=self.other_agent, body="peer dm")

        self.assertEqual(self._latest_id(self.agent), str(visible.id))

    def test_reports_none_for_an_agent_with_no_visible_messages(self):
        state = build_latest_agent_message_read_state([str(self.agent.id)], self.user)

        self.assertIsNone(state[str(self.agent.id)]["latest_agent_message_id"])

    def test_each_agent_gets_its_own_latest(self):
        alpha_newest = self._message(self.agent, minutes_ago=5, body="alpha")
        bravo_newest = self._message(self.other_agent, minutes_ago=1, body="bravo")

        state = build_latest_agent_message_read_state(
            [str(self.agent.id), str(self.other_agent.id)], self.user
        )

        self.assertEqual(str(state[str(self.agent.id)]["latest_agent_message_id"]), str(alpha_newest.id))
        self.assertEqual(str(state[str(self.other_agent.id)]["latest_agent_message_id"]), str(bravo_newest.id))

    def test_returns_an_entry_for_every_requested_agent(self):
        self._message(self.agent, minutes_ago=1)

        state = build_latest_agent_message_read_state(
            [str(self.agent.id), str(self.other_agent.id)], self.user
        )

        self.assertEqual(set(state), {str(self.agent.id), str(self.other_agent.id)})

    def test_query_count_does_not_grow_with_message_volume(self):
        for index in range(10):
            self._message(self.agent, minutes_ago=index + 1, body=f"alpha-{index}")
            self._message(self.other_agent, minutes_ago=index + 1, body=f"bravo-{index}")

        with CaptureQueriesContext(connection) as captured:
            state = build_latest_agent_message_read_state(
                [self.agent.id, self.other_agent.id],
                self.user,
            )

        self.assertEqual(set(state), {str(self.agent.id), str(self.other_agent.id)})
        self.assertLessEqual(len(captured), 2)
