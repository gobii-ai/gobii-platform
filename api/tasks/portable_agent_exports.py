import hashlib
import html
import logging
import os
import shutil
import smtplib
import sqlite3
import tempfile
import zipfile
from datetime import timedelta
from pathlib import Path

from anymail.exceptions import AnymailError
from celery import shared_task
from django.conf import settings
from django.core.files import File
from django.core.mail import send_mail
from django.db import DatabaseError, transaction
from django.db.models import F
from django.urls import reverse
from django.utils import timezone
from django.utils.text import get_valid_filename

from api.agent.comms.email_endpoint_routing import resolve_agent_email_sender_endpoint
from api.agent.comms.outbound_delivery import deliver_agent_email
from api.agent.tools.sqlite_recovery import SQLiteStateError
from api.models import (
    CommsChannel,
    DeliveryStatus,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentMessage,
    PortableAgentExport,
    PortableAgentExportItem,
)
from api.services.agent_sqlite_coordination import AgentSQLiteBusy
from api.services.email_verification import has_verified_email_address
from api.services.portable_agent_export_archive import PortableAgentArchiveBuilder, write_json_file
from api.services.portable_agent_exports import (
    STORAGE_ERRORS,
    build_download_token,
    delete_portable_agent_export_artifact,
    expire_export_artifact,
    portable_agent_export_storage,
    revoke_export_artifact,
    user_can_access_export,
    user_can_export_agent,
)
from util.analytics import Analytics, AnalyticsEvent


logger = logging.getLogger(__name__)

SAFE_FAILURE_MESSAGE = "The export could not be completed. Please try again."
ARCHIVE_BUILD_ERRORS = (
    AttributeError,
    *STORAGE_ERRORS,
    KeyError,
    RuntimeError,
    TypeError,
    sqlite3.Error,
    DatabaseError,
    SQLiteStateError,
    AgentSQLiteBusy,
)


def _duration_seconds(export: PortableAgentExport, *, finished_at=None) -> float:
    finished = finished_at or timezone.now()
    return max(0.0, (finished - export.created_at).total_seconds())


def mark_portable_agent_export_failed(export: PortableAgentExport, *, code: str, message: str | None = None) -> None:
    export.status = PortableAgentExport.Status.FAILED
    export.phase = "failed"
    export.error_code = code[:64]
    export.error_message = (message or SAFE_FAILURE_MESSAGE)[:512]
    export.completed_at = timezone.now()
    export.save(update_fields=[
        "status", "phase", "error_code", "error_message", "completed_at", "updated_at",
    ])
    Analytics.track(
        user_id=export.requester_id,
        event=AnalyticsEvent.AGENT_PORTABLE_EXPORT_FAILED,
        properties={
            "export_id": str(export.id),
            "scope": export.scope,
            "error_code": code[:64],
            "duration_seconds": _duration_seconds(export, finished_at=export.completed_at),
            "agent_count": export.total_agents,
            "agents_completed": export.completed_agents,
            "agents_failed": export.failed_agents,
            "warning_count": export.warning_count,
            "redaction_count": export.redaction_count,
        },
        user=export.requester,
    )


