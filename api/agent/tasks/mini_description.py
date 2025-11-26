"""Celery task for generating mini descriptions from agent charters."""

import logging
from typing import Any

from celery import shared_task

from api.agent.core.llm_config import get_summarization_llm_config
from api.agent.core.llm_utils import run_completion
from api.agent.short_description import (
    compute_charter_hash,
    prepare_mini_description,
)
from api.models import PersistentAgent

logger = logging.getLogger(__name__)


def _clear_requested_hash(agent_id: str, expected_hash: str) -> None:
    PersistentAgent.objects.filter(
        id=agent_id,
        mini_description_requested_hash=expected_hash,
    ).update(mini_description_requested_hash="")


def _generate_via_llm(agent: PersistentAgent, charter: str, routing_profile: Any = None) -> str:
    try:
        model, params = get_summarization_llm_config(agent=agent, routing_profile=routing_profile)
    except Exception as exc:
        logger.warning("No summarization model available for mini description: %s", exc)
        return ""

    prompt: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": (
                "You write ultra-short labels for AI agents. Given a full charter, "
                "respond with 2-5 plain words describing the agent's core purpose. "
                "Use simple noun phrases like 'Sales leads generator'. Avoid punctuation,"
                " emojis, or extra commentary."
            ),
        },
        {
            "role": "user",
            "content": charter.strip(),
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
        logger.exception("LLM mini description generation failed: %s", exc)
        return ""

    try:
        return response.choices[0].message.content.strip()
    except Exception:  # pragma: no cover - defensive against schema drift
        logger.exception(
            "Unexpected LiteLLM response structure when generating mini description"
        )
        return ""


@shared_task(bind=True, name="api.agent.tasks.generate_agent_mini_description")
def generate_agent_mini_description_task(
    self,  # noqa: ANN001
    persistent_agent_id: str,
    charter_hash: str,
    routing_profile_id: str | None = None,
) -> None:
    """Generate and persist a mini description for the given agent."""
    try:
        agent = PersistentAgent.objects.get(id=persistent_agent_id)
    except PersistentAgent.DoesNotExist:
        logger.info(
            "Skipping mini description generation; agent %s no longer exists",
            persistent_agent_id,
        )
        return

    # Look up routing profile if provided
    routing_profile = None
    if routing_profile_id:
        try:
            from api.models import LLMRoutingProfile
            routing_profile = LLMRoutingProfile.objects.filter(id=routing_profile_id).first()
        except Exception:
            logger.debug("Failed to look up routing profile %s", routing_profile_id, exc_info=True)

    charter = (agent.charter or "").strip()
    if not charter:
        _clear_requested_hash(agent.id, charter_hash)
        logger.debug("Agent %s has no charter; skipping mini description", agent.id)
        return

    current_hash = compute_charter_hash(charter)
    if current_hash != charter_hash:
        _clear_requested_hash(agent.id, charter_hash)
        logger.debug(
            "Charter changed for agent %s before mini description generation; current=%s provided=%s",
            agent.id,
            current_hash,
            charter_hash,
        )
        return

    mini_desc = _generate_via_llm(agent, charter, routing_profile)
    if not mini_desc:
        mini_desc = charter

    prepared = prepare_mini_description(mini_desc)
    if not prepared:
        prepared = prepare_mini_description(charter)

    PersistentAgent.objects.filter(id=agent.id).update(
        mini_description=prepared,
        mini_description_charter_hash=current_hash,
        mini_description_requested_hash="",
    )
    logger.info(
        "Persisted mini description for agent %s (length=%s)", agent.id, len(prepared)
    )


__all__ = ["generate_agent_mini_description_task"]
