import logging
from urllib.parse import urlencode

from botocore.exceptions import BotoCoreError, ClientError
from google.cloud.exceptions import GoogleCloudError
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.storage import storages
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from api.models import (
    OrganizationMembership,
    PersistentAgent,
    PortableAgentExport,
    PortableAgentExportArtifactCleanup,
    PortableAgentExportItem,
)
from api.services.organization_permissions import ORG_AGENT_CONFIG_AUTHORITY_ROLES
from console.context_helpers import build_console_context


logger = logging.getLogger(__name__)

DOWNLOAD_TOKEN_SALT = "portable-agent-export-download-v1"
STORAGE_ERRORS = (BotoCoreError, ClientError, GoogleCloudError, OSError, ValueError)


def portable_agent_export_storage():
    return storages["portable_agent_exports"]


def delete_portable_agent_export_artifact(storage_key: str, *, export_id=None) -> bool:
    if not storage_key:
        return True
    storage = portable_agent_export_storage()
    try:
        if storage.exists(storage_key):
            storage.delete(storage_key)
    except STORAGE_ERRORS as exc:
        logger.warning(
            "Could not delete portable export artifact %s error=%s",
            export_id or storage_key,
            type(exc).__name__,
        )
        return False
    return True


def try_portable_agent_export_artifact_cleanup(cleanup_id) -> bool:
    cleanup = PortableAgentExportArtifactCleanup.objects.filter(pk=cleanup_id).first()
    if cleanup is None:
        return True
    if not delete_portable_agent_export_artifact(
        cleanup.storage_key,
        export_id=cleanup.source_export_id,
    ):
        return False
    cleanup.delete()
    return True


def retry_portable_agent_export_artifact_cleanups() -> int:
    deleted = 0
    cleanup_ids = PortableAgentExportArtifactCleanup.objects.values_list(
        "id",
        flat=True,
    ).iterator(chunk_size=100)
    for cleanup_id in cleanup_ids:
        if try_portable_agent_export_artifact_cleanup(cleanup_id):
            deleted += 1
    return deleted


def user_can_export_agent(user, agent: PersistentAgent) -> bool:
    """Use natural ownership only; staff viewing overrides must never grant migration access."""
    if (
        not getattr(user, "is_authenticated", False)
        or agent is None
        or agent.is_deleted
        or agent.execution_environment == "eval"
    ):
        return False
    if agent.organization_id is None:
        return agent.user_id == user.id
    return OrganizationMembership.objects.filter(
        user=user,
        org_id=agent.organization_id,
        status=OrganizationMembership.OrgStatus.ACTIVE,
        role__in=ORG_AGENT_CONFIG_AUTHORITY_ROLES,
    ).exists()


def user_can_access_export(user, export: PortableAgentExport) -> bool:
    if not getattr(user, "is_authenticated", False) or export.requester_id != user.id:
        return False
    if export.scope == PortableAgentExport.Scope.PERSONAL:
        return not export.items.filter(status=PortableAgentExportItem.Status.READY).filter(
            Q(agent__isnull=True)
            | Q(agent__is_deleted=True)
            | Q(agent__execution_environment="eval")
            | ~Q(agent__user_id=user.id, agent__organization__isnull=True)
        ).exists()
    if export.scope == PortableAgentExport.Scope.ORGANIZATION:
        if not export.organization_id:
            return False
        has_scope_access = OrganizationMembership.objects.filter(
            user=user,
            org_id=export.organization_id,
            status=OrganizationMembership.OrgStatus.ACTIVE,
            role__in=ORG_AGENT_CONFIG_AUTHORITY_ROLES,
        ).exists()
        if not has_scope_access:
            return False
        return not export.items.filter(status=PortableAgentExportItem.Status.READY).filter(
            Q(agent__isnull=True)
            | Q(agent__is_deleted=True)
            | Q(agent__execution_environment="eval")
            | ~Q(agent__organization_id=export.organization_id)
        ).exists()
    return bool(export.agent_id and user_can_export_agent(user, export.agent))


def _agent_folder_name(agent: PersistentAgent) -> str:
    name = slugify(agent.name or "agent")[:80] or "agent"
    return f"{name}--{str(agent.id).replace('-', '')[:8]}"


def resolve_portable_export_scope(request, scope: str, agent_id=None, *, include_agents: bool = True):
    user = request.user
    if scope == PortableAgentExport.Scope.AGENT:
        if not agent_id:
            raise ValidationError({"agentId": "agentId is required for an agent export."})
        agent = PersistentAgent.objects.non_eval().alive().select_related("organization", "user").filter(pk=agent_id).first()
        if agent is None:
            raise ValidationError({"agentId": "Agent not found."})
        if not user_can_export_agent(user, agent):
            raise PermissionDenied("You do not have permission to export this agent.")
        return f"agent:{agent.id}", agent, agent.organization, [agent] if include_agents else []

    if scope == PortableAgentExport.Scope.PERSONAL:
        agents = list(
            PersistentAgent.objects.non_eval().alive()
            .filter(user=user, organization__isnull=True)
            .select_related("organization", "user")
            .order_by("name", "id")
        ) if include_agents else []
        return f"personal:{user.id}", None, None, agents

    if scope == PortableAgentExport.Scope.ORGANIZATION:
        context = build_console_context(request)
        membership = context.current_membership
        if context.current_context.type != "organization" or membership is None:
            raise ValidationError({"scope": "Switch to the organization you want to export."})
        if membership.role not in ORG_AGENT_CONFIG_AUTHORITY_ROLES:
            raise PermissionDenied("You do not have permission to export this organization's agents.")
        organization = membership.org
        agents = list(
            PersistentAgent.objects.non_eval().alive()
            .filter(organization=organization)
            .select_related("organization", "user")
            .order_by("name", "id")
        ) if include_agents else []
        return f"organization:{organization.id}", None, organization, agents

    raise ValidationError({"scope": "scope must be agent, personal, or organization."})