def _write_root_files(staging: Path, export: PortableAgentExport, items, results) -> None:
    successful = [item for item in items if item.status == PortableAgentExportItem.Status.READY]
    failed = [item for item in items if item.status == PortableAgentExportItem.Status.FAILED]
    manifest = {
        "formatVersion": export.format_version,
        "exportId": str(export.id),
        "scope": export.scope,
        "requestedAt": export.created_at.isoformat(),
        "generatedAt": timezone.now().isoformat(),
        "agents": [
            {
                "id": str(item.source_agent_id),
                "name": (
                    results[str(item.id)].display_name
                    if str(item.id) in results
                    else "Unavailable agent"
                ),
                "folder": f"agents/{item.folder_name}" if item.status == PortableAgentExportItem.Status.READY else None,
                "status": item.status,
                "snapshotAt": item.snapshot_at.isoformat() if item.snapshot_at else None,
                "warningCount": item.warning_count,
                "redactionCount": item.redaction_count,
                "error": item.error_message or None,
            }
            for item in items
        ],
        "summary": {
            "requested": len(items),
            "succeeded": len(successful),
            "failed": len(failed),
            "warnings": sum(item.warning_count for item in items) + len(failed),
            "redactions": sum(item.redaction_count for item in items),
        },
        "security": {
            "managedCredentialsIncluded": False,
            "containsPotentiallySensitiveUserContent": True,
        },
    }
    write_json_file(staging / "manifest.json", manifest)
    readme = (
        "# Gobii portable agent export\n\n"
        "Each agent has its own folder under `agents/`. Begin with that agent's README and destination adapter. "
        "Managed credentials are intentionally excluded. This archive may contain sensitive messages, files, "
        "attachments, and SQLite data; store it securely.\n"
    )
    (staging / "README.md").write_text(readme, encoding="utf-8")
    successful_folders = {
        str(item.source_agent_id): f"agents/{item.folder_name}"
        for item in successful
    }
    shared_references = []
    for result in results.values():
        for reference in result.shared_file_references:
            normalized = dict(reference)
            normalized["ownerAgents"] = [
                {
                    **owner,
                    **(
                        {"archiveAgentFolder": successful_folders[str(owner.get("agentId"))]}
                        if str(owner.get("agentId")) in successful_folders
                        else {}
                    ),
                }
                for owner in reference.get("ownerAgents", [])
            ]
            shared_references.append(normalized)
    write_json_file(staging / "shared-files/index.json", {"references": shared_references})

    checksum_lines = []
    for path in sorted(staging.rglob("*")):
        if not path.is_file() or path.name == "checksums.sha256":
            continue
        checksum_lines.append(f"{_sha256_file(path)}  {path.relative_to(staging).as_posix()}")
    (staging / "checksums.sha256").write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")


def _archive_filename(export: PortableAgentExport) -> str:
    timestamp = timezone.now().strftime("%Y%m%dT%H%M%SZ")
    if export.scope == PortableAgentExport.Scope.AGENT and export.agent:
        raw_label = os.path.basename((export.agent.name or "agent").replace("\\", "/"))
        label = (get_valid_filename(raw_label) or "agent")[:180]
        return f"{label}_portable_export_{timestamp}.zip"
    if export.scope == PortableAgentExport.Scope.ORGANIZATION and export.organization:
        raw_label = os.path.basename((export.organization.name or "organization").replace("\\", "/"))
        label = (get_valid_filename(raw_label) or "organization")[:180]
        return f"{label}_agents_portable_export_{timestamp}.zip"
    return f"personal_agents_portable_export_{timestamp}.zip"


def _zip_staging_directory(staging: Path, archive_path: Path) -> None:
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
        for path in sorted(staging.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(staging).as_posix())


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _download_url(export: PortableAgentExport) -> str:
    path = reverse("console_portable_agent_export_download", args=[export.id])
    token = build_download_token(export)
    return f"{settings.PUBLIC_SITE_URL.rstrip('/')}{path}?token={token}"


def _notification_copy(export: PortableAgentExport) -> tuple[str, str, str]:
    link = _download_url(export)
    subject = "Your agent export is ready"
    if export.scope == PortableAgentExport.Scope.AGENT and export.agent:
        subject = f"Your export of {export.agent.name} is ready"
    text = (
        f"Your Gobii agent migration export is ready.\n\nDownload it here: {link}\n\n"
        f"The link expires {export.expires_at.isoformat() if export.expires_at else 'in 7 days'}. "
        "Managed credentials are not included, so reconnect integrations after importing."
    )
    html_body = (
        "<p>Your Gobii agent migration export is ready.</p>"
        f'<p><a href="{html.escape(link, quote=True)}">Download the ZIP file</a></p>'
        f"<p>The link expires {html.escape(export.expires_at.isoformat() if export.expires_at else 'in 7 days')}. "
        "Managed credentials are not included, so reconnect integrations after importing.</p>"
    )
    return subject, text, html_body


