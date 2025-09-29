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