def create_portable_agent_export(request, *, scope: str, agent_id=None) -> tuple[PortableAgentExport, bool]:
    scope_key, agent, organization, agents = resolve_portable_export_scope(request, scope, agent_id)
    if not agents:
        raise ValidationError({"scope": "There are no agents to export in this workspace."})

    try:
        with transaction.atomic():
            active = (
                PortableAgentExport.objects.select_for_update()
                .filter(
                    requester=request.user,
                    scope_key=scope_key,
                    status__in=PortableAgentExport.ACTIVE_STATUSES,
                )
                .first()
            )
            if active is not None:
                return active, False
            export = PortableAgentExport.objects.create(
                requester=request.user,
                scope=scope,
                scope_key=scope_key,
                agent=agent,
                organization=organization,
                total_agents=len(agents),
            )
            PortableAgentExportItem.objects.bulk_create([
                PortableAgentExportItem(
                    export=export,
                    agent=current,
                    source_agent_id=current.id,
                    source_agent_name=current.name or "Agent",
                    folder_name=_agent_folder_name(current),
                )
                for current in agents
            ])
    except IntegrityError:
        active = PortableAgentExport.objects.get(
            requester=request.user,
            scope_key=scope_key,
            status__in=PortableAgentExport.ACTIVE_STATUSES,
        )
        return active, False
    return export, True


def build_download_token(export: PortableAgentExport) -> str:
    return signing.dumps(
        {
            "exportId": str(export.id),
            "requesterId": export.requester_id,
            "expiresAt": int(export.expires_at.timestamp()) if export.expires_at else None,
        },
        salt=DOWNLOAD_TOKEN_SALT,
        compress=True,
    )


def load_download_token(token: str, *, max_age_seconds: int) -> dict:
    payload = signing.loads(token, salt=DOWNLOAD_TOKEN_SALT, max_age=max_age_seconds)
    if not isinstance(payload, dict):
        raise signing.BadSignature("Invalid export download token.")
    return payload


def serialize_portable_agent_export(export: PortableAgentExport) -> dict:
    ready = export.status in PortableAgentExport.READY_STATUSES
    download_url = None
    if ready and export.storage_key and export.expires_at and export.expires_at > timezone.now():
        query = urlencode({"token": build_download_token(export)})
        download_url = f"{reverse('console_portable_agent_export_download', args=[export.id])}?{query}"
    return {
        "id": str(export.id),
        "scope": export.scope,
        "status": export.status,
        "phase": export.phase,
        "agentsTotal": export.total_agents,
        "agentsCompleted": export.completed_agents,
        "agentsFailed": export.failed_agents,
        "warningCount": export.warning_count,
        "redactionCount": export.redaction_count,
        "archiveSizeBytes": export.archive_size_bytes,
        "error": export.error_message or None,
        "createdAt": export.created_at.isoformat(),
        "expiresAt": export.expires_at.isoformat() if export.expires_at else None,
        "downloadUrl": download_url,
    }


def revoke_export_artifact(export: PortableAgentExport, *, code: str = "access_revoked") -> None:
    storage_key = export.storage_key
    artifact_deleted = delete_portable_agent_export_artifact(storage_key, export_id=export.id)
    export.status = PortableAgentExport.Status.FAILED
    export.phase = "revoked"
    export.expires_at = None
    export.error_code = code
    export.error_message = "Access to this export is no longer available."
    update_fields = [
        "status", "phase", "storage_key", "expires_at", "error_code", "error_message", "updated_at",
    ]
    if artifact_deleted:
        export.storage_key = ""
        export.archive_size_bytes = None
        export.archive_sha256 = ""
        update_fields.extend(["archive_size_bytes", "archive_sha256"])
    export.save(update_fields=update_fields)


def expire_export_artifact(export: PortableAgentExport) -> bool:
    if not delete_portable_agent_export_artifact(export.storage_key, export_id=export.id):
        return False
    export.status = PortableAgentExport.Status.EXPIRED
    export.phase = "expired"
    export.storage_key = ""
    export.archive_size_bytes = None
    export.archive_sha256 = ""
    export.save(update_fields=[
        "status", "phase", "storage_key", "archive_size_bytes", "archive_sha256", "updated_at",
    ])
    return True
