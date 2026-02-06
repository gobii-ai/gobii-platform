"""Celery tasks for persistent-agent visual descriptions and avatar generation."""

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from celery import shared_task
from django.core.files.base import ContentFile
from django.db import transaction

from api.agent.avatar import (
    build_avatar_prompt,
    maybe_schedule_agent_avatar,
    prepare_visual_description,
)
from api.agent.core.image_generation_config import get_image_generation_llm_configs
from api.agent.core.llm_config import get_summarization_llm_config
from api.agent.core.llm_utils import run_completion
from api.agent.core.token_usage import log_agent_completion
from api.agent.short_description import compute_charter_hash
from api.agent.tools.create_image import _extension_for_mime, _generate_image_bytes
from api.models import PersistentAgent, PersistentAgentCompletion

logger = logging.getLogger(__name__)

AVATAR_ASPECT_RATIO = "1:1"


@dataclass(frozen=True)
class AvatarGenerationResult:
    image_bytes: bytes | None
    mime_type: str | None
    endpoint_key: str | None
    model: str | None
    error_detail: str | None


def _clear_visual_requested_hash(agent_id: str, expected_hash: str) -> None:
    PersistentAgent.objects.filter(
        id=agent_id,
        visual_description_requested_hash=expected_hash,
    ).update(visual_description_requested_hash="")


def _clear_avatar_requested_hash(agent_id: str, expected_hash: str) -> None:
    PersistentAgent.objects.filter(
        id=agent_id,
        avatar_requested_hash=expected_hash,
    ).update(avatar_requested_hash="")


def _load_routing_profile(routing_profile_id: str | None) -> Any:
    if not routing_profile_id:
        return None
    try:
        from api.models import LLMRoutingProfile

        return LLMRoutingProfile.objects.filter(id=routing_profile_id).first()
    except Exception:
        logger.debug("Failed to look up routing profile %s", routing_profile_id, exc_info=True)
        return None


def _generate_visual_description_via_llm(
    agent: PersistentAgent,
    charter: str,
    routing_profile: Any = None,
) -> str:
    try:
        _provider, model, params = get_summarization_llm_config(
            agent=agent,
            routing_profile=routing_profile,
        )
    except Exception as exc:
        logger.warning("No summarization model available for visual description generation: %s", exc)
        return ""

    agent_name = (agent.name or "").strip() or "this agent"

    prompt: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": (
                "You design authentic, warm visual identities for AI agents. Given the agent's name and charter, "
                "write one flowing prose paragraph describing WHO this person is - their stable physical identity "
                "that will remain consistent across different photos and contexts. "
                "\n\n"
                "Write as if describing someone you know - not a technical spec, but a living person. "
                "Let their name guide their identity. This description will be used to render the SAME PERSON "
                "in different settings and roles, so focus on their inherent characteristics, not situational details. "
                "\n\n"
                "Naturally weave in:\n"
                "• Physical traits - skin tone, eye color, facial features, hair (color, texture, length), "
                "approximate age, build, distinctive characteristics\n"
                "• Natural expression and energy - their resting warmth, the vibe they carry, what draws you to them\n"
                "• Personal style sensibility - their general aesthetic (polished, casual, eclectic, minimalist, etc.)\n"
                "\n"
                "Do NOT describe lighting, camera angles, specific settings, or how they'd be photographed - "
                "that will vary by role. Focus on WHO they are, not WHERE or HOW they're captured. "
                "\n\n"
                "Embrace beautiful diversity in age, ethnicity, features, style, and personality. "
                "Some people are polished, others wonderfully imperfect. Let each person feel distinctly themselves. "
                "Prioritize warmth and relatability over perfection. "
                "\n\n"
                "Avoid fantasy elements, celebrities, copyrighted characters, bullet lists, or disclaimers. "
                "Just describe a real, warm human being who could be recognized in any photo."
            ),
        },
        {
            "role": "user",
            "content": f"Agent name: {agent_name}\n\nCharter:\n{charter.strip()}",
        },
    ]

    try:
        response = run_completion(
            model=model,
            messages=prompt,
            params=params,
            drop_params=True,
        )
    except Exception as exc:
        logger.exception("LLM visual description generation failed: %s", exc)
        return ""

    log_agent_completion(
        agent,
        completion_type=PersistentAgentCompletion.CompletionType.AVATAR_VISUAL_DESCRIPTION,
        response=response,
    )

    try:
        return response.choices[0].message.content.strip()
    except Exception:
        logger.exception(
            "Unexpected LiteLLM response structure when generating visual description"
        )
        return ""


