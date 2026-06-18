from typing import Any

from django.conf import settings

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
    if not organization_id:
        return ""
    instructions = (
        AgentOwnerCustomInstructions.objects
        .filter(organization_id=organization_id)
        .values_list("instructions", flat=True)
        .first()
    )
    return instructions or ""


def get_custom_instructions_for_user_id(user_id) -> str:
    if not user_id:
        return ""
    instructions = (
        AgentOwnerCustomInstructions.objects
        .filter(user_id=user_id)
        .values_list("instructions", flat=True)
        .first()
    )
    return instructions or ""


def save_custom_instructions_for_organization_id(organization_id, *, instructions: str, updated_by) -> None:
    _save_custom_instructions({"organization_id": organization_id}, instructions=instructions, updated_by=updated_by)


def save_custom_instructions_for_user_id(user_id, *, instructions: str, updated_by) -> None:
    _save_custom_instructions({"user_id": user_id}, instructions=instructions, updated_by=updated_by)


def _save_custom_instructions(owner_filter: dict, *, instructions: str, updated_by) -> None:
    if instructions:
        AgentOwnerCustomInstructions.objects.update_or_create(
            **owner_filter,
            defaults={
                "instructions": instructions,
                "updated_by": updated_by,
            },
        )
        return

    AgentOwnerCustomInstructions.objects.filter(**owner_filter).delete()
