import logging
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.core.exceptions import PermissionDenied
from django.db import DatabaseError

from console.agent_chat.access import resolve_staff_agent

logger = logging.getLogger(__name__)


class StaffAgentDeveloperConsumer(AsyncJsonWebsocketConsumer):
    """Realtime channel for staff-only live-chat developer updates."""

    async def connect(self):
        user = self.scope.get("user")
        if user is None or not getattr(user, "is_authenticated", False):
            await self.close(code=4401)
            return
        if not (user.is_staff or user.is_superuser):
            await self.close(code=4403)
            return
        self.user = user

        agent_id = self.scope.get("url_route", {}).get("kwargs", {}).get("agent_id")
        if not agent_id:
            await self.close(code=4404)
            return
        self.agent_id = str(agent_id)
        query = parse_qs((self.scope.get("query_string") or b"").decode("utf-8"))
        staff_context_type = (query.get("staff_context_type") or [None])[0]
        staff_context_id = (query.get("staff_context_id") or [None])[0]
        if bool(staff_context_type) != bool(staff_context_id):
            await self.close(code=4403)
            return

        try:
            await self._ensure_agent_exists(self.agent_id, staff_context_type, staff_context_id)
        except PermissionDenied:
            await self.close(code=4403)
            return
        except DatabaseError:
            logger.exception("Failed resolving agent %s for audit websocket", self.agent_id)
            await self.close(code=1011)
            return

        self.group_name = f"agent-audit-{self.agent_id}"
        if self.channel_layer is None:
            logger.error("StaffAgentDeveloperConsumer cannot attach to channel layer (agent=%s)", self.agent_id)
            await self.close(code=1011)
            return

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        if hasattr(self, "group_name") and self.channel_layer is not None:
            try:
                await self.channel_layer.group_discard(self.group_name, self.channel_name)
            except Exception:  # pragma: no cover - defensive
                logger.debug("Failed to discard audit channel %s", getattr(self, "group_name", None))

    async def receive_json(self, content, **kwargs):
        if content.get("type") == "ping":
            await self.send_json({"type": "pong"})

    async def audit_event(self, event):
        await self.send_json({"type": "developer.event", "payload": event.get("payload")})

    @database_sync_to_async
    def _ensure_agent_exists(
        self,
        agent_id: str,
        staff_context_type: str | None,
        staff_context_id: str | None,
    ) -> None:
        override = None
        if staff_context_type and staff_context_id:
            override = {"type": staff_context_type, "id": staff_context_id}
        resolve_staff_agent(self.user, agent_id, override)
