import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

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