def _generate_avatar_image(prompt: str) -> AvatarGenerationResult:
    configs = get_image_generation_llm_configs()
    if not configs:
        return AvatarGenerationResult(
            image_bytes=None,
            mime_type=None,
            endpoint_key=None,
            model=None,
            error_detail="No image generation model is configured.",
        )

    errors: list[str] = []
    for config in configs:
        try:
            image_bytes, mime_type = _generate_image_bytes(
                config,
                prompt=prompt,
                aspect_ratio=AVATAR_ASPECT_RATIO,
                source_image_data_uris=None,
            )
            return AvatarGenerationResult(
                image_bytes=image_bytes,
                mime_type=mime_type,
                endpoint_key=config.endpoint_key,
                model=config.model,
                error_detail=None,
            )
        except ValueError as exc:
            errors.append(f"{config.endpoint_key or config.model}: {exc}")
            logger.info("Avatar generation attempt failed: %s", errors[-1])
        except Exception as exc:
            errors.append(f"{config.endpoint_key or config.model}: {type(exc).__name__}: {exc}")
            logger.warning("Avatar generation attempt failed", exc_info=True)

    detail = errors[-1] if errors else "unknown error"
    return AvatarGenerationResult(
        image_bytes=None,
        mime_type=None,
        endpoint_key=None,
        model=None,
        error_detail=detail,
    )


def _save_agent_avatar(agent: PersistentAgent, *, image_bytes: bytes, mime_type: str, charter_hash: str) -> None:
    extension = _extension_for_mime(mime_type) or ".png"
    old_file_field = getattr(agent, "avatar", None)
    old_name = old_file_field.name if old_file_field and getattr(old_file_field, "name", None) else None
    old_storage = old_file_field.storage if old_name else None

    filename = f"avatar-{uuid.uuid4().hex}{extension}"
    agent.avatar.save(filename, ContentFile(image_bytes), save=False)
    agent.avatar_charter_hash = charter_hash
    agent.avatar_requested_hash = ""

    with transaction.atomic():
        agent.save(update_fields=["avatar", "avatar_charter_hash", "avatar_requested_hash", "updated_at"])

        if old_name and old_name != agent.avatar.name and old_storage is not None:
            def _delete_old_avatar() -> None:
                try:
                    old_storage.delete(old_name)
                except (OSError, ValueError):
                    logger.warning(
                        "Failed deleting prior avatar %s for agent %s",
                        old_name,
                        agent.id,
                        exc_info=True,
                    )

            transaction.on_commit(_delete_old_avatar)


