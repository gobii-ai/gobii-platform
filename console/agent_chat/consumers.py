from __future__ import annotations

import logging

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.core.exceptions import PermissionDenied

from console.agent_chat.access import resolve_agent


logger = logging.getLogger(__name__)


class AgentChatConsumer(AsyncJsonWebsocketConsumer):
    """Realtime channel for persistent agent timeline updates."""

    async def connect(self):
        user = self.scope.get("user")
        session = self.scope.get("session")
        if user is None or not getattr(user, "is_authenticated", False):
            logger.warning("AgentChatConsumer rejected unauthenticated connection")
            await self.close(code=4401)
            return

        agent_id = self.scope.get("url_route", {}).get("kwargs", {}).get("agent_id")
        if not agent_id:
            logger.warning("AgentChatConsumer missing agent_id in path")
            await self.close(code=4404)
            return
        self.agent_id = str(agent_id)

        try:
            self.agent = await self._resolve_agent(user, session, self.agent_id)
        except PermissionDenied as exc:
            logger.warning("AgentChatConsumer permission denied for user %s agent %s: %s", user, self.agent_id, exc)
            await self.close(code=4403)
            return

        self.group_name = f"agent-chat-{self.agent_id}"
        if self.channel_layer is None:
            logger.error("AgentChatConsumer cannot attach to channel layer (not configured)")
            await self.close(code=1011)
            return
        try:
            await self.channel_layer.group_add(self.group_name, self.channel_name)
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.exception(
                "AgentChatConsumer failed to join group; channel layer unavailable (agent=%s): %s",
                self.agent_id,
                exc,
            )
            await self.close(code=1011)
            return
        logger.info("AgentChatConsumer connected user=%s agent=%s channel=%s", user, self.agent_id, self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        if hasattr(self, "group_name") and self.channel_layer is not None:
            logger.info("AgentChatConsumer disconnect agent=%s channel=%s code=%s", getattr(self, "agent_id", None), self.channel_name, code)
            try:
                await self.channel_layer.group_discard(self.group_name, self.channel_name)
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.exception("AgentChatConsumer failed removing channel from group: %s", exc)

    async def receive_json(self, content, **kwargs):
        # Basic ping/pong support for client health checks
        if content.get("type") == "ping":
            await self.send_json({"type": "pong"})

    async def timeline_event(self, event):
        await self.send_json({"type": "timeline.event", "payload": event.get("payload")})

    async def processing_event(self, event):
        await self.send_json({"type": "processing", "payload": event.get("payload")})

    @database_sync_to_async
    def _resolve_agent(self, user, session, agent_id):
        return resolve_agent(user, session, agent_id)


class EchoConsumer(AsyncJsonWebsocketConsumer):
    """Simple echo consumer kept for diagnostics page compatibility."""

    async def connect(self):
        user = self.scope.get("user")
        if user is None or not getattr(user, "is_authenticated", False):
            await self.close(code=4401)
            return
        await self.accept()

    async def receive_json(self, content, **kwargs):
        await self.send_json(content)
