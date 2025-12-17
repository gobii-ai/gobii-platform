import logging
from typing import TYPE_CHECKING

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

if TYPE_CHECKING:
    from api.models import PersistentAgentSystemMessage

logger = logging.getLogger(__name__)


def _group_name(agent_id: str) -> str:
    return f"agent-audit-{agent_id}"


def send_audit_event(agent_id: str, payload: dict) -> None:
    """Broadcast a structured audit event to staff subscribers."""

    channel_layer = get_channel_layer()
    if channel_layer is None:
        logger.debug("Channel layer unavailable; skipping audit realtime send for agent %s", agent_id)
        return
    async_to_sync(channel_layer.group_send)(
        _group_name(agent_id),
        {
            "type": "audit_event",
            "payload": payload,
        },
    )


def broadcast_system_message_audit(message: "PersistentAgentSystemMessage") -> None:
    """Serialize and send a system message to audit subscribers."""

    agent_id = getattr(message, "agent_id", None)
    if not agent_id:
        return

    try:
        from console.agent_audit.serializers import serialize_system_message

        payload = serialize_system_message(message)
        send_audit_event(str(agent_id), payload)
    except Exception:
        logger.debug(
            "Failed to broadcast audit system message for %s",
            getattr(message, "id", None),
            exc_info=True,
        )
