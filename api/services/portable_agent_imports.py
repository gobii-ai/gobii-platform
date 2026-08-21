import hashlib
import os
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.storage import storages
from django.db import transaction
from django.utils import timezone
from django.utils.text import get_valid_filename

from agents.services import AgentService
from api.agent.tools.sqlite_state import sqlite_storage_key
from api.models import (
    AgentFileSpace,
    OrganizationMembership,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentMessageAttachment,
    PortableAgentImport,
    PortableAgentImportArtifactCleanup,
    PortableAgentImportItem,
)
from api.services.owner_execution_pause import get_owner_account_pause_state
from api.services.organization_permissions import user_role_can_create_org_agents
from api.services.persistent_agents import (
    PersistentAgentProvisioningError,
    PersistentAgentProvisioningService,
    lock_agent_creation_owner,
)
from api.services.portable_agent_exports import STORAGE_ERRORS
from console.context_helpers import build_console_context
from constants.feature_flags import PORTABLE_AGENT_IMPORTS
from util.trial_enforcement import (
    PERSONAL_USAGE_REQUIRES_TRIAL_MESSAGE,
    can_user_use_personal_agents_and_api,
)
from util.waffle_flags import is_waffle_flag_active


def portable_agent_import_storage():
    return storages["portable_agent_imports"]


def portable_agent_imports_enabled(request=None) -> bool:
    if not settings.PORTABLE_AGENT_IMPORTS_ENABLED:
        return False
    if not settings.GOBII_PROPRIETARY_MODE:
        return True
    return is_waffle_flag_active(PORTABLE_AGENT_IMPORTS, request, default=False)


def delete_portable_agent_import_artifact(
    storage_key: str,
    *,
    import_id=None,
    storage_alias: str = "portable_agent_imports",
) -> bool:
    if not storage_key:
        return True
    storage = storages[storage_alias]
    try:
        if storage.exists(storage_key):
            storage.delete(storage_key)
    except STORAGE_ERRORS:
        if import_id is not None:
            PortableAgentImportArtifactCleanup.objects.get_or_create(
                storage_alias=storage_alias,
                storage_key=storage_key,
                defaults={"source_import_id": import_id},
            )
        return False
    PortableAgentImportArtifactCleanup.objects.filter(
        storage_alias=storage_alias,
        storage_key=storage_key,
    ).delete()
    return True


def try_portable_agent_import_artifact_cleanup(cleanup_id) -> bool:
    cleanup = PortableAgentImportArtifactCleanup.objects.filter(pk=cleanup_id).first()
    if cleanup is None:
        return True
    return delete_portable_agent_import_artifact(
        cleanup.storage_key,
        import_id=cleanup.source_import_id,
        storage_alias=cleanup.storage_alias,
    )


def retry_portable_agent_import_artifact_cleanups() -> int:
    cleaned = 0
    cleanup_ids = list(PortableAgentImportArtifactCleanup.objects.values_list("id", flat=True))
    for cleanup_id in cleanup_ids:
        if try_portable_agent_import_artifact_cleanup(cleanup_id):
            cleaned += 1
    return cleaned


def resolve_portable_import_target(request):
    context = build_console_context(request)
    if context.current_context.type == PortableAgentImport.TargetType.ORGANIZATION:
        membership = context.current_membership
        if membership is None or not context.can_create_org_agents:
            raise PermissionDenied("You do not have permission to import agents into this organization.")
        organization = membership.org
        return (
            PortableAgentImport.TargetType.ORGANIZATION,
            f"organization:{organization.id}",
            organization,
        )
    return (
        PortableAgentImport.TargetType.PERSONAL,
        f"personal:{request.user.id}",
        None,
    )


def user_can_import_to_target(user, job: PortableAgentImport) -> bool:
    if not user.is_authenticated or job.requester_id != user.id:
        return False
    if job.target_type == PortableAgentImport.TargetType.PERSONAL:
        return job.organization_id is None
    if not job.organization_id:
        return False
    membership = OrganizationMembership.objects.select_related("org").filter(
        user=user,
        org_id=job.organization_id,
        status=OrganizationMembership.OrgStatus.ACTIVE,
    ).first()
    return bool(membership and user_role_can_create_org_agents(membership.role, membership.org))


