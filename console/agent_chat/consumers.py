from __future__ import annotations

import logging

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.core.exceptions import PermissionDenied

from console.agent_chat.access import resolve_agent
from console.agent_chat.realtime import user_stream_group_name


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
        self.user_group_name = user_stream_group_name(self.agent_id, user.id)
        if self.channel_layer is None:
            logger.error("AgentChatConsumer cannot attach to channel layer (not configured)")
            await self.close(code=1011)
            return
        try:
            await self.channel_layer.group_add(self.group_name, self.channel_name)
            await self.channel_layer.group_add(self.user_group_name, self.channel_name)
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
                await self.channel_layer.group_discard(self.user_group_name, self.channel_name)
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

    async def stream_event(self, event):
        await self.send_json({"type": "stream.event", "payload": event.get("payload")})

    @database_sync_to_async
    def _resolve_agent(self, user, session, agent_id):
        return resolve_agent(user, session, agent_id)


class AgentChatSessionConsumer(AsyncJsonWebsocketConsumer):
    """Realtime channel for persistent agent updates with a session-level connection."""

    async def connect(self):
        user = self.scope.get("user")
        session = self.scope.get("session")
        if user is None or not getattr(user, "is_authenticated", False):
            logger.warning("AgentChatSessionConsumer rejected unauthenticated connection")
            await self.close(code=4401)
            return

        self.user = user
        self.session = session
        self.agent_id = None
        self.group_name = None
        self.user_group_name = None

        if self.channel_layer is None:
            logger.error("AgentChatSessionConsumer cannot attach to channel layer (not configured)")
            await self.close(code=1011)
            return

        logger.info("AgentChatSessionConsumer connected user=%s channel=%s", user, self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        await self._clear_subscription()
        logger.info(
            "AgentChatSessionConsumer disconnect user=%s channel=%s code=%s",
            getattr(self, "user", None),
            self.channel_name,
            code,
        )

    async def receive_json(self, content, **kwargs):
        message_type = content.get("type")
        if message_type == "ping":
            await self.send_json({"type": "pong"})
            return
        if message_type == "subscribe":
            agent_id = content.get("agent_id")
            if not agent_id:
                await self.send_json({"type": "subscription.error", "message": "agent_id is required"})
                return
            await self._subscribe(str(agent_id))
            return
        if message_type == "unsubscribe":
            agent_id = content.get("agent_id")
            await self._unsubscribe(str(agent_id) if agent_id else None)

    async def timeline_event(self, event):
        await self.send_json({"type": "timeline.event", "payload": event.get("payload")})

    async def processing_event(self, event):
        await self.send_json({"type": "processing", "payload": event.get("payload")})

    async def stream_event(self, event):
        await self.send_json({"type": "stream.event", "payload": event.get("payload")})

    async def _subscribe(self, agent_id: str) -> None:
        if self.agent_id == agent_id:
            return

        await self._clear_subscription()

        try:
            await self._resolve_agent(self.user, self.session, agent_id)
        except PermissionDenied as exc:
            logger.warning(
                "AgentChatSessionConsumer permission denied for user %s agent %s: %s",
                self.user,
                agent_id,
                exc,
            )
            await self.send_json(
                {
                    "type": "subscription.error",
                    "agent_id": agent_id,
                    "message": "Permission denied for agent subscription.",
                }
            )
            return

        self.agent_id = agent_id
        self.group_name = f"agent-chat-{agent_id}"
        self.user_group_name = user_stream_group_name(agent_id, self.user.id)
        try:
            await self.channel_layer.group_add(self.group_name, self.channel_name)
            await self.channel_layer.group_add(self.user_group_name, self.channel_name)
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.exception(
                "AgentChatSessionConsumer failed to join group; channel layer unavailable (agent=%s): %s",
                agent_id,
                exc,
            )
            await self.send_json(
                {
                    "type": "subscription.error",
                    "agent_id": agent_id,
                    "message": "Failed to join agent realtime group.",
                }
            )
            await self._clear_subscription()
            return

        logger.info(
            "AgentChatSessionConsumer subscribed user=%s agent=%s channel=%s",
            self.user,
            agent_id,
            self.channel_name,
        )

    async def _unsubscribe(self, agent_id: str | None) -> None:
        if agent_id and self.agent_id and agent_id != self.agent_id:
            return
        await self._clear_subscription()

    async def _clear_subscription(self) -> None:
        if self.channel_layer is None:
            return
        if self.group_name:
            try:
                await self.channel_layer.group_discard(self.group_name, self.channel_name)
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.exception("AgentChatSessionConsumer failed removing channel from group: %s", exc)
        if self.user_group_name:
            try:
                await self.channel_layer.group_discard(self.user_group_name, self.channel_name)
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.exception("AgentChatSessionConsumer failed removing channel from group: %s", exc)
        self.agent_id = None
        self.group_name = None
        self.user_group_name = None

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
        """Mirror the payload using the legacy diagnostic echo format."""

        await self.send_json({"you_sent": content})
