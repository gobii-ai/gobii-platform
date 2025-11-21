"""ASGI entrypoint wiring HTTP + WebSocket support via Django Channels."""

from __future__ import annotations

import os

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application
from django.urls import path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

django_asgi_app = get_asgi_application()


from console.agent_chat.consumers import AgentChatConsumer, EchoConsumer  # noqa: E402  pylint: disable=wrong-import-position
from console.evals.consumers import EvalRunConsumer, EvalSuiteRunConsumer  # noqa: E402  pylint: disable=wrong-import-position


websocket_urlpatterns = [
    path("ws/agents/<uuid:agent_id>/chat/", AgentChatConsumer.as_asgi()),
    path("ws/echo/", EchoConsumer.as_asgi()),
    path("ws/evals/suites/<uuid:suite_run_id>/", EvalSuiteRunConsumer.as_asgi()),
    path("ws/evals/runs/<uuid:run_id>/", EvalRunConsumer.as_asgi()),
]


application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": AuthMiddlewareStack(URLRouter(websocket_urlpatterns)),
    }
)
