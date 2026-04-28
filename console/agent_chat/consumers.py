import logging
from dataclasses import dataclass

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.core.exceptions import PermissionDenied

from console.agent_chat.access import resolve_agent
from console.agent_chat.realtime import user_profile_group_name, user_stream_group_name


logger = logging.getLogger(__name__)


@dataclass
class AgentChatSubscription:
    group_name: str
    user_group_name: str


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
        self.profile_group_name = user_profile_group_name(user.id)

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
            await self.channel_layer.group_add(self.profile_group_name, self.channel_name)
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
                if hasattr(self, "profile_group_name"):
                    await self.channel_layer.group_discard(self.profile_group_name, self.channel_name)
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

    async def credit_event(self, event):
        await self.send_json({"type": "credit.event", "payload": event.get("payload")})

    async def agent_profile_event(self, event):
        await self.send_json({"type": "agent.profile", "payload": event.get("payload")})

    async def message_notification_event(self, event):
        # Notification events are session-scoped; agent-scoped sockets ignore them.
        return

    async def human_input_requests_event(self, event):
        await self.send_json({"type": "human_input_requests.updated", "payload": event.get("payload")})

    async def pending_action_requests_event(self, event):
        await self.send_json({"type": "pending_action_requests.updated", "payload": event.get("payload")})

    @database_sync_to_async
    def _resolve_agent(self, user, session, agent_id):
        return resolve_agent(
            user,
            session,
            agent_id,
            allow_shared=True,
            allow_delinquent_personal_chat=True,
        )


class AgentChatSessionConsumer(AsyncJsonWebsocketConsumer):
    """Realtime channel for persistent agent updates with a session-level connection."""

    async def connect(self):
        self.active_agent_id = None
        self.subscriptions = {}
        self.user_group_name = None
        self.profile_group_name = None

        user = self.scope.get("user")
        session = self.scope.get("session")
        if user is None or not getattr(user, "is_authenticated", False):
            logger.warning("AgentChatSessionConsumer rejected unauthenticated connection")
            await self.close(code=4401)
            return

        self.user = user
        self.session = session
        self.profile_group_name = user_profile_group_name(user.id)

        if self.channel_layer is None:
            logger.error("AgentChatSessionConsumer cannot attach to channel layer (not configured)")
            await self.close(code=1011)
            return

        try:
            await self.channel_layer.group_add(self.profile_group_name, self.channel_name)
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.exception(
                "AgentChatSessionConsumer failed to join profile group; channel layer unavailable (user=%s): %s",
                user,
                exc,
            )
            await self.close(code=1011)
            return

        logger.info("AgentChatSessionConsumer connected user=%s channel=%s", user, self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        await self._clear_subscriptions()
        if getattr(self, "profile_group_name", None) and self.channel_layer is not None:
            try:
                await self.channel_layer.group_discard(self.profile_group_name, self.channel_name)
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.exception("AgentChatSessionConsumer failed removing profile channel from group: %s", exc)
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
            mode = content.get("mode")
            context_override = content.get("context")
            if context_override is not None and not isinstance(context_override, dict):
                context_override = None
            if not agent_id:
                await self.send_json({"type": "subscription.error", "message": "agent_id is required"})
                return
            if mode not in {"active", "background"}:
                await self.send_json(
                    {
                        "type": "subscription.error",
                        "agent_id": str(agent_id),
                        "message": "mode must be active or background",
                    }
                )
                return
            await self._subscribe(str(agent_id), mode=mode, context_override=context_override)
            return
        if message_type == "unsubscribe":
            agent_id = content.get("agent_id")
            await self._unsubscribe(str(agent_id) if agent_id else None)

    async def timeline_event(self, event):
        await self._send_agent_event("timeline.event", event)

    async def processing_event(self, event):
        await self._send_agent_event("processing", event)

    async def stream_event(self, event):
        agent_id = self._extract_agent_id(event)
        if not agent_id or agent_id != self.active_agent_id:
            return
        if agent_id not in self.subscriptions:
            return
        await self.send_json({"type": "stream.event", "agent_id": agent_id, "payload": event.get("payload")})

    async def credit_event(self, event):
        await self._send_agent_event("credit.event", event)

    async def agent_profile_event(self, event):
        agent_id = self._extract_agent_id(event)
        await self.send_json({"type": "agent.profile", "agent_id": agent_id, "payload": event.get("payload")})

    async def message_notification_event(self, event):
        await self.send_json({"type": "message.notification", "payload": event.get("payload")})

    async def human_input_requests_event(self, event):
        await self._send_agent_event("human_input_requests.updated", event)

    async def pending_action_requests_event(self, event):
        await self._send_agent_event("pending_action_requests.updated", event)

    async def _subscribe(self, agent_id: str, *, mode: str, context_override=None) -> None:
        if agent_id not in self.subscriptions:
            try:
                await self._resolve_agent(self.user, self.session, agent_id, context_override=context_override)
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

            subscription = AgentChatSubscription(
                group_name=f"agent-chat-{agent_id}",
                user_group_name=user_stream_group_name(agent_id, self.user.id),
            )
            try:
                await self.channel_layer.group_add(subscription.group_name, self.channel_name)
                await self.channel_layer.group_add(subscription.user_group_name, self.channel_name)
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
                return
            self.subscriptions[agent_id] = subscription

        if mode == "active":
            self.active_agent_id = agent_id
        elif self.active_agent_id == agent_id:
            self.active_agent_id = None

        logger.info(
            "AgentChatSessionConsumer subscribed user=%s agent=%s mode=%s channel=%s",
            self.user,
            agent_id,
            mode,
            self.channel_name,
        )

    async def _unsubscribe(self, agent_id: str | None) -> None:
        if agent_id is None:
            await self._clear_subscriptions()
            return
        if agent_id not in self.subscriptions:
            return
        await self._remove_subscription(agent_id)

    async def _clear_subscriptions(self) -> None:
        if self.channel_layer is None:
            return
        for agent_id in list(self.subscriptions.keys()):
            await self._remove_subscription(agent_id)

    async def _remove_subscription(self, agent_id: str) -> None:
        subscription = self.subscriptions.pop(agent_id, None)
        if subscription is None:
            return
        if self.active_agent_id == agent_id:
            self.active_agent_id = None
        if self.channel_layer is None:
            return
        if subscription.group_name:
            try:
                await self.channel_layer.group_discard(subscription.group_name, self.channel_name)
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.exception("AgentChatSessionConsumer failed removing channel from group: %s", exc)
        if subscription.user_group_name:
            try:
                await self.channel_layer.group_discard(subscription.user_group_name, self.channel_name)
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.exception("AgentChatSessionConsumer failed removing channel from group: %s", exc)

    async def _send_agent_event(self, event_type: str, event) -> None:
        agent_id = self._extract_agent_id(event)
        if agent_id and agent_id not in self.subscriptions:
            return
        await self.send_json({"type": event_type, "agent_id": agent_id, "payload": event.get("payload")})

    def _extract_agent_id(self, event) -> str | None:
        agent_id = event.get("agent_id")
        if isinstance(agent_id, str) and agent_id:
            return agent_id
        payload = event.get("payload")
        if isinstance(payload, dict):
            payload_agent_id = payload.get("agent_id")
            if isinstance(payload_agent_id, str) and payload_agent_id:
                return payload_agent_id
        return None

    @database_sync_to_async
    def _resolve_agent(self, user, session, agent_id, context_override=None):
        return resolve_agent(
            user,
            session,
            agent_id,
            context_override=context_override,
            allow_shared=True,
            allow_delinquent_personal_chat=True,
        )


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
