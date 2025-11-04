import logging
from typing import Union
from uuid import UUID

from config.redis_client import get_redis_client

logger = logging.getLogger(__name__)

_QUEUED_KEY_TEMPLATE = "agent-event-processing:queued:{agent_id}"
_DEFAULT_QUEUE_TTL_SECONDS = 3600


def _queued_key(agent_id: Union[str, UUID]) -> str:
    return _QUEUED_KEY_TEMPLATE.format(agent_id=agent_id)


def set_processing_queued_flag(agent_id: Union[str, UUID], *, ttl: int = _DEFAULT_QUEUE_TTL_SECONDS) -> None:
    """Mark the agent as having queued processing work."""
    try:
        client = get_redis_client()
        key = _queued_key(agent_id)
        client.set(key, "1")
        if ttl > 0:
            client.expire(key, ttl)
    except Exception:
        logger.exception("Failed to set processing queued flag for agent %s", agent_id)


def clear_processing_queued_flag(agent_id: Union[str, UUID]) -> None:
    """Clear the queued processing flag for the agent."""
    try:
        client = get_redis_client()
        client.delete(_queued_key(agent_id))
    except Exception:
        logger.exception("Failed to clear processing queued flag for agent %s", agent_id)


def is_processing_queued(agent_id: Union[str, UUID], client=None) -> bool:
    """Check whether the agent currently has queued processing work."""
    try:
        redis_client = client or get_redis_client()
        return bool(redis_client.exists(_queued_key(agent_id)))
    except Exception:
        logger.exception("Failed to check processing queued flag for agent %s", agent_id)
        return False