def _send_from_agent(export: PortableAgentExport, recipient: str) -> bool:
    agent = export.agent
    if agent is None:
        return False
    try:
        sender = resolve_agent_email_sender_endpoint(agent, to_address=recipient, log_context="portable_agent_export")
        if sender is None:
            return False
        recipient_endpoint, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
            channel=CommsChannel.EMAIL,
            address=recipient,
            defaults={"owner_agent": None},
        )
        subject, _text, html_body = _notification_copy(export)
        message = PersistentAgentMessage.objects.create(
            owner_agent=agent,
            from_endpoint=sender,
            to_endpoint=recipient_endpoint,
            is_outbound=True,
            body=html_body,
            raw_payload={"subject": subject, "kind": "portable_agent_export_ready", "export_id": str(export.id)},
        )
        deliver_agent_email(message)
        message.refresh_from_db(fields=["latest_status"])
    except (AnymailError, DatabaseError, smtplib.SMTPException, OSError, ValueError) as exc:
        logger.warning(
            "Agent delivery failed for portable export %s error=%s",
            export.id,
            type(exc).__name__,
        )
        return False
    return message.latest_status in {DeliveryStatus.SENT, DeliveryStatus.DELIVERED}


def _send_completion_email(export: PortableAgentExport) -> bool:
    recipient = str(export.requester.email or "").strip()
    try:
        verified_recipient = bool(recipient and has_verified_email_address(export.requester, recipient))
    except DatabaseError as exc:
        logger.warning(
            "Could not verify the email recipient for portable export %s error=%s",
            export.id,
            type(exc).__name__,
        )
        return False
    if not verified_recipient:
        logger.info("Portable export %s is ready without email because requester has no verified address", export.id)
        return False
    if export.scope == PortableAgentExport.Scope.AGENT and _send_from_agent(export, recipient):
        return True
    subject, text, html_body = _notification_copy(export)
    try:
        sent = send_mail(
            subject=subject,
            message=text,
            from_email=None,
            recipient_list=[recipient],
            html_message=html_body,
            fail_silently=False,
        )
    except (AnymailError, DatabaseError, smtplib.SMTPException, OSError, ValueError) as exc:
        logger.warning(
            "System delivery failed for portable export %s error=%s",
            export.id,
            type(exc).__name__,
        )
        return False
    return sent > 0


def _record_item_progress(export: PortableAgentExport, status: str) -> None:
    field = "completed_agents" if status == PortableAgentExportItem.Status.READY else "failed_agents"
    PortableAgentExport.objects.filter(pk=export.pk).update(**{field: F(field) + 1, "phase": "snapshotting"})


def _agent_remains_in_export_scope(export: PortableAgentExport, agent: PersistentAgent) -> bool:
    if not user_can_export_agent(export.requester, agent):
        return False
    if export.scope == PortableAgentExport.Scope.PERSONAL:
        return agent.user_id == export.requester_id and agent.organization_id is None
    if export.scope == PortableAgentExport.Scope.ORGANIZATION:
        return bool(export.organization_id and agent.organization_id == export.organization_id)
    return agent.id == export.agent_id