def validate_import_creation_eligibility(job: PortableAgentImport) -> None:
    if not user_can_import_to_target(job.requester, job):
        raise PermissionDenied("You no longer have permission to import agents into this workspace.")
    owner = job.organization or job.requester
    if get_owner_account_pause_state(owner).get("customer_paused"):
        raise ValidationError("Agent creation is unavailable while this account is paused.")
    if job.target_type == PortableAgentImport.TargetType.PERSONAL and settings.GOBII_PROPRIETARY_MODE:
        if not can_user_use_personal_agents_and_api(job.requester):
            raise ValidationError(PERSONAL_USAGE_REQUIRES_TRIAL_MESSAGE)
    elif settings.GOBII_PROPRIETARY_MODE:
        billing = getattr(job.organization, "billing", None)
        if not billing or billing.purchased_seats <= 0:
            raise ValidationError("This organization needs an agent seat before importing agents.")


def create_portable_agent_import(request, uploaded_file) -> PortableAgentImport:
    target_type, target_key, organization = resolve_portable_import_target(request)
    size = getattr(uploaded_file, "size", None)
    if size is not None and size > settings.PORTABLE_AGENT_IMPORT_MAX_ARCHIVE_BYTES:
        raise ValidationError("The ZIP exceeds the portable-agent import size limit.")

    digest = hashlib.sha256()
    counted = 0
    for chunk in uploaded_file.chunks():
        counted += len(chunk)
        if counted > settings.PORTABLE_AGENT_IMPORT_MAX_ARCHIVE_BYTES:
            raise ValidationError("The ZIP exceeds the portable-agent import size limit.")
        digest.update(chunk)
    try:
        uploaded_file.seek(0)
    except (AttributeError, OSError) as exc:
        raise ValidationError("The uploaded ZIP could not be read.") from exc

    raw_filename = os.path.basename(str(getattr(uploaded_file, "name", "") or "agent-export.zip"))
    filename = (get_valid_filename(raw_filename) or "agent-export.zip")[:255]
    if not filename.lower().endswith(".zip"):
        raise ValidationError("Choose a Gobii portable-export ZIP file.")

    job = PortableAgentImport.objects.create(
        requester=request.user,
        target_type=target_type,
        target_key=target_key,
        organization=organization,
        archive_filename=filename,
        archive_size_bytes=counted,
        archive_sha256=digest.hexdigest(),
        expires_at=timezone.now() + timedelta(hours=settings.PORTABLE_AGENT_IMPORT_SELECTION_TTL_HOURS),
    )
    try:
        validate_import_creation_eligibility(job)
    except (PermissionDenied, ValidationError):
        job.delete()
        raise
    storage_key = f"portable_agent_imports/{timezone.now():%Y/%m}/{job.id}.zip"
    try:
        saved_key = portable_agent_import_storage().save(storage_key, uploaded_file)
    except STORAGE_ERRORS:
        job.delete()
        raise ValidationError("The uploaded ZIP could not be stored. Please try again.")
    job.storage_key = saved_key
    job.save(update_fields=["storage_key", "updated_at"])
    return job


def target_owner(job: PortableAgentImport):
    return job.organization or job.requester


def available_import_capacity(job: PortableAgentImport) -> int:
    if not user_can_import_to_target(job.requester, job):
        return 0
    return AgentService.get_agents_available(target_owner(job))


def _target_payload(job: PortableAgentImport) -> dict:
    if job.organization_id and job.organization:
        return {
            "type": PortableAgentImport.TargetType.ORGANIZATION,
            "id": str(job.organization_id),
            "name": job.organization.name,
        }
    user = job.requester
    return {
        "type": PortableAgentImport.TargetType.PERSONAL,
        "id": str(user.id),
        "name": user.get_full_name() or user.username or user.email or "Personal",
    }


