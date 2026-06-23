from typing import Any

from django.conf import settings
from django.db import transaction

from api.models import AgentOwnerCustomInstructions


CUSTOM_INSTRUCTIONS_FIELD = "customInstructions"


class CustomInstructionsValidationError(ValueError):
    pass


def get_custom_instructions_max_chars() -> int:
    return settings.AGENT_OWNER_CUSTOM_INSTRUCTIONS_MAX_CHARS


def normalize_custom_instructions(raw_instructions: Any) -> str:
    if not isinstance(raw_instructions, str):
        raise CustomInstructionsValidationError("Custom instructions must be text.")

    normalized = raw_instructions.replace("\r\n", "\n").replace("\r", "\n").strip()
    max_chars = get_custom_instructions_max_chars()
    if len(normalized) > max_chars:
        raise CustomInstructionsValidationError(f"Custom instructions must be {max_chars} characters or fewer.")
    return normalized


def get_custom_instructions_for_organization_id(organization_id) -> str:
    query_filter, _, _ = _owner_scope("organization_id", organization_id, "user")
    return _get_custom_instructions(query_filter)


def get_custom_instructions_for_user_id(user_id) -> str:
    query_filter, _, _ = _owner_scope("user_id", user_id, "organization")
    return _get_custom_instructions(query_filter)


def _get_custom_instructions(owner_filter: dict) -> str:
    instructions = (
        AgentOwnerCustomInstructions.objects
        .filter(**owner_filter)
        .values_list("instructions", flat=True)
        .first()
    )
    return instructions or ""


def save_custom_instructions_for_organization_id(organization_id, *, instructions: str, updated_by) -> None:
    query_filter, update_lookup, invalid_owner_filter = _owner_scope("organization_id", organization_id, "user")
    _save_custom_instructions(
        query_filter,
        update_lookup,
        invalid_owner_filter,
        instructions=instructions,
        updated_by=updated_by,
    )


def save_custom_instructions_for_user_id(user_id, *, instructions: str, updated_by) -> None:
    query_filter, update_lookup, invalid_owner_filter = _owner_scope("user_id", user_id, "organization")
    _save_custom_instructions(
        query_filter,
        update_lookup,
        invalid_owner_filter,
        instructions=instructions,
        updated_by=updated_by,
    )


def _owner_scope(owner_field: str, owner_id, other_owner_field: str) -> tuple[dict, dict, dict]:
    if not owner_id:
        raise ValueError("A valid organization_id or user_id must be provided.")
    return (
        {owner_field: owner_id, f"{other_owner_field}__isnull": True},
        {owner_field: owner_id, other_owner_field: None},
        {owner_field: owner_id, f"{other_owner_field}__isnull": False},
    )


def _save_custom_instructions(
    query_filter: dict,
    update_lookup: dict,
    invalid_owner_filter: dict,
    *,
    instructions: str,
    updated_by,
) -> None:
    with transaction.atomic():
        AgentOwnerCustomInstructions.objects.filter(**invalid_owner_filter).delete()
        if instructions:
            AgentOwnerCustomInstructions.objects.update_or_create(
                **update_lookup,
                defaults={
                    "instructions": instructions,
                    "updated_by": updated_by,
                },
            )
            return

        AgentOwnerCustomInstructions.objects.filter(**query_filter).delete()
