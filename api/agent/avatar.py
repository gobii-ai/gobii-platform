"""
Helpers for generating and scheduling persistent agent avatars.
"""

import logging

from django.db import transaction
from django.utils import timezone

from api.models import PersistentAgent
from api.agent.tasks.avatar import generate_agent_avatar_task

logger = logging.getLogger(__name__)


def maybe_schedule_agent_avatar(agent: PersistentAgent) -> bool:
    """
    Queue avatar generation for the agent if it does not already have one.

    Returns True when a task was enqueued, False otherwise.
    """
    charter = (agent.charter or "").strip()
    if not charter:
        return False

    if agent.avatar_storage_path:
        return False

    updated = PersistentAgent.objects.filter(
        id=agent.id,
        avatar_storage_path="",
        avatar_generation_requested_at__isnull=True,
    ).update(avatar_generation_requested_at=timezone.now())

    if not updated:
        return False

    def _enqueue() -> None:
        try:
            generate_agent_avatar_task.delay(str(agent.id))
        except Exception:
            logger.exception("Failed to enqueue avatar generation for agent %s", agent.id)

    transaction.on_commit(_enqueue)
    return True


__all__ = ["maybe_schedule_agent_avatar"]
