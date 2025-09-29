"""ASGI entrypoint wiring HTTP + WebSocket support via Django Channels."""

from __future__ import annotations

import os

from channels.auth import AuthMiddlewareStack
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application
from django.urls import path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

django_asgi_app = get_asgi_application()


class EchoConsumer(AsyncJsonWebsocketConsumer):
    """Minimal authenticated echo consumer for smoke-testing WebSockets."""

    async def connect(self) -> None:
        user = self.scope.get("user")
        if user and getattr(user, "is_authenticated", False):
            await self.accept()
        else:
            await self.close(code=4401)

    async def receive_json(self, content, **kwargs):
        await self.send_json({"you_sent": content})


websocket_urlpatterns = [
    path("ws/echo/", EchoConsumer.as_asgi()),
]


application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": AuthMiddlewareStack(URLRouter(websocket_urlpatterns)),
    }
)
