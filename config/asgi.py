"""ASGI entrypoint wiring HTTP + WebSocket support via Django Channels."""

from __future__ import annotations

import os

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application
from django.urls import path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

django_asgi_app = get_asgi_application()


from app_api.auth import AppTokenAuthMiddleware  # noqa: E402  pylint: disable=wrong-import-position
from console.agent_chat.consumers import AgentChatConsumer, AgentChatSessionConsumer, EchoConsumer  # noqa: E402  pylint: disable=wrong-import-position
from console.agent_audit.consumers import StaffAgentAuditConsumer  # noqa: E402  pylint: disable=wrong-import-position
from console.evals.consumers import EvalRunConsumer, EvalSuiteRunConsumer  # noqa: E402  pylint: disable=wrong-import-position


legacy_websocket_urlpatterns = [
    path("ws/agents/chat/", AgentChatSessionConsumer.as_asgi()),
    path("ws/agents/<uuid:agent_id>/chat/", AgentChatConsumer.as_asgi()),
    path("ws/staff/agents/<uuid:agent_id>/audit/", StaffAgentAuditConsumer.as_asgi()),
    path("ws/echo/", EchoConsumer.as_asgi()),
    path("ws/evals/suites/<uuid:suite_run_id>/", EvalSuiteRunConsumer.as_asgi()),
    path("ws/evals/runs/<uuid:run_id>/", EvalRunConsumer.as_asgi()),
]

legacy_websocket_application = AuthMiddlewareStack(URLRouter(legacy_websocket_urlpatterns))
app_websocket_application = URLRouter(
    [
        path(
            "ws/app/v1/chat/",
            AppTokenAuthMiddleware(AgentChatSessionConsumer.as_asgi()),
        ),
    ]
)


async def websocket_application(scope, receive, send):
    if scope.get("path", "").startswith("/ws/app/v1/"):
        await app_websocket_application(scope, receive, send)
        return
    await legacy_websocket_application(scope, receive, send)


application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": websocket_application,
    }
)