def serialize_portable_agent_import(job: PortableAgentImport, *, include_agents: bool = True) -> dict:
    payload = {
        "id": str(job.id),
        "status": job.status,
        "target": _target_payload(job),
        "formatVersion": job.format_version or None,
        "archiveName": job.archive_filename,
        "archiveSizeBytes": job.archive_size_bytes,
        "agentsTotal": job.total_agents,
        "agentsSelected": job.selected_agents,
        "agentsCompleted": job.completed_agents,
        "agentsFailed": job.failed_agents,
        "warningCount": job.warning_count,
        "capacityAvailable": available_import_capacity(job),
        "error": job.error_message or None,
        "createdAt": job.created_at.isoformat(),
        "expiresAt": job.expires_at.isoformat() if job.expires_at else None,
    }
    if not include_agents:
        return payload
    items = job.items.select_related("imported_agent").order_by("source_agent_name", "source_agent_id")
    payload["agents"] = [
            {
                "id": str(item.id),
                "sourceAgentId": str(item.source_agent_id),
                "sourceName": item.source_agent_name,
                "proposedName": item.requested_name or item.source_agent_name,
                "snapshotAt": item.snapshot_at.isoformat() if item.snapshot_at else None,
                "status": item.status,
                "selectable": item.status in {
                    PortableAgentImportItem.Status.AVAILABLE,
                    PortableAgentImportItem.Status.SELECTED,
                },
                "messageCount": item.message_count,
                "stepCount": item.step_count,
                "fileCount": item.file_count,
                "warningCount": len(item.warnings) if isinstance(item.warnings, list) else 0,
                "warnings": item.warnings if isinstance(item.warnings, list) else [],
                "error": item.error_message or None,
                "importedAgent": (
                    {
                        "id": str(item.imported_agent_id),
                        "name": item.imported_agent.name,
                        "url": f"/app/agents/{item.imported_agent_id}",
                    }
                    if item.imported_agent_id and item.imported_agent
                    else None
                ),
            }
            for item in items
        ]
    return payload


def expire_portable_agent_import(job: PortableAgentImport) -> None:
    storage_key = job.storage_key
    deleted = delete_portable_agent_import_artifact(storage_key, import_id=job.id)
    job.status = PortableAgentImport.Status.EXPIRED
    job.expires_at = None
    job.completed_at = timezone.now()
    if deleted:
        job.storage_key = ""
    job.save(update_fields=[
        "status", "expires_at", "completed_at", "storage_key", "updated_at",
    ])


def discard_portable_agent_import(job: PortableAgentImport) -> None:
    if job.status in {
        PortableAgentImport.Status.VALIDATING,
        PortableAgentImport.Status.QUEUED,
        PortableAgentImport.Status.RUNNING,
    }:
        raise ValidationError("An import in progress cannot be discarded.")
    storage_key = job.storage_key
    with transaction.atomic():
        job.storage_key = ""
        job.save(update_fields=["storage_key", "updated_at"])
        job.delete()
    delete_portable_agent_import_artifact(storage_key, import_id=job.id)


def _validate_requested_name(raw_name) -> str:
    name = str(raw_name or "").strip()
    if not name:
        raise ValidationError("Every selected agent needs a name.")
    if len(name) > 255:
        raise ValidationError("Agent names must be 255 characters or fewer.")
    return name


