from __future__ import annotations

from types import SimpleNamespace

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings, tag
from unittest.mock import patch

from api.models import BrowserUseAgent, Organization, OrganizationMembership, PersistentAgent
from app_api.auth import create_native_app_session

from config.asgi import application


CHANNEL_LAYER_SETTINGS = {
    "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}
}


@tag("batch_websocket")
class EchoConsumerTests(SimpleTestCase):
    """Exercise the minimal authenticated echo consumer configured in ASGI."""

    def test_agent_chat_session_rejects_anonymous_user_without_disconnect_error(self) -> None:
        async def _run():
            communicator = WebsocketCommunicator(application, "/ws/agents/chat/")
            connected, _ = await communicator.connect()
            self.assertFalse(connected)
            await communicator.disconnect()

        async_to_sync(_run)()


@override_settings(CHANNEL_LAYERS=CHANNEL_LAYER_SETTINGS)
@tag("batch_websocket")
class NativeAppWebsocketTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.user = user_model.objects.create_user(
            username="native-ws-user",
            email="native-ws-user@example.com",
            password="password123",
        )
        cls.organization = Organization.objects.create(
            name="Websocket Org",
            slug="websocket-org",
            created_by=cls.user,
        )
        billing = cls.organization.billing
        billing.purchased_seats = 3
        billing.save(update_fields=["purchased_seats"])
        OrganizationMembership.objects.create(
            org=cls.organization,
            user=cls.user,
            role=OrganizationMembership.OrgRole.OWNER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        cls.browser_agent = BrowserUseAgent.objects.create(user=cls.user, name="Websocket Browser Agent")
        cls.agent = PersistentAgent.objects.create(
            user=cls.user,
            organization=cls.organization,
            name="Websocket Org Agent",
            charter="Stream websocket events.",
            browser_use_agent=cls.browser_agent,
        )

    def test_native_app_socket_rejects_missing_bearer_token(self):
        async def _run():
            communicator = WebsocketCommunicator(application, "/ws/app/v1/chat/")
            connected, _ = await communicator.connect()
            self.assertFalse(connected)
            await communicator.disconnect()

        async_to_sync(_run)()

    def test_native_app_socket_accepts_bearer_token_and_subscribes_with_context(self):
        credentials = create_native_app_session(self.user)
        captured_contexts = []

        from console.agent_chat.consumers import AgentChatSessionConsumer
        from console.agent_chat.access import resolve_agent

        async def _capturing_resolve_agent(self, user, session, agent_id, context_override=None):
            captured_contexts.append(context_override)
            return await database_sync_to_async(resolve_agent)(
                user,
                session,
                agent_id,
                allow_shared=True,
                allow_delinquent_personal_chat=True,
                context_override=context_override,
            )

        with patch.object(AgentChatSessionConsumer, "_resolve_agent", new=_capturing_resolve_agent):
            async def _run():
                communicator = WebsocketCommunicator(
                    application,
                    "/ws/app/v1/chat/",
                    headers=[
                        (b"authorization", f"Bearer {credentials.access_token}".encode("utf-8")),
                    ],
                )
                connected, _ = await communicator.connect()
                self.assertTrue(connected)

                await communicator.send_json_to(
                    {
                        "type": "subscribe",
                        "agent_id": str(self.agent.id),
                        "context": {
                            "type": "organization",
                            "id": str(self.organization.id),
                        },
                    }
                )
                await communicator.send_json_to({"type": "ping"})
                self.assertEqual(await communicator.receive_json_from(timeout=1), {"type": "pong"})
                await communicator.disconnect()

            async_to_sync(_run)()

        self.assertEqual(
            captured_contexts,
            [{"type": "organization", "id": str(self.organization.id)}],
        )

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
