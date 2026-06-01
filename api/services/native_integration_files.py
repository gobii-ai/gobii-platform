from dataclasses import dataclass
from typing import Iterable

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from api.models import NativeIntegrationGrantedFile, PersistentAgent
from api.services.native_integrations import GOOGLE_DRIVE_PROVIDER, get_native_integration_provider
from api.services.persistent_agent_secrets import resolve_global_secret_owner_for_agent


GOOGLE_SHEETS_MIME_TYPE = "application/vnd.google-apps.spreadsheet"
GOOGLE_DOCS_MIME_TYPE = "application/vnd.google-apps.document"
GOOGLE_DRIVE_PICKER_MIME_TYPES = frozenset({GOOGLE_SHEETS_MIME_TYPE, GOOGLE_DOCS_MIME_TYPE})
GOOGLE_SHEETS_PROMPT_LIMIT = 20


@dataclass(frozen=True)
class NativeIntegrationFileSelection:
    external_file_id: str
    name: str
    mime_type: str
    url: str = ""


def native_integration_granted_file_queryset(owner_user, owner_org, provider_key: str | None = None):
    queryset = NativeIntegrationGrantedFile.objects.all()
    if owner_org is not None:
        queryset = queryset.filter(organization=owner_org)
    else:
        queryset = queryset.filter(user=owner_user, organization__isnull=True)
    if provider_key:
        provider = get_native_integration_provider(provider_key)
        queryset = queryset.filter(provider_key=provider.key)
    return queryset


def _coerce_selection(payload: object) -> NativeIntegrationFileSelection:
    if not isinstance(payload, dict):
        raise ValidationError("Each file must be an object.")

    external_file_id = str(payload.get("external_file_id") or payload.get("id") or "").strip()
    name = str(payload.get("name") or "").strip()
    mime_type = str(payload.get("mime_type") or "").strip()
    url = str(payload.get("url") or "").strip()

    errors = {}
    if not external_file_id:
        errors["external_file_id"] = "This field is required."
    if not name:
        errors["name"] = "This field is required."
    if not mime_type:
        errors["mime_type"] = "This field is required."
    if mime_type and mime_type not in GOOGLE_DRIVE_PICKER_MIME_TYPES:
        errors["mime_type"] = "Unsupported Google Drive file type."
    if errors:
        raise ValidationError(errors)

    return NativeIntegrationFileSelection(
        external_file_id=external_file_id[:255],
        name=name[:512],
        mime_type=mime_type[:255],
        url=url,
    )


def upsert_native_integration_granted_files(
    provider_key: str,
    owner_user,
    owner_org,
    files: Iterable[object],
    *,
    selected_by=None,
) -> list[NativeIntegrationGrantedFile]:
    provider = get_native_integration_provider(provider_key)
    if provider.key != GOOGLE_DRIVE_PROVIDER.key:
        raise ValidationError("This native integration does not support selected files.")

    selections = [_coerce_selection(file_payload) for file_payload in files]
    selected_at = timezone.now()
    saved_files: list[NativeIntegrationGrantedFile] = []

    with transaction.atomic():
        for selection in selections:
            lookup = {
                "provider_key": provider.key,
                "external_file_id": selection.external_file_id,
            }
            if owner_org is not None:
                lookup["organization"] = owner_org
            else:
                lookup["user"] = owner_user
                lookup["organization__isnull"] = True

            granted_file = NativeIntegrationGrantedFile.objects.filter(**lookup).first()
            if granted_file is None:
                granted_file = NativeIntegrationGrantedFile(
                    user=None if owner_org is not None else owner_user,
                    organization=owner_org,
                    provider_key=provider.key,
                    external_file_id=selection.external_file_id,
                )

            granted_file.name = selection.name
            granted_file.mime_type = selection.mime_type
            granted_file.url = selection.url
            granted_file.selected_by = selected_by
            granted_file.last_selected_at = selected_at
            granted_file.save()
            saved_files.append(granted_file)

    return saved_files


def serialize_native_integration_granted_file(granted_file: NativeIntegrationGrantedFile) -> dict[str, object]:
    return {
        "id": str(granted_file.id),
        "provider_key": granted_file.provider_key,
        "external_file_id": granted_file.external_file_id,
        "name": granted_file.name,
        "mime_type": granted_file.mime_type,
        "url": granted_file.url,
        "last_selected_at": granted_file.last_selected_at.isoformat(),
        "selected_by_id": str(granted_file.selected_by_id) if granted_file.selected_by_id else None,
    }


def list_google_sheets_for_agent(agent: PersistentAgent, *, limit: int = GOOGLE_SHEETS_PROMPT_LIMIT):
    owner_user, owner_org = resolve_global_secret_owner_for_agent(agent)
    return list(
        native_integration_granted_file_queryset(owner_user, owner_org, GOOGLE_DRIVE_PROVIDER.key)
        .filter(mime_type=GOOGLE_SHEETS_MIME_TYPE)
        .order_by("-last_selected_at", "name", "external_file_id")[:limit]
    )


def count_google_sheets_for_agent(agent: PersistentAgent) -> int:
    owner_user, owner_org = resolve_global_secret_owner_for_agent(agent)
    return native_integration_granted_file_queryset(owner_user, owner_org, GOOGLE_DRIVE_PROVIDER.key).filter(
        mime_type=GOOGLE_SHEETS_MIME_TYPE,
    ).count()


def format_google_sheets_access_for_prompt(agent: PersistentAgent, *, limit: int = GOOGLE_SHEETS_PROMPT_LIMIT) -> str:
    sheets = list_google_sheets_for_agent(agent, limit=limit)
    if not sheets:
        return (
            "Accessible Google Sheets selected through Google Drive:\n"
            "- None recorded. With `drive.file` access, ask the user to choose the spreadsheet in the Google Drive "
            "native integration before trying to access an unlisted spreadsheet."
        )

    lines = ["Accessible Google Sheets selected through Google Drive:"]
    for sheet in sheets:
        detail = f"id: {sheet.external_file_id}"
        if sheet.url:
            detail = f"{detail}, url: {sheet.url}"
        lines.append(f"- {sheet.name} ({detail})")

    total_count = count_google_sheets_for_agent(agent)
    if total_count > len(sheets):
        lines.append(f"- {total_count - len(sheets)} additional spreadsheet(s) omitted from this prompt.")
    return "\n".join(lines)
