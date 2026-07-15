import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)


def user_stream_group_name(agent_id: str, user_id: int) -> str:
    return f"agent-chat-{agent_id}-user-{user_id}"


def user_profile_group_name(user_id: int) -> str:
    return f"agent-chat-user-{user_id}"


def send_developer_update(agent_id: str) -> None:
    """Notify staff chat subscribers that the enriched timeline changed."""
    if not agent_id:
        return
    channel_layer = get_channel_layer()
    if channel_layer is None:
        logger.debug("Channel layer unavailable; skipping developer update for agent %s", agent_id)
        return
    try:
        async_to_sync(channel_layer.group_send)(
            f"agent-chat-{agent_id}",
            {"type": "developer_event", "agent_id": str(agent_id)},
        )
    except Exception:
        logger.debug("Failed to send developer update for agent %s", agent_id, exc_info=True)


def send_stream_event(agent_id: str, user_id: int, payload: dict) -> None:
    if not agent_id or user_id is None:
        return
    channel_layer = get_channel_layer()
    if channel_layer is None:
        logger.debug("Channel layer unavailable; skipping stream send for agent %s user %s", agent_id, user_id)
        return
    async_to_sync(channel_layer.group_send)(
        user_stream_group_name(agent_id, user_id),
        {"type": "stream_event", "agent_id": str(agent_id), "payload": payload},
    )


def send_user_group_event(agent_id: str, user_id: int, message_type: str, payload: dict) -> None:
    if not agent_id or user_id is None:
        return
    channel_layer = get_channel_layer()
    if channel_layer is None:
        logger.debug(
            "Channel layer unavailable; skipping user group send for agent %s user %s",
            agent_id,
            user_id,
        )
        return
    async_to_sync(channel_layer.group_send)(
        user_stream_group_name(agent_id, user_id),
        {"type": message_type, "agent_id": str(agent_id), "payload": payload},
    )
