
import logging
import json
import time
from enum import Enum
from typing import Any, Dict, Optional
from config.redis_client import get_redis_client

logger = logging.getLogger(__name__)

class AgentEventType(str, Enum):
    PROCESSING_STARTED = "processing_started"
    PROCESSING_COMPLETE = "processing_complete"
    STEP_COMPLETED = "step_completed"
    CYCLE_CLOSED = "cycle_closed"
    ERROR = "error"

def get_agent_event_channel(agent_id: str) -> str:
    return f"agent:events:{agent_id}"

def publish_agent_event(
    agent_id: str, 
    event_type: AgentEventType | str, 
    payload: Optional[Dict[str, Any]] = None
) -> None:
    """
    Publish a semantic lifecycle event for an agent to Redis.
    """
    try:
        channel = get_agent_event_channel(agent_id)
        
        message = {
            "type": event_type.value if isinstance(event_type, AgentEventType) else event_type,
            "timestamp": time.time(),
            "agent_id": str(agent_id),
            "payload": payload or {}
        }
        
        redis = get_redis_client()
        # Use publish for fire-and-forget event bus
        redis.publish(channel, json.dumps(message))
        
    except Exception as e:
        # Never block agent execution due to metric/event publishing failures
        logger.warning(f"Failed to publish agent event {event_type} for {agent_id}: {e}")
