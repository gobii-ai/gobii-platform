"""Celery tasks for importing message attachments into filespace."""
from __future__ import annotations

from celery import shared_task
import logging

from ..files.filespace_service import import_message_attachments_to_filespace

logger = logging.getLogger(__name__)

@shared_task(name="api.agent.tasks.import_message_attachments_to_filespace")
def import_message_attachments_to_filespace_task(message_id: str) -> int:
    """Import attachments for the given message into the agent's filespace.

    Returns the number of created nodes.
    """
    try:
        created = import_message_attachments_to_filespace(message_id)
        return len(created)
    except Exception as e:
        logger.exception("Failed importing attachments for message %s: %s", message_id, e)
        return 0

