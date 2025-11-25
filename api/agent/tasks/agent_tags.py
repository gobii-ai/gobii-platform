"""Celery task for generating descriptive tags from agent charters."""

import ast
import json
import logging
import re
from typing import Any, Iterable, List

from celery import shared_task

from api.agent.core.llm_config import get_summarization_llm_config
from api.agent.core.llm_utils import run_completion
from api.agent.core.token_usage import extract_token_usage, log_agent_completion
from api.agent.short_description import compute_charter_hash
from api.agent.tags import MAX_TAGS, normalize_tags, strip_code_fence
from api.models import PersistentAgent, PersistentAgentCompletion

logger = logging.getLogger(__name__)

_QUOTED_PATTERN = re.compile(r'"([^"]+)"|\'([^\']+)\'')


def _clear_requested_hash(agent_id: str, expected_hash: str) -> None:
    PersistentAgent.objects.filter(
        id=agent_id,
        tags_requested_hash=expected_hash,
    ).update(tags_requested_hash="")


def _coerce_iterable(data: Any) -> Iterable[str]:
    if isinstance(data, (list, tuple, set)):
        return [str(item) for item in data]
    if isinstance(data, dict):
        if isinstance(data.get("tags"), (list, tuple, set)):
            return [str(item) for item in data["tags"]]
        return [str(value) for value in data.values()]
    if isinstance(data, str):
        return [data]
    return []


def _extract_tags(content: str) -> List[str]:
    """Parse the LLM response into a list of tags."""
    if not content:
        return []

    cleaned = strip_code_fence(content).strip()
    if not cleaned:
        return []

    parsed: List[str] = []

    try:
        parsed = list(_coerce_iterable(json.loads(cleaned)))
    except json.JSONDecodeError:
        try:
            parsed = list(_coerce_iterable(ast.literal_eval(cleaned)))
        except (SyntaxError, ValueError):
            parsed = []

    if not parsed:
        quoted = [first or second for first, second in _QUOTED_PATTERN.findall(cleaned)]
        if quoted:
            parsed = quoted

    if not parsed:
        for separator in ("\n", ",", ";", "|"):
            if separator in cleaned:
                parts = [part.strip() for part in cleaned.split(separator)]
                parsed = [part for part in parts if part]
                if parsed:
                    break

    if not parsed and cleaned:
        parsed = [cleaned]

    return normalize_tags(parsed)


def _generate_via_llm(agent: PersistentAgent, charter: str, routing_profile: Any = None) -> List[str]:
    try:
        provider, model, params = get_summarization_llm_config(agent=agent, routing_profile=routing_profile)
    except Exception as exc:
        logger.warning("No summarization model available for tag generation: %s", exc)
        return []

    prompt = [
        {
            "role": "system",
            "content": (
                "You label AI agents so they are easy to discover. "
                "Given an agent charter, reply with a JSON array containing exactly three short tags. "
                "Each tag should be at most three words, written in title case, and focus on capabilities "
                "or audiences. Do not include explanations or additional text."
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
        logger.exception("LLM tag generation failed: %s", exc)
        return []

    token_usage, _ = extract_token_usage(
        response,
        model=model,
        provider=provider,
    )
    log_agent_completion(
        agent,
        token_usage,
        completion_type=PersistentAgentCompletion.CompletionType.TAG,
    )

    try:
        content = response.choices[0].message.content
    except Exception:
        logger.exception("Unexpected LiteLLM response structure when generating tags")
        return []

    return _extract_tags(content)


@shared_task(bind=True, name="api.agent.tasks.generate_agent_tags")
def generate_agent_tags_task(
    self,  # noqa: ANN001
    persistent_agent_id: str,
    charter_hash: str,
    routing_profile_id: str | None = None,
) -> None:
    """Generate and persist tags for the given agent."""
    try:
        agent = PersistentAgent.objects.get(id=persistent_agent_id)
    except PersistentAgent.DoesNotExist:
        logger.info("Skipping tag generation; agent %s no longer exists", persistent_agent_id)
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
        logger.debug("Agent %s has no charter; skipping tag generation", agent.id)
        return

    current_hash = compute_charter_hash(charter)
    if current_hash != charter_hash:
        _clear_requested_hash(agent.id, charter_hash)
        logger.debug(
            "Charter changed for agent %s before tag generation; current=%s provided=%s",
            agent.id,
            current_hash,
            charter_hash,
        )
        return

    tags = _generate_via_llm(agent, charter, routing_profile)
    PersistentAgent.objects.filter(id=agent.id).update(
        tags=tags,
        tags_charter_hash=current_hash if tags else "",
        tags_requested_hash="",
    )

    if tags:
        logger.info("Persisted %s tags for agent %s", len(tags), agent.id)
    else:
        logger.warning("Tag generation produced no tags for agent %s", agent.id)


__all__ = ["generate_agent_tags_task"]
