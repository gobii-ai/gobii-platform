from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.test import SimpleTestCase, override_settings, tag

from console.agent_chat.consumers import AgentChatSessionConsumer, AgentChatSubscription
from config.asgi import application

CHANNEL_LAYER_SETTINGS = {
    "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}
}


@tag("batch_websocket")
class EchoConsumerTests(SimpleTestCase):
    """Exercise the minimal authenticated echo consumer configured in ASGI."""

    def test_agent_chat_session_rejects_anonymous_user_without_disconnect_error(self) -> None:
        async def _run():
            communicator = WebsocketCommunicator(AgentChatSessionConsumer.as_asgi(), "/")
            connected, _ = await communicator.connect()
            self.assertFalse(connected)

        async_to_sync(_run)()


@override_settings(CHANNEL_LAYERS=CHANNEL_LAYER_SETTINGS)
@tag("batch_websocket")
class AgentChatSessionConsumerTests(SimpleTestCase):
    def _build_communicator(self) -> WebsocketCommunicator:
        communicator = WebsocketCommunicator(AgentChatSessionConsumer.as_asgi(), "/")
        communicator.scope["user"] = SimpleNamespace(is_authenticated=True, id=123)
        communicator.scope["session"] = None
        return communicator

    def test_session_consumer_retains_multiple_subscriptions_and_targeted_unsubscribe(self) -> None:
        async def _allow_subscription(*args, **kwargs):
            return None

        async def _run():
            communicator = self._build_communicator()
            connected, _ = await communicator.connect()
            self.assertTrue(connected)

            channel_layer = get_channel_layer()
            self.assertIsNotNone(channel_layer)

            try:
                await communicator.send_json_to(
                    {"type": "subscribe", "agent_id": "agent-a", "mode": "active"}
                )
                await communicator.send_json_to(
                    {"type": "subscribe", "agent_id": "agent-b", "mode": "background"}
                )
                await asyncio.sleep(0.01)

                await channel_layer.group_send(
                    "agent-chat-agent-a",
                    {
                        "type": "timeline_event",
                        "agent_id": "agent-a",
                        "payload": {"kind": "thinking", "cursor": "1", "reasoning": "active"},
                    },
                )
                await channel_layer.group_send(
                    "agent-chat-agent-b",
                    {
                        "type": "timeline_event",
                        "agent_id": "agent-b",
                        "payload": {"kind": "thinking", "cursor": "2", "reasoning": "background"},
                    },
                )

                first = await communicator.receive_json_from()
                second = await communicator.receive_json_from()

                self.assertEqual(first["type"], "timeline.event")
                self.assertEqual(first["agent_id"], "agent-a")
                self.assertEqual(second["type"], "timeline.event")
                self.assertEqual(second["agent_id"], "agent-b")

                await communicator.send_json_to({"type": "unsubscribe", "agent_id": "agent-b"})
                await asyncio.sleep(0.01)
                await channel_layer.group_send(
                    "agent-chat-agent-b",
                    {
                        "type": "timeline_event",
                        "agent_id": "agent-b",
                        "payload": {"kind": "thinking", "cursor": "3", "reasoning": "ignored"},
                    },
                )
                self.assertTrue(await communicator.receive_nothing(timeout=0.1))
            finally:
                await communicator.disconnect()

        with patch(
            "console.agent_chat.consumers.AgentChatSessionConsumer._resolve_agent",
            new=_allow_subscription,
        ):
            async_to_sync(_run)()

    def test_session_consumer_suppresses_background_stream_events(self) -> None:
        async def _allow_subscription(*args, **kwargs):
            return None

        async def _run():
            communicator = self._build_communicator()
            connected, _ = await communicator.connect()
            self.assertTrue(connected)

            channel_layer = get_channel_layer()
            self.assertIsNotNone(channel_layer)

            try:
                await communicator.send_json_to(
                    {"type": "subscribe", "agent_id": "agent-a", "mode": "active"}
                )
                await communicator.send_json_to(
                    {"type": "subscribe", "agent_id": "agent-b", "mode": "background"}
                )
                await asyncio.sleep(0.01)

                await channel_layer.group_send(
                    "agent-chat-agent-a-user-123",
                    {
                        "type": "stream_event",
                        "agent_id": "agent-a",
                        "payload": {"stream_id": "stream-a", "status": "delta", "content_delta": "hello"},
                    },
                )
                active_stream = await communicator.receive_json_from()
                self.assertEqual(active_stream["type"], "stream.event")
                self.assertEqual(active_stream["agent_id"], "agent-a")

                await channel_layer.group_send(
                    "agent-chat-agent-b-user-123",
                    {
                        "type": "stream_event",
                        "agent_id": "agent-b",
                        "payload": {"stream_id": "stream-b", "status": "delta", "content_delta": "skip"},
                    },
                )
                self.assertTrue(await communicator.receive_nothing(timeout=0.1))

                await channel_layer.group_send(
                    "agent-chat-agent-b-user-123",
                    {
                        "type": "pending_action_requests_event",
                        "agent_id": "agent-b",
                        "payload": {
                            "agent_id": "agent-b",
                            "pending_action_requests": [],
                            "timestamp": "2026-04-23T12:00:00Z",
                        },
                    },
                )
                pending_actions = await communicator.receive_json_from()
                self.assertEqual(pending_actions["type"], "pending_action_requests.updated")
                self.assertEqual(pending_actions["agent_id"], "agent-b")
            finally:
                await communicator.disconnect()

        with patch(
            "console.agent_chat.consumers.AgentChatSessionConsumer._resolve_agent",
            new=_allow_subscription,
        ):
            async_to_sync(_run)()

    def test_remove_subscription_clears_state_without_channel_layer(self) -> None:
        consumer = AgentChatSessionConsumer()
        consumer.channel_layer = None
        consumer.subscriptions = {
            "agent-a": AgentChatSubscription(
                group_name="agent-chat-agent-a",
                user_group_name="agent-chat-agent-a-user-123",
            )
        }
        consumer.active_agent_id = "agent-a"

        async_to_sync(consumer._remove_subscription)("agent-a")

        self.assertEqual(consumer.subscriptions, {})
        self.assertIsNone(consumer.active_agent_id)

    def test_rejects_anonymous_user(self) -> None:
        async def _run():
            communicator = WebsocketCommunicator(application, "/ws/echo/")
            connected, _ = await communicator.connect()
            self.assertFalse(connected)
            await communicator.disconnect()

        async_to_sync(_run)()

    def test_echoes_authenticated_payload(self) -> None:
        async def _run():
            communicator = WebsocketCommunicator(application, "/ws/echo/")
            communicator.scope["user"] = SimpleNamespace(is_authenticated=True)
            connected, _ = await communicator.connect()
            self.assertTrue(connected)

            await communicator.send_json_to({"ping": "pong"})
            self.assertEqual(
                await communicator.receive_json_from(),
                {"you_sent": {"ping": "pong"}},
            )

            await communicator.disconnect()

        async_to_sync(_run)()