def _process_portable_agent_export(export: PortableAgentExport) -> None:
    if not user_can_access_export(export.requester, export):
        mark_portable_agent_export_failed(export, code="access_revoked", message="Access to the requested agents is no longer available.")
        return

    with transaction.atomic():
        locked = PortableAgentExport.objects.select_for_update().get(pk=export.pk)
        if locked.status != PortableAgentExport.Status.QUEUED:
            return
        locked.status = PortableAgentExport.Status.RUNNING
        locked.phase = "snapshotting"
        locked.started_at = locked.started_at or timezone.now()
        locked.save(update_fields=["status", "phase", "started_at", "updated_at"])

    export.refresh_from_db()
    items = list(export.items.select_related("agent").order_by("source_agent_name", "source_agent_id"))
    results = {}
    content_registry = {}
    with tempfile.TemporaryDirectory(prefix=f"gobii-portable-export-{export.id}-") as temp_dir:
        staging = Path(temp_dir) / "bundle"
        agents_root = staging / "agents"
        agents_root.mkdir(parents=True, exist_ok=True)
        for item in items:
            snapshot_at = timezone.now()
            item.status = PortableAgentExportItem.Status.RUNNING
            item.snapshot_at = snapshot_at
            item.save(update_fields=["status", "snapshot_at"])
            agent = PersistentAgent.objects.non_eval().alive().select_related(
                "user", "organization", "preferred_llm_tier",
            ).filter(pk=item.source_agent_id).first()
            if agent is None or not _agent_remains_in_export_scope(export, agent):
                item.status = PortableAgentExportItem.Status.FAILED
                item.error_code = "access_revoked"
                item.error_message = "This agent is no longer available to export."
                item.save(update_fields=["status", "error_code", "error_message"])
                _record_item_progress(export, item.status)
                continue
            destination = agents_root / item.folder_name
            try:
                result = PortableAgentArchiveBuilder(
                    agent,
                    item,
                    destination,
                    content_registry=content_registry,
                ).build()
            except ARCHIVE_BUILD_ERRORS as exc:
                logger.warning(
                    "Portable export item failed export=%s agent=%s error=%s",
                    export.id,
                    item.source_agent_id,
                    type(exc).__name__,
                )
                shutil.rmtree(destination, ignore_errors=True)
                failed_prefix = f"agents/{item.folder_name}/"
                for digest, path in tuple(content_registry.items()):
                    if path.startswith(failed_prefix):
                        content_registry.pop(digest)
                item.status = PortableAgentExportItem.Status.FAILED
                item.error_code = type(exc).__name__.lower()[:64]
                item.error_message = "This agent could not be exported."
                item.save(update_fields=["status", "error_code", "error_message"])
                _record_item_progress(export, item.status)
                continue
            results[str(item.id)] = result
            item.status = PortableAgentExportItem.Status.READY
            item.message_count = result.message_count
            item.step_count = result.step_count
            item.file_count = result.file_count
            item.warning_count = len(result.warnings)
            item.redaction_count = int(result.redaction_report.get("total", 0))
            item.save(update_fields=[
                "status", "message_count", "step_count", "file_count", "warning_count", "redaction_count",
            ])
            _record_item_progress(export, item.status)

        items = list(export.items.order_by("source_agent_name", "source_agent_id"))
        successful = [item for item in items if item.status == PortableAgentExportItem.Status.READY]
        if not successful:
            mark_portable_agent_export_failed(export, code="no_agents_exported")
            return
        export.phase = "packaging"
        export.save(update_fields=["phase", "updated_at"])
        _write_root_files(staging, export, items, results)
        archive_path = Path(temp_dir) / "portable-agent-export.zip"
        _zip_staging_directory(staging, archive_path)
        archive_size = archive_path.stat().st_size
        if archive_size > settings.PORTABLE_AGENT_EXPORT_MAX_ARCHIVE_BYTES:
            mark_portable_agent_export_failed(export, code="archive_too_large", message="The export is too large to package safely.")
            return
        archive_sha256 = _sha256_file(archive_path)

        export.refresh_from_db()
        if not user_can_access_export(export.requester, export):
            mark_portable_agent_export_failed(export, code="access_revoked", message="Access to the requested agents is no longer available.")
            return
        export.phase = "uploading"
        export.save(update_fields=["phase", "updated_at"])
        storage_key = f"portable_agent_exports/{timezone.now():%Y/%m}/{export.id}.zip"
        storage = portable_agent_export_storage()
        with archive_path.open("rb") as archive:
            saved_key = storage.save(storage_key, File(archive))

        export.refresh_from_db()
        if not user_can_access_export(export.requester, export):
            export.storage_key = saved_key
            revoke_export_artifact(export)
            return
        warning_count = sum(item.warning_count for item in items) + len(items) - len(successful)
        now = timezone.now()
        export.status = (
            PortableAgentExport.Status.READY_WITH_WARNINGS if warning_count
            else PortableAgentExport.Status.READY
        )
        export.phase = "ready"
        export.completed_agents = len(successful)
        export.failed_agents = len(items) - len(successful)
        export.warning_count = warning_count
        export.redaction_count = sum(item.redaction_count for item in items)
        export.storage_key = saved_key
        export.archive_filename = _archive_filename(export)
        export.archive_size_bytes = archive_size
        export.archive_sha256 = archive_sha256
        export.completed_at = now
        export.expires_at = now + timedelta(days=settings.PORTABLE_AGENT_EXPORT_ARTIFACT_TTL_DAYS)
        export.error_code = ""
        export.error_message = ""
        try:
            export.save(update_fields=[
                "status", "phase", "completed_agents", "failed_agents", "warning_count", "redaction_count", "storage_key",
                "archive_filename", "archive_size_bytes", "archive_sha256", "completed_at", "expires_at",
                "error_code", "error_message", "updated_at",
            ])
        except DatabaseError:
            if not delete_portable_agent_export_artifact(saved_key, export_id=export.id):
                PortableAgentExport.objects.filter(pk=export.pk).update(storage_key=saved_key)
            raise

    export.refresh_from_db()
    if not user_can_access_export(export.requester, export):
        revoke_export_artifact(export)
        return
    email_sent = _send_completion_email(export)
    if email_sent:
        export.email_sent_at = timezone.now()
        export.save(update_fields=["email_sent_at", "updated_at"])
    Analytics.track(
        user_id=export.requester_id,
        event=AnalyticsEvent.AGENT_PORTABLE_EXPORT_COMPLETED,
        properties={
            "export_id": str(export.id), "scope": export.scope, "status": export.status,
            "agents": export.completed_agents, "failed_agents": export.failed_agents,
            "warning_count": export.warning_count, "archive_size_bytes": export.archive_size_bytes,
            "redaction_count": export.redaction_count,
            "email_sent": email_sent,
            "duration_seconds": _duration_seconds(export, finished_at=export.completed_at),
        },
        user=export.requester,
    )
    logger.info(
        "Portable export completed export=%s scope=%s status=%s agents=%s failed=%s warnings=%s bytes=%s",
        export.id, export.scope, export.status, export.completed_agents, export.failed_agents,
        export.warning_count, export.archive_size_bytes,
    )


