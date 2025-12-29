import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)


def user_stream_group_name(agent_id: str, user_id: int) -> str:
    return f"agent-chat-{agent_id}-user-{user_id}"


def send_stream_event(agent_id: str, user_id: int, payload: dict) -> None:
    if not agent_id or user_id is None:
        return
    channel_layer = get_channel_layer()
    if channel_layer is None:
        logger.debug("Channel layer unavailable; skipping stream send for agent %s user %s", agent_id, user_id)
        return
    async_to_sync(channel_layer.group_send)(
        user_stream_group_name(agent_id, user_id),
        {"type": "stream_event", "payload": payload},
    )
