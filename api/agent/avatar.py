"""Utilities for persistent-agent visual identity and avatar generation."""

import logging
from datetime import timedelta

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from api.agent.core.image_generation_config import is_image_generation_configured
from api.agent.short_description import compute_charter_hash
from api.models import PersistentAgent

logger = logging.getLogger(__name__)

MAX_VISUAL_DESCRIPTION_LENGTH = 1800


def _normalize_visual_description(text: str) -> str:
    if not text:
        return ""
    return " ".join(text.split()).strip()


def _avatar_cooldown_cutoff(now=None):
    cooldown_hours = max(0, int(settings.AGENT_AVATAR_GENERATION_COOLDOWN_HOURS))
    if cooldown_hours <= 0:
        return None
    reference_time = now or timezone.now()
    return reference_time - timedelta(hours=cooldown_hours)


def _acquire_avatar_enqueue_slot(
    *,
    agent_id,
    charter_hash: str,
    cooldown_cutoff,
) -> bool:
    """Atomically claim avatar enqueue slot if cooldown permits and request is not already current."""
    update_query = PersistentAgent.objects.filter(
        id=agent_id,
    ).exclude(avatar_requested_hash=charter_hash)
    if cooldown_cutoff is not None:
        update_query = update_query.filter(
            Q(avatar_last_generation_attempt_at__isnull=True)
            | Q(avatar_last_generation_attempt_at__lte=cooldown_cutoff)
        )
    return bool(update_query.update(avatar_requested_hash=charter_hash))


def prepare_visual_description(text: str, max_length: int = MAX_VISUAL_DESCRIPTION_LENGTH) -> str:
    """Normalize and bound visual description text for storage."""
    normalized = _normalize_visual_description(text)
    if not normalized:
        return ""
    if max_length > 0 and len(normalized) > max_length:
        return normalized[:max_length].rstrip()
    return normalized


def build_avatar_prompt(*, agent: PersistentAgent, visual_description: str, charter: str) -> str:
    """Build an image-generation prompt for a distinctive, realistic agent avatar."""
    safe_name = (agent.name or "Agent").strip() or "Agent"
    safe_visual = prepare_visual_description(visual_description)
    safe_charter = " ".join((charter or "").split()).strip()

    return (
        "Create an authentic, photorealistic square portrait of this specific person. "
        "They are looking directly into the camera — genuine eye contact, like they're mid-conversation with you. "
        "One person only, no text, no logos, no watermark. "
        "\n\n"
        f"Agent name: {safe_name}\n"
        "\n"
        "WHO this person is (their stable identity - match these traits exactly):\n"
        f"{safe_visual}\n"
        "\n"
        "THEIR CURRENT ROLE (use this to guide photographic approach, setting, wardrobe, and context):\n"
        f"{safe_charter}\n"
        "\n"
        "The visual identity above defines who this person is - their face, features, coloring, age, energy, style. "
        "Render this EXACT person, then choose the photographic approach (lighting, composition, setting, mood) "
        "that authentically captures them in the context of their role. Let creative variety emerge naturally - "
        "different roles might call for different photographic treatments (intimate window light, environmental "
        "portraits, golden hour outdoors, studio, candid moments, etc.). "
        "Head and shoulders framing, shallow depth of field, sharp focus on the eyes. "
        "Not a stock photo — a real person."
        "\n\n"
        "Technical constraints: modern color photo, realistic human proportions, no fantasy elements, "
        "no cartoon/anime/3D render style."
    )


def maybe_schedule_agent_avatar(
    agent: PersistentAgent,
    routing_profile_id: str | None = None,
) -> bool:
    """Schedule visual-description/avatar generation as needed.

    Behavior:
    - If no visual description exists, queue generation for that first.
    - If visual description exists and charter hash differs from avatar hash,
      queue a new avatar render.
    """
    charter = (agent.charter or "").strip()
    if not charter:
        return False

    charter_hash = compute_charter_hash(charter)

    visual_description = prepare_visual_description(getattr(agent, "visual_description", ""))
    if visual_description and visual_description != getattr(agent, "visual_description", ""):
        PersistentAgent.objects.filter(id=agent.id).update(visual_description=visual_description)

    if not visual_description:
        if agent.visual_description_requested_hash == charter_hash:
            return False

        updated = PersistentAgent.objects.filter(id=agent.id).exclude(
            visual_description_requested_hash=charter_hash,
        ).update(
            visual_description_requested_hash=charter_hash,
        )
        if not updated:
            return False

        try:
            from api.agent.tasks.agent_avatar import generate_agent_visual_description_task

            generate_agent_visual_description_task.delay(str(agent.id), charter_hash, routing_profile_id)
            logger.debug(
                "Queued visual-description generation for agent %s (hash=%s)",
                agent.id,
                charter_hash,
            )
            return True
        except Exception:
            logger.exception("Failed to enqueue visual-description generation for agent %s", agent.id)
            PersistentAgent.objects.filter(id=agent.id).update(
                visual_description_requested_hash="",
            )
            return False

    # Treat matching hash as up-to-date even when avatar is intentionally cleared.
    if (agent.avatar_charter_hash or "") == charter_hash:
        return False

    try:
        image_generation_ready = is_image_generation_configured()
    except Exception:
        logger.exception("Failed checking image-generation availability for agent %s", agent.id)
        image_generation_ready = False

    if not image_generation_ready:
        return False

    if not _acquire_avatar_enqueue_slot(
        agent_id=agent.id,
        charter_hash=charter_hash,
        cooldown_cutoff=_avatar_cooldown_cutoff(),
    ):
        return False

    try:
        from api.agent.tasks.agent_avatar import generate_agent_avatar_task

        generate_agent_avatar_task.delay(str(agent.id), charter_hash, routing_profile_id)
        logger.debug("Queued avatar generation for agent %s (hash=%s)", agent.id, charter_hash)
        return True
    except Exception:
        logger.exception("Failed to enqueue avatar generation for agent %s", agent.id)
        PersistentAgent.objects.filter(id=agent.id).update(avatar_requested_hash="")
        return False


__all__ = [
    "build_avatar_prompt",
    "maybe_schedule_agent_avatar",
    "prepare_visual_description",
]