@shared_task(name="api.tasks.portable_agent_exports.process_portable_agent_export")
def process_portable_agent_export(export_id: str) -> None:
    export = PortableAgentExport.objects.select_related("requester", "agent", "organization").filter(pk=export_id).first()
    if export is None or export.status not in PortableAgentExport.ACTIVE_STATUSES:
        return
    try:
        _process_portable_agent_export(export)
    except ARCHIVE_BUILD_ERRORS + (zipfile.BadZipFile,) as exc:
        logger.warning("Portable export job failed export=%s error=%s", export_id, type(exc).__name__)
        export.refresh_from_db()
        mark_portable_agent_export_failed(export, code=type(exc).__name__.lower())


@shared_task(name="api.tasks.portable_agent_exports.prune_portable_agent_exports")
def prune_portable_agent_exports() -> dict:
    now = timezone.now()
    expired_count = 0
    deleted_count = 0
    for export in PortableAgentExport.objects.filter(
        status__in=PortableAgentExport.READY_STATUSES,
        expires_at__lte=now,
    ).iterator(chunk_size=100):
        archive_size_bytes = export.archive_size_bytes
        if not expire_export_artifact(export):
            continue
        expired_count += 1
        Analytics.track(
            user_id=export.requester_id,
            event=AnalyticsEvent.AGENT_PORTABLE_EXPORT_EXPIRED,
            properties={
                "export_id": str(export.id),
                "scope": export.scope,
                "agent_count": export.completed_agents,
                "warning_count": export.warning_count,
                "redaction_count": export.redaction_count,
                "archive_size_bytes": archive_size_bytes,
            },
            user=export.requester,
        )

    for export in PortableAgentExport.objects.filter(
        status=PortableAgentExport.Status.FAILED,
    ).exclude(storage_key="").iterator(chunk_size=100):
        if delete_portable_agent_export_artifact(export.storage_key, export_id=export.id):
            export.storage_key = ""
            export.archive_size_bytes = None
            export.archive_sha256 = ""
            export.save(update_fields=["storage_key", "archive_size_bytes", "archive_sha256", "updated_at"])

    metadata_cutoff = now - timedelta(days=settings.PORTABLE_AGENT_EXPORT_METADATA_TTL_DAYS)
    stale = PortableAgentExport.objects.exclude(status__in=PortableAgentExport.ACTIVE_STATUSES).filter(
        created_at__lt=metadata_cutoff,
        storage_key="",
    )
    deleted_count = stale.count()
    stale.delete()
    logger.info("Portable export cleanup expired=%s deleted_metadata=%s", expired_count, deleted_count)
    return {"expired": expired_count, "deletedMetadata": deleted_count}
