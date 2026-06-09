import json
import uuid
from typing import Any

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from api.agent.tasks import process_agent_events_task
from api.models import PersistentAgent, PersistentAgentStep, PersistentAgentSystemStep
from api.services.native_integrations import NativeIntegrationProvider

NATIVE_INTEGRATION_EVENT_TYPES = {"connected", "files_selected"}
MAX_NATIVE_INTEGRATION_EVENT_FILES = 50


def resolve_native_integration_event_agent(agent_id: str, *, owner_user, owner_org) -> PersistentAgent:
    normalized_agent_id = str(agent_id or "").strip()
    if not normalized_agent_id:
        raise ValidationError({"agent_id": "agent_id is required."})
    try:
        uuid.UUID(normalized_agent_id)
    except ValueError as exc:
        raise ValidationError({"agent_id": "Invalid agent_id format."}) from exc

    queryset = PersistentAgent.objects.non_eval().alive()
    if owner_org is not None:
        queryset = queryset.filter(organization=owner_org)
    else:
        queryset = queryset.filter(user=owner_user, organization__isnull=True)

    agent = queryset.filter(id=normalized_agent_id).first()
    if agent is None:
        raise PermissionDenied("You do not have access to this agent.")
    return agent


def normalize_native_integration_event_files(raw_files: Any) -> list[dict[str, str]]:
    if raw_files in (None, ""):
        return []
    if not isinstance(raw_files, list):
        raise ValidationError({"files": "files must be a list."})
    if len(raw_files) > MAX_NATIVE_INTEGRATION_EVENT_FILES:
        raise ValidationError({"files": f"Select at most {MAX_NATIVE_INTEGRATION_EVENT_FILES} files at a time."})

    files: list[dict[str, str]] = []
    for index, raw_file in enumerate(raw_files):
        if not isinstance(raw_file, dict):
            raise ValidationError({"files": f"File entry {index + 1} must be an object."})
        external_id = str(raw_file.get("external_id") or raw_file.get("externalId") or "").strip()
        name = str(raw_file.get("name") or "").strip()
        mime_type = str(raw_file.get("mime_type") or raw_file.get("mimeType") or "").strip()
        web_url = str(raw_file.get("web_url") or raw_file.get("webUrl") or "").strip()
        if not external_id or not name or not mime_type:
            raise ValidationError({"files": f"File entry {index + 1} requires external_id, name, and mime_type."})
        files.append(
            {
                "external_id": external_id,
                "name": name,
                "mime_type": mime_type,
                "web_url": web_url,
            }
        )
    return files


def record_native_integration_agent_event(
    *,
    agent: PersistentAgent,
    provider: NativeIntegrationProvider,
    event_type: str,
    files: list[dict[str, str]] | None = None,
    source: str = "unknown",
) -> PersistentAgentStep:
    normalized_event_type = str(event_type or "").strip()
    if normalized_event_type not in NATIVE_INTEGRATION_EVENT_TYPES:
        raise ValidationError({"event_type": "event_type must be connected or files_selected."})

    normalized_files = files or []
    if normalized_event_type == "files_selected" and not normalized_files:
        raise ValidationError({"files": "files are required for files_selected events."})

    if normalized_event_type == "connected":
        description = (
            f"{provider.display_name} was connected by the user. "
            "Resume any blocked work that was waiting for this native integration."
        )
    else:
        file_names = ", ".join(file["name"] for file in normalized_files[:5])
        remaining_count = max(0, len(normalized_files) - 5)
        if remaining_count:
            file_names = f"{file_names}, and {remaining_count} more"
        description = (
            f"The user selected {len(normalized_files)} {provider.display_name} file"
            f"{'' if len(normalized_files) == 1 else 's'} for this agent: {file_names}. "
            "Use these selected file IDs for native integration work when relevant."
        )

    notes_payload = {
        "source": source,
        "provider_key": provider.key,
        "provider_name": provider.display_name,
        "event_type": normalized_event_type,
        "files": normalized_files,
    }

    with transaction.atomic():
        step = PersistentAgentStep.objects.create(agent=agent, description=description)
        PersistentAgentSystemStep.objects.create(
            step=step,
            code=PersistentAgentSystemStep.Code.SYSTEM_DIRECTIVE,
            notes=json.dumps(notes_payload, separators=(",", ":"), sort_keys=True),
        )
        agent_id_str = str(agent.id)
        transaction.on_commit(lambda: process_agent_events_task.delay(agent_id_str))
    return step