@shared_task(bind=True, name="api.agent.tasks.generate_agent_visual_description")
def generate_agent_visual_description_task(
    self,  # noqa: ANN001
    persistent_agent_id: str,
    charter_hash: str,
    routing_profile_id: str | None = None,
) -> None:
    """Generate and persist the stable visual identity description for an agent."""
    try:
        agent = PersistentAgent.objects.get(id=persistent_agent_id)
    except PersistentAgent.DoesNotExist:
        logger.info(
            "Skipping visual description generation; agent %s no longer exists",
            persistent_agent_id,
        )
        return

    charter = (agent.charter or "").strip()
    if not charter:
        _clear_visual_requested_hash(agent.id, charter_hash)
        logger.debug("Agent %s has no charter; skipping visual description", agent.id)
        return

    current_hash = compute_charter_hash(charter)
    existing_visual_description = prepare_visual_description(agent.visual_description or "")
    if current_hash != charter_hash and not existing_visual_description:
        _clear_visual_requested_hash(agent.id, charter_hash)
        logger.debug(
            "Charter changed for agent %s before visual description generation; current=%s provided=%s",
            agent.id,
            current_hash,
            charter_hash,
        )
        return

    if existing_visual_description:
        updates: dict[str, Any] = {}
        if existing_visual_description != (agent.visual_description or ""):
            updates["visual_description"] = existing_visual_description
        if agent.visual_description_requested_hash == charter_hash:
            updates["visual_description_requested_hash"] = ""
        if updates:
            PersistentAgent.objects.filter(id=agent.id).update(**updates)
        agent.refresh_from_db(
            fields=[
                "visual_description",
                "visual_description_requested_hash",
                "avatar_charter_hash",
                "avatar_requested_hash",
                "charter",
            ]
        )
        maybe_schedule_agent_avatar(agent, routing_profile_id=routing_profile_id)
        return

    routing_profile = _load_routing_profile(routing_profile_id)

    visual_description = _generate_visual_description_via_llm(
        agent,
        charter,
        routing_profile,
    )
    if not visual_description:
        visual_description = charter

    prepared = prepare_visual_description(visual_description)
    if not prepared:
        prepared = prepare_visual_description(charter)

    PersistentAgent.objects.filter(id=agent.id).update(
        visual_description=prepared,
        visual_description_charter_hash=current_hash,
        visual_description_requested_hash="",
    )
    logger.info(
        "Persisted visual description for agent %s (length=%s)",
        agent.id,
        len(prepared),
    )

    agent.refresh_from_db(
        fields=[
            "charter",
            "visual_description",
            "avatar_charter_hash",
            "avatar_requested_hash",
            "visual_description_requested_hash",
        ]
    )
    maybe_schedule_agent_avatar(agent, routing_profile_id=routing_profile_id)


@shared_task(bind=True, name="api.agent.tasks.generate_agent_avatar")
def generate_agent_avatar_task(
    self,  # noqa: ANN001
    persistent_agent_id: str,
    charter_hash: str,
    routing_profile_id: str | None = None,
) -> None:
    """Generate and persist an avatar image for the given agent."""
    try:
        agent = PersistentAgent.objects.get(id=persistent_agent_id)
    except PersistentAgent.DoesNotExist:
        logger.info("Skipping avatar generation; agent %s no longer exists", persistent_agent_id)
        return

    charter = (agent.charter or "").strip()
    if not charter:
        _clear_avatar_requested_hash(agent.id, charter_hash)
        logger.debug("Agent %s has no charter; skipping avatar generation", agent.id)
        return

    current_hash = compute_charter_hash(charter)
    if current_hash != charter_hash:
        _clear_avatar_requested_hash(agent.id, charter_hash)
        logger.debug(
            "Charter changed for agent %s before avatar generation; current=%s provided=%s",
            agent.id,
            current_hash,
            charter_hash,
        )
        return

    visual_description = prepare_visual_description(agent.visual_description or "")
    if not visual_description:
        _clear_avatar_requested_hash(agent.id, charter_hash)
        maybe_schedule_agent_avatar(agent, routing_profile_id=routing_profile_id)
        logger.debug(
            "Agent %s missing visual description before avatar generation; queued prerequisite",
            agent.id,
        )
        return

    prompt = build_avatar_prompt(
        agent=agent,
        visual_description=visual_description,
        charter=charter,
    )

    result = _generate_avatar_image(prompt)
    if not result.image_bytes or not result.mime_type:
        _clear_avatar_requested_hash(agent.id, charter_hash)
        logger.warning(
            "Avatar generation failed for agent %s (%s)",
            agent.id,
            result.error_detail,
        )
        return

    _save_agent_avatar(
        agent,
        image_bytes=result.image_bytes,
        mime_type=result.mime_type,
        charter_hash=current_hash,
    )
    logger.info(
        "Persisted avatar for agent %s (endpoint=%s model=%s)",
        agent.id,
        result.endpoint_key,
        result.model,
    )


__all__ = [
    "generate_agent_avatar_task",
    "generate_agent_visual_description_task",
]
