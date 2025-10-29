"""
Celery task responsible for creating persistent agent avatars.
"""

import base64
import io
import logging
from pathlib import Path

from celery import shared_task
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils import timezone

from api.models import PersistentAgent
from openai import OpenAI

logger = logging.getLogger(__name__)


def _reset_request(agent_id: str) -> None:
    """Clear the pending-request timestamp so future updates may retry."""
    PersistentAgent.objects.filter(id=agent_id).update(
        avatar_generation_requested_at=None,
    )


def _mark_success(agent_id: str, storage_path: str) -> None:
    """Persist success metadata on the agent."""
    PersistentAgent.objects.filter(id=agent_id).update(
        avatar_storage_path=storage_path,
        avatar_generated_at=timezone.now(),
        avatar_generation_requested_at=None,
    )


def _resolve_base_image_path() -> Path:
    """Return the filesystem path for the base avatar image."""
    candidate = Path(settings.AGENT_AVATAR_BASE_IMAGE_PATH)
    if not candidate.is_absolute():
        candidate = Path(settings.BASE_DIR) / candidate
    return candidate


def _load_base_image_bytes() -> tuple[Path, bytes]:
    base_path = _resolve_base_image_path()
    if not base_path.exists():
        raise FileNotFoundError(f"Base avatar image not found at {base_path}")
    return base_path, base_path.read_bytes()


def _build_prompt(charter: str) -> str:
    return (
        "You are designing a distinctive square avatar for a Gobii autonomous agent. "
        "Use the provided Gobii fish base image as inspiration and retain recognizable "
        "elements of the fish while creating a unique look that reflects the agent's charter. "
        "Blend colors and small details that hint at the agent's responsibilities. "
        "Keep the composition clean, legible, and professional. "
        "I have permissions as the rights holder to make modifications to this image.\n\n"
        f"Agent charter:\n{charter.strip()}"
    )


def _save_avatar_bytes(agent_id: str, image_bytes: bytes) -> str:
    """Persist avatar bytes via Django's default storage and return the stored path."""
    prefix = (settings.AGENT_AVATAR_STORAGE_PREFIX or "agent_avatars").strip("/")
    storage_path = f"{prefix}/{agent_id}/avatar.png"
    try:
        default_storage.delete(storage_path)
    except Exception:
        logger.debug("Unable to delete existing avatar at %s (may not exist)", storage_path)

    saved_path = default_storage.save(storage_path, ContentFile(image_bytes))
    return saved_path


@shared_task(bind=True, name="api.agent.tasks.generate_agent_avatar")
def generate_agent_avatar_task(self, persistent_agent_id: str) -> None:  # noqa: D401, ANN001
    """Generate an avatar for the provided persistent agent."""
    try:
        agent = PersistentAgent.objects.get(id=persistent_agent_id)
    except PersistentAgent.DoesNotExist:
        logger.info("Skipping avatar generation; agent %s no longer exists", persistent_agent_id)
        return

    charter = (agent.charter or "").strip()
    if not charter:
        logger.debug("Agent %s has no charter; skipping avatar generation", persistent_agent_id)
        _reset_request(agent.id)
        return

    if agent.avatar_storage_path:
        logger.debug("Agent %s already has an avatar; clearing pending flag", persistent_agent_id)
        _reset_request(agent.id)
        return

    try:
        base_image_path, base_image_bytes = _load_base_image_bytes()
    except FileNotFoundError as exc:
        logger.error("Cannot generate avatar for agent %s: %s", persistent_agent_id, exc)
        _reset_request(agent.id)
        return
    except Exception:
        logger.exception("Failed to load base avatar image for agent %s", persistent_agent_id)
        _reset_request(agent.id)
        return

    prompt = _build_prompt(charter)

    try:
        base_image_stream = io.BytesIO(base_image_bytes)
        base_image_stream.name = base_image_path.name  # Help OpenAI detect mimetype
        base_image_stream.seek(0)

        client = OpenAI()
        response = client.images.edit(  # type: ignore[attr-defined]
            model="gpt-image-1",
            image=base_image_stream,
            prompt=prompt,
            size="1024x1024",
            n=1,
        )
    except Exception:
        logger.exception("OpenAI image generation failed for agent %s", persistent_agent_id)
        _reset_request(agent.id)
        return

    try:
        data = response.data[0]
        image_b64 = data.b64_json
    except Exception:
        logger.exception("Unexpected response structure from OpenAI for agent %s", persistent_agent_id)
        _reset_request(agent.id)
        return

    if not image_b64:
        logger.error("OpenAI returned an empty avatar payload for agent %s", persistent_agent_id)
        _reset_request(agent.id)
        return

    try:
        image_bytes = base64.b64decode(image_b64)
    except Exception:
        logger.exception("Failed to decode avatar image payload for agent %s", persistent_agent_id)
        _reset_request(agent.id)
        return

    try:
        storage_path = _save_avatar_bytes(str(agent.id), image_bytes)
    except Exception:
        logger.exception("Failed to store avatar image for agent %s", persistent_agent_id)
        _reset_request(agent.id)
        return

    _mark_success(agent.id, storage_path)
    logger.info("Generated avatar for agent %s at %s", persistent_agent_id, storage_path)


__all__ = ["generate_agent_avatar_task"]
