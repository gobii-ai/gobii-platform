"""Celery task for generating short descriptions from agent charters."""
from __future__ import annotations

import logging
from typing import Any

from celery import shared_task

from api.agent.core.llm_config import get_summarization_llm_config
from api.agent.core.llm_utils import run_completion
from api.agent.short_description import (
    compute_charter_hash,
    prepare_short_description,
)
from api.models import PersistentAgent

logger = logging.getLogger(__name__)


def _clear_requested_hash(agent_id: str, expected_hash: str) -> None:
    PersistentAgent.objects.filter(
        id=agent_id,
        short_description_requested_hash=expected_hash,
    ).update(short_description_requested_hash="")


def _generate_via_llm(agent: PersistentAgent, charter: str) -> str:
    try:
        model, params = get_summarization_llm_config(agent=agent)
    except Exception as exc:
        logger.warning("No summarization model available for short description: %s", exc)
        return ""

    prompt = [
        {
            "role": "system",
            "content": (
                "You write concise listings for AI agents. Given the full charter, "
                "respond with one plain-language sentence under 160 characters "
                "summarising who the agent helps and what it does. Do not add emojis, "
                "quotes, bullet points, or introductions."
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
        logger.exception("LLM short description generation failed: %s", exc)
        return ""

    try:
        return response.choices[0].message.content.strip()
    except Exception:
        logger.exception("Unexpected LiteLLM response structure when generating short description")
        return ""


@shared_task(bind=True, name="api.agent.tasks.generate_agent_short_description")
def generate_agent_short_description_task(self, persistent_agent_id: str, charter_hash: str) -> None:  # noqa: D401, ANN001
    """Generate and persist a short description for the given agent."""
    try:
        agent = PersistentAgent.objects.get(id=persistent_agent_id)
    except PersistentAgent.DoesNotExist:
        logger.info("Skipping short description generation; agent %s no longer exists", persistent_agent_id)
        return

    charter = (agent.charter or "").strip()
    if not charter:
        _clear_requested_hash(agent.id, charter_hash)
        logger.debug("Agent %s has no charter; skipping short description", agent.id)
        return

    current_hash = compute_charter_hash(charter)
    if current_hash != charter_hash:
        _clear_requested_hash(agent.id, charter_hash)
        logger.debug(
            "Charter changed for agent %s before short description generation; current=%s provided=%s",
            agent.id,
            current_hash,
            charter_hash,
        )
        return

    short_desc = _generate_via_llm(agent, charter)
    if not short_desc:
        short_desc = charter

    prepared = prepare_short_description(short_desc, max_length=160)
    if not prepared:
        prepared = prepare_short_description(charter, max_length=160)

    PersistentAgent.objects.filter(id=agent.id).update(
        short_description=prepared,
        short_description_charter_hash=current_hash,
        short_description_requested_hash="",
    )
    logger.info(
        "Persisted short description for agent %s (length=%s)", agent.id, len(prepared)
    )


__all__ = ["generate_agent_short_description_task"]
