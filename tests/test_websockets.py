from __future__ import annotations

from types import SimpleNamespace

from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.test import SimpleTestCase, tag

from config.asgi import application


@tag("batch_websocket")
class EchoConsumerTests(SimpleTestCase):
    """Exercise the minimal authenticated echo consumer configured in ASGI."""

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


@tag("batch_agent_chat")
class AgentChatSessionConsumerTests(SimpleTestCase):
    """Verify AgentChatSessionConsumer early-rejection paths don't raise AttributeError."""

    def test_rejects_unauthenticated_connection_without_attribute_error(self) -> None:
        # Regression test for Bug #103: disconnect() must not crash because
        # subscription attributes were never initialized before the auth check.
        async def _run():
            communicator = WebsocketCommunicator(application, "/ws/agents/chat/")
            # No user in scope → consumer rejects with code 4401.
            connected, code = await communicator.connect()
            self.assertFalse(connected)
            self.assertEqual(code, 4401)
            await communicator.disconnect()

        async_to_sync(_run)()

    def test_rejects_anonymous_user_without_attribute_error(self) -> None:
        async def _run():
            communicator = WebsocketCommunicator(application, "/ws/agents/chat/")
            communicator.scope["user"] = SimpleNamespace(is_authenticated=False)
            connected, code = await communicator.connect()
            self.assertFalse(connected)
            self.assertEqual(code, 4401)
            await communicator.disconnect()

        async_to_sync(_run)()

    def test_rejects_when_channel_layer_missing_without_attribute_error(self) -> None:
        # When channel_layer is None the consumer closes early; subscription
        # attributes must already be initialized so _clear_subscription() is safe.
        # Test this by calling the consumer's _clear_subscription directly after
        # simulating the state that would exist at early rejection time.
        async def _run():
            from console.agent_chat.consumers import AgentChatSessionConsumer

            consumer = AgentChatSessionConsumer()
            # Simulate state after the early attribute initialization but before
            # self.channel_layer is available (i.e. right after auth rejection).
            consumer.agent_id = None
            consumer.group_name = None
            consumer.user_group_name = None
            consumer.channel_layer = None
            # This must not raise AttributeError
            await consumer._clear_subscription()

        async_to_sync(_run)()
