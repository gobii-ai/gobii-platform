"""Persist an agent's stable physical identity and request a fresh avatar."""

from typing import Any, Dict

from django.core.exceptions import ValidationError
from django.db import DatabaseError

from api.evals.execution import get_current_eval_routing_profile

from ..avatar import (
    MAX_VISUAL_DESCRIPTION_LENGTH,
    compute_appearance_revision,
    maybe_schedule_agent_avatar,
    prepare_visual_description,
)
from ..eval_agents import is_eval_agent
from ..short_description import compute_charter_hash


def normalize_appearance(value: str, *, allow_blank: bool = False) -> str:
    if not isinstance(value, str):
        raise ValidationError("Appearance must be text.")
    normalized = prepare_visual_description(value, max_length=0)
    if not normalized and not allow_blank:
        raise ValidationError("Appearance must describe a stable physical identity.")
    if len(normalized) > MAX_VISUAL_DESCRIPTION_LENGTH:
        raise ValidationError(
            f"Appearance must be {MAX_VISUAL_DESCRIPTION_LENGTH} characters or fewer."
        )
    return normalized


def execute_update_appearance(agent, params: Dict[str, Any]) -> Dict[str, Any]:
    """Update physical identity without removing the current avatar while its replacement renders."""
    try:
        appearance = normalize_appearance(params.get("appearance"))
    except ValidationError as exc:
        return {"status": "error", "message": " ".join(exc.messages)}

    routing_profile = get_current_eval_routing_profile()
    routing_profile_id = str(routing_profile.id) if routing_profile else None
    avatar_state = type(agent).objects.filter(id=agent.id).values_list(
        "avatar",
        "avatar_charter_hash",
        "avatar_requested_hash",
    ).get()
    if appearance == normalize_appearance(agent.visual_description or "", allow_blank=True):
        scheduled = maybe_schedule_agent_avatar(
            agent,
            routing_profile_id=routing_profile_id,
            appearance_changed=True,
            expected_avatar_state=avatar_state,
        )
        message = "Appearance was already current."
        return _appearance_result(agent, appearance, scheduled, message)

    charter_hash = compute_charter_hash(agent.charter or "")
    agent.visual_description = appearance
    agent.visual_description_charter_hash = charter_hash
    agent.visual_description_requested_hash = ""
    try:
        agent.save(
            update_fields=[
                "visual_description",
                "visual_description_charter_hash",
                "visual_description_requested_hash",
            ]
        )
    except DatabaseError as exc:
        return {"status": "error", "message": f"Failed to update appearance: {exc}"}

    scheduled = maybe_schedule_agent_avatar(
        agent,
        routing_profile_id=routing_profile_id,
        appearance_changed=True,
        expected_avatar_state=avatar_state,
    )
    return _appearance_result(agent, appearance, scheduled, "Appearance updated successfully.")


def _appearance_result(agent, appearance: str, scheduled: bool, message: str) -> Dict[str, Any]:
    if scheduled or is_eval_agent(agent):
        return {"status": "ok", "message": message}
    revision = compute_appearance_revision(agent.charter or "", appearance)
    state = type(agent).objects.filter(id=agent.id).values_list(
        "avatar_charter_hash",
        "avatar_requested_hash",
    ).get()
    if revision in state:
        return {"status": "ok", "message": message}
    return {
        "status": "ok",
        "message": message,
        "warning": (
            "Appearance was saved, but the avatar refresh was not queued; retry later if needed."
        ),
    }


__all__ = ["execute_update_appearance", "normalize_appearance"]