def reserve_portable_agent_shells(job: PortableAgentImport, selections: list[dict]) -> bool:
    """Reserve inactive shells atomically; return False for an already-started job."""
    if not isinstance(selections, list) or not selections:
        raise ValidationError("Select at least one agent to import.")

    selection_by_id: dict[str, str] = {}
    normalized_names: set[str] = set()
    for selection in selections:
        if not isinstance(selection, dict):
            raise ValidationError("The selected agent list is malformed.")
        item_id = str(selection.get("itemId") or "")
        if not item_id or item_id in selection_by_id:
            raise ValidationError("The selected agent list contains a duplicate or missing item.")
        name = _validate_requested_name(selection.get("name"))
        normalized = name.casefold()
        if normalized in normalized_names:
            raise ValidationError("Selected agents must have different names.")
        normalized_names.add(normalized)
        selection_by_id[item_id] = name

    with transaction.atomic():
        # PostgreSQL cannot lock the nullable organization side of this outer join.
        locked_job = PortableAgentImport.objects.select_for_update(of=("self",)).select_related(
            "requester", "organization",
        ).get(pk=job.pk)
        if locked_job.status != PortableAgentImport.Status.AWAITING_SELECTION:
            return False
        if locked_job.expires_at and locked_job.expires_at <= timezone.now():
            raise ValidationError("This upload expired. Upload the export again.")
        validate_import_creation_eligibility(locked_job)
        lock_agent_creation_owner(target_owner(locked_job))

        items = {
            str(item.id): item
            for item in PortableAgentImportItem.objects.select_for_update().filter(
                import_job=locked_job,
                status=PortableAgentImportItem.Status.AVAILABLE,
            )
        }
        if set(selection_by_id) - set(items):
            raise ValidationError("One or more selected agents are unavailable.")
        if AgentService.get_agents_available(target_owner(locked_job)) < len(selection_by_id):
            raise ValidationError("This workspace does not have enough available agent capacity.")

        existing_queryset = PersistentAgent.objects.non_eval().filter(is_deleted=False)
        if locked_job.organization_id:
            existing_queryset = existing_queryset.filter(organization_id=locked_job.organization_id)
        else:
            existing_queryset = existing_queryset.filter(
                organization__isnull=True,
                user_id=locked_job.requester_id,
            )
        existing_names = set(existing_queryset.values_list("name", flat=True))
        existing_normalized = {name.casefold() for name in existing_names}
        conflicts = normalized_names & existing_normalized
        if conflicts:
            raise ValidationError("An agent with one of these names already exists in this workspace.")

        for item_id, name in selection_by_id.items():
            item = items[item_id]
            browser_agent_name = name
            if locked_job.requester.agents.filter(name=browser_agent_name).exists():
                browser_agent_name = PersistentAgentProvisioningService.generate_unique_name(locked_job.requester)
            try:
                result = PersistentAgentProvisioningService.provision(
                    user=locked_job.requester,
                    organization=locked_job.organization,
                    name=name,
                    charter="",
                    schedule=None,
                    is_active=False,
                    life_state=PersistentAgent.LifeState.ACTIVE,
                    planning_state=PersistentAgent.PlanningState.SKIPPED,
                    generate_charter_artifacts=False,
                    create_onboarding_schedule=False,
                    browser_agent_name=browser_agent_name,
                    _owner_lock_held=True,
                )
            except PersistentAgentProvisioningError as exc:
                raise ValidationError(str(exc)) from exc
            item.requested_name = name
            item.imported_agent = result.agent
            item.status = PortableAgentImportItem.Status.SELECTED
            item.save(update_fields=["requested_name", "imported_agent", "status", "updated_at"])

        locked_job.items.filter(status=PortableAgentImportItem.Status.AVAILABLE).update(
            status=PortableAgentImportItem.Status.SKIPPED,
        )
        locked_job.status = PortableAgentImport.Status.QUEUED
        locked_job.selected_agents = len(selection_by_id)
        locked_job.started_at = timezone.now()
        locked_job.expires_at = None
        locked_job.error_code = ""
        locked_job.error_message = ""
        locked_job.save(update_fields=[
            "status", "selected_agents", "started_at", "expires_at",
            "error_code", "error_message", "updated_at",
        ])
    return True


def delete_failed_import_shells(
    job: PortableAgentImport,
    *,
    failed_item: PortableAgentImportItem | None = None,
) -> None:
    failed_items = (
        PortableAgentImportItem.objects.filter(
            pk=failed_item.pk,
            status=PortableAgentImportItem.Status.FAILED,
        ).select_related("imported_agent")
        if failed_item is not None
        else job.items.filter(status=PortableAgentImportItem.Status.FAILED).select_related("imported_agent")
    )
    for item in failed_items:
        agent = item.imported_agent
        if agent is None or agent.is_active:
            continue
        agent_id = agent.id
        browser_agent = agent.browser_use_agent
        filespaces = list(agent.filespaces.all())
        storage_names = list(
            agent.created_nodes.exclude(content="").values_list("content", flat=True)
        )
        for filespace in filespaces:
            storage_names.extend(
                filespace.nodes.exclude(content="").values_list("content", flat=True)
            )
        storage_names.extend(
            PersistentAgentMessageAttachment.objects.filter(message__owner_agent=agent)
            .exclude(file="")
            .values_list("file", flat=True)
        )
        if agent.avatar and agent.avatar.name:
            storage_names.append(agent.avatar.name)
        storage_names.append(sqlite_storage_key(str(agent_id)))
        item.imported_agent = None
        item.save(update_fields=["imported_agent", "updated_at"])
        AgentFileSpace.objects.filter(pk__in=[filespace.pk for filespace in filespaces]).delete()
        agent.delete()
        browser_agent.delete()
        PersistentAgentCommsEndpoint.objects.filter(
            address__startswith=f"portable-import://{agent_id}/",
        ).delete()
        for storage_name in {name for name in storage_names if name}:
            delete_portable_agent_import_artifact(
                storage_name,
                import_id=job.id,
                storage_alias="default",
            )
