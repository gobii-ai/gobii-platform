from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.core.exceptions import PermissionDenied
from django.db import DatabaseError
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
    SNAPSHOT = {
        "processing_snapshot": {
            "active": True,
            "webTasks": [],
            "nextScheduledAt": None,
        },
        "pending_action_requests": [],
    }

    def _build_communicator(self, *, is_staff: bool = False) -> WebsocketCommunicator:
        communicator = WebsocketCommunicator(AgentChatSessionConsumer.as_asgi(), "/")
        communicator.scope["user"] = SimpleNamespace(
            is_authenticated=True,
            is_staff=is_staff,
            is_superuser=False,
            id=123,
        )
        communicator.scope["session"] = None
        return communicator

    async def _receive_ready(self, communicator, *, agent_id: str, mode: str):
        ready = await communicator.receive_json_from()
        self.assertEqual(
            ready,
            {
                "type": "subscription.ready",
                "agent_id": agent_id,
                "mode": mode,
                "payload": self.SNAPSHOT,
            },
        )

    @staticmethod
    async def _build_snapshot(*args, **kwargs):
        return AgentChatSessionConsumerTests.SNAPSHOT

    def test_subscription_ready_contains_authoritative_snapshot(self) -> None:
        async def _allow_subscription(*args, **kwargs):
            return SimpleNamespace(id="agent-a")

        async def _run():
            communicator = self._build_communicator()
            connected, _ = await communicator.connect()
            self.assertTrue(connected)
            try:
                await communicator.send_json_to(
                    {"type": "subscribe", "agent_id": "agent-a", "mode": "active"}
                )
                await self._receive_ready(communicator, agent_id="agent-a", mode="active")
            finally:
                await communicator.disconnect()

        with patch(
            "console.agent_chat.consumers.AgentChatSessionConsumer._resolve_agent",
            new=_allow_subscription,
        ), patch(
            "console.agent_chat.consumers.AgentChatSessionConsumer._build_subscription_snapshot",
            new=self._build_snapshot,
        ):
            async_to_sync(_run)()

    def test_group_event_queued_during_snapshot_follows_ready_frame(self) -> None:
        snapshot_started = asyncio.Event()
        release_snapshot = asyncio.Event()

        async def _allow_subscription(*args, **kwargs):
            return SimpleNamespace(id="agent-a")

        async def _delayed_snapshot(*args, **kwargs):
            snapshot_started.set()
            await release_snapshot.wait()
            return self.SNAPSHOT

        async def _run():
            communicator = self._build_communicator()
            connected, _ = await communicator.connect()
            self.assertTrue(connected)
            try:
                await communicator.send_json_to(
                    {"type": "subscribe", "agent_id": "agent-a", "mode": "active"}
                )
                await snapshot_started.wait()
                await get_channel_layer().group_send(
                    "agent-chat-agent-a",
                    {
                        "type": "timeline_event",
                        "agent_id": "agent-a",
                        "payload": {"kind": "thinking", "cursor": "1"},
                    },
                )
                release_snapshot.set()

                await self._receive_ready(communicator, agent_id="agent-a", mode="active")
                event = await communicator.receive_json_from()
                self.assertEqual(event["type"], "timeline.event")
                self.assertEqual(event["agent_id"], "agent-a")
            finally:
                await communicator.disconnect()

        with patch(
            "console.agent_chat.consumers.AgentChatSessionConsumer._resolve_agent",
            new=_allow_subscription,
        ), patch(
            "console.agent_chat.consumers.AgentChatSessionConsumer._build_subscription_snapshot",
            new=_delayed_snapshot,
        ):
            async_to_sync(_run)()

    def test_mode_change_returns_fresh_ready_snapshot(self) -> None:
        snapshots = []

        async def _allow_subscription(*args, **kwargs):
            return SimpleNamespace(id="agent-a")

        async def _snapshot(*args, **kwargs):
            snapshot = {
                **self.SNAPSHOT,
                "processing_snapshot": {
                    **self.SNAPSHOT["processing_snapshot"],
                    "active": len(snapshots) == 1,
                },
            }
            snapshots.append(snapshot)
            return snapshot

        async def _run():
            communicator = self._build_communicator()
            connected, _ = await communicator.connect()
            self.assertTrue(connected)
            try:
                await communicator.send_json_to(
                    {"type": "subscribe", "agent_id": "agent-a", "mode": "background"}
                )
                first = await communicator.receive_json_from()
                await communicator.send_json_to(
                    {"type": "subscribe", "agent_id": "agent-a", "mode": "active"}
                )
                second = await communicator.receive_json_from()
                self.assertEqual(first["mode"], "background")
                self.assertFalse(first["payload"]["processing_snapshot"]["active"])
                self.assertEqual(second["mode"], "active")
                self.assertTrue(second["payload"]["processing_snapshot"]["active"])
            finally:
                await communicator.disconnect()

        with patch(
            "console.agent_chat.consumers.AgentChatSessionConsumer._resolve_agent",
            new=_allow_subscription,
        ), patch(
            "console.agent_chat.consumers.AgentChatSessionConsumer._build_subscription_snapshot",
            new=_snapshot,
        ):
            async_to_sync(_run)()

    def test_snapshot_failure_removes_groups_and_returns_error(self) -> None:
        async def _allow_subscription(*args, **kwargs):
            return SimpleNamespace(id="agent-a")

        async def _fail_snapshot(*args, **kwargs):
            raise DatabaseError("snapshot unavailable")

        async def _run():
            communicator = self._build_communicator()
            connected, _ = await communicator.connect()
            self.assertTrue(connected)
            try:
                await communicator.send_json_to(
                    {"type": "subscribe", "agent_id": "agent-a", "mode": "active"}
                )
                error = await communicator.receive_json_from()
                self.assertEqual(error["type"], "subscription.error")
                self.assertEqual(error["agent_id"], "agent-a")
                await get_channel_layer().group_send(
                    "agent-chat-agent-a",
                    {"type": "timeline_event", "agent_id": "agent-a", "payload": {"cursor": "1"}},
                )
                self.assertTrue(await communicator.receive_nothing(timeout=0.1))
            finally:
                await communicator.disconnect()

        with patch(
            "console.agent_chat.consumers.AgentChatSessionConsumer._resolve_agent",
            new=_allow_subscription,
        ), patch(
            "console.agent_chat.consumers.AgentChatSessionConsumer._build_subscription_snapshot",
            new=_fail_snapshot,
        ):
            async_to_sync(_run)()

    def test_permission_failure_removes_existing_subscription(self) -> None:
        authorization_attempts = 0

        async def _resolve_subscription(*args, **kwargs):
            nonlocal authorization_attempts
            authorization_attempts += 1
            if authorization_attempts > 1:
                raise PermissionDenied("revoked")
            return SimpleNamespace(id="agent-a")

        async def _run():
            communicator = self._build_communicator()
            connected, _ = await communicator.connect()
            self.assertTrue(connected)
            try:
                await communicator.send_json_to(
                    {"type": "subscribe", "agent_id": "agent-a", "mode": "background"}
                )
                await self._receive_ready(communicator, agent_id="agent-a", mode="background")
                await communicator.send_json_to(
                    {"type": "subscribe", "agent_id": "agent-a", "mode": "active"}
                )
                error = await communicator.receive_json_from()
                self.assertEqual(error["type"], "subscription.error")
                await get_channel_layer().group_send(
                    "agent-chat-agent-a",
                    {"type": "timeline_event", "agent_id": "agent-a", "payload": {"cursor": "1"}},
                )
                self.assertTrue(await communicator.receive_nothing(timeout=0.1))
            finally:
                await communicator.disconnect()

        with patch(
            "console.agent_chat.consumers.AgentChatSessionConsumer._resolve_agent",
            new=_resolve_subscription,
        ), patch(
            "console.agent_chat.consumers.AgentChatSessionConsumer._build_subscription_snapshot",
            new=self._build_snapshot,
        ):
            async_to_sync(_run)()

    def test_session_consumer_only_forwards_developer_updates_to_staff(self) -> None:
        async def _allow_subscription(*args, **kwargs):
            return None

        async def _run(is_staff: bool):
            communicator = self._build_communicator(is_staff=is_staff)
            connected, _ = await communicator.connect()
            self.assertTrue(connected)
            try:
                await communicator.send_json_to(
                    {"type": "subscribe", "agent_id": "agent-a", "mode": "active"}
                )
                await self._receive_ready(communicator, agent_id="agent-a", mode="active")
                await get_channel_layer().group_send(
                    "agent-chat-agent-a",
                    {"type": "developer_event", "agent_id": "agent-a"},
                )
                if is_staff:
                    event = await communicator.receive_json_from()
                    self.assertEqual(event, {"type": "developer.updated", "agent_id": "agent-a", "payload": None})
                else:
                    self.assertTrue(await communicator.receive_nothing(timeout=0.1))
            finally:
                await communicator.disconnect()

        with patch(
            "console.agent_chat.consumers.AgentChatSessionConsumer._resolve_agent",
            new=_allow_subscription,
        ), patch(
            "console.agent_chat.consumers.AgentChatSessionConsumer._build_subscription_snapshot",
            new=self._build_snapshot,
        ):
            async_to_sync(_run)(False)
            async_to_sync(_run)(True)

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
                await self._receive_ready(communicator, agent_id="agent-a", mode="active")
                await communicator.send_json_to(
                    {"type": "subscribe", "agent_id": "agent-b", "mode": "background"}
                )
                await self._receive_ready(communicator, agent_id="agent-b", mode="background")

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
        ), patch(
            "console.agent_chat.consumers.AgentChatSessionConsumer._build_subscription_snapshot",
            new=self._build_snapshot,
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
                await self._receive_ready(communicator, agent_id="agent-a", mode="active")
                await communicator.send_json_to(
                    {"type": "subscribe", "agent_id": "agent-b", "mode": "background"}
                )
                await self._receive_ready(communicator, agent_id="agent-b", mode="background")

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
        ), patch(
            "console.agent_chat.consumers.AgentChatSessionConsumer._build_subscription_snapshot",
            new=self._build_snapshot,
        ):
            async_to_sync(_run)()

    def test_session_consumer_forwards_usage_updates_to_subscribed_agents(self) -> None:
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
                await self._receive_ready(communicator, agent_id="agent-a", mode="active")

                await channel_layer.group_send(
                    "agent-chat-agent-a",
                    {
                        "type": "usage_update_event",
                        "agent_id": "agent-a",
                        "payload": {
                            "agent_id": "agent-a",
                            "insight_type": "burn_rate",
                            "metadata": {
                                "agentName": "Agent A",
                                "todayUsage": {
                                    "used": 2,
                                    "limit": 10,
                                    "percentUsed": 20,
                                    "unlimited": False,
                                },
                                "monthUsage": {
                                    "used": 20,
                                    "limit": 100,
                                    "percentUsed": 20,
                                    "unlimited": False,
                                },
                            },
                        },
                    },
                )

                event = await communicator.receive_json_from()
                self.assertEqual(event["type"], "usage.updated")
                self.assertEqual(event["agent_id"], "agent-a")
                self.assertEqual(event["payload"]["metadata"]["todayUsage"]["used"], 2)
            finally:
                await communicator.disconnect()

        with patch(
            "console.agent_chat.consumers.AgentChatSessionConsumer._resolve_agent",
            new=_allow_subscription,
        ), patch(
            "console.agent_chat.consumers.AgentChatSessionConsumer._build_subscription_snapshot",
            new=self._build_snapshot,
        ):
            async_to_sync(_run)()

    def test_session_consumer_forwards_message_notifications_from_profile_group(self) -> None:
        async def _run():
            communicator = self._build_communicator()
            connected, _ = await communicator.connect()
            self.assertTrue(connected)

            channel_layer = get_channel_layer()
            self.assertIsNotNone(channel_layer)

            try:
                await channel_layer.group_send(
                    "agent-chat-user-123",
                    {
                        "type": "message_notification_event",
                        "payload": {
                            "agent_id": "agent-z",
                            "agent_name": "Agent Z",
                            "agent_avatar_url": "https://example.com/avatar.png",
                            "workspace": {
                                "type": "organization",
                                "id": "org-1",
                            },
                            "message": {
                                "id": "message-1",
                                "body_preview": "Hello from Agent Z",
                                "timestamp": "2026-04-28T12:00:00Z",
                                "channel": "web",
                            },
                        },
                    },
                )

                event = await communicator.receive_json_from()
                self.assertEqual(event["type"], "message.notification")
                self.assertEqual(event["payload"]["agent_id"], "agent-z")
                self.assertEqual(event["payload"]["message"]["id"], "message-1")
            finally:
                await communicator.disconnect()

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
