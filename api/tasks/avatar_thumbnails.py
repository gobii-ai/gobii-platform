import logging

from celery import shared_task

from api.models import PersistentAgent
from api.services.agent_avatar_thumbnails import AvatarThumbnailUnavailable, open_agent_avatar_thumbnail

logger = logging.getLogger(__name__)


@shared_task(name="api.tasks.generate_agent_avatar_thumbnail")
def generate_agent_avatar_thumbnail_task(agent_id: str, expected_version: str) -> bool:
    agent = PersistentAgent.objects.filter(id=agent_id).only("id", "avatar", "updated_at").first()
    if agent is None or agent.get_avatar_thumbnail_version() != expected_version:
        return False
    try:
        thumbnail = open_agent_avatar_thumbnail(agent)
        thumbnail.close()
    except AvatarThumbnailUnavailable:
        logger.warning("Unable to pre-generate avatar thumbnail for agent %s", agent_id)
        return False
    return True


def enqueue_agent_avatar_thumbnail(agent: PersistentAgent) -> None:
    expected_version = agent.get_avatar_thumbnail_version()
    if expected_version:
        generate_agent_avatar_thumbnail_task.delay(str(agent.id), expected_version)
