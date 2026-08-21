import hashlib
import logging
import os
import tempfile
import zipfile
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db import DatabaseError, transaction
from django.db.models import F, Q
from django.utils import timezone

from api.models import (
    AgentFileSpace,
    AgentFileSpaceAccess,
    AgentPeerLink,
    PortableAgentImport,
    PortableAgentImportItem,
    PortableAgentMigrationReport,
)
from api.services.portable_agent_import_archive import (
    PortableAgentImportArchive,
    PortableAgentImportArchiveError,
    validate_portable_agent_archive,
)
from api.services.portable_agent_import_restore import (
    PortableAgentRestorer,
    PortableAgentRestoreError,
    RESTORE_ERRORS,
)
from api.services.portable_agent_imports import (
    STORAGE_ERRORS,
    delete_failed_import_shells,
    delete_portable_agent_import_artifact,
    expire_portable_agent_import,
    portable_agent_import_storage,
    retry_portable_agent_import_artifact_cleanups,
    user_can_import_to_target,
)


logger = logging.getLogger(__name__)
SAFE_FAILURE_MESSAGE = "The agent import could not be completed. Please review the archive and try again."
IMPORT_TASK_ERRORS = (
    AttributeError,
    DatabaseError,
    KeyError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
    zipfile.BadZipFile,
    *STORAGE_ERRORS,
    *RESTORE_ERRORS,
)


def _copy_archive_to_temp(job: PortableAgentImport, temp_dir: str) -> str:
    destination = os.path.join(temp_dir, "portable-agent-import.zip")
    digest = hashlib.sha256()
    storage = portable_agent_import_storage()
    with storage.open(job.storage_key, "rb") as source, open(destination, "wb") as output:
        while chunk := source.read(1024 * 1024):
            output.write(chunk)
            digest.update(chunk)
    if job.archive_sha256 and digest.hexdigest() != job.archive_sha256:
        raise PortableAgentImportArchiveError(
            "The stored upload no longer matches the received archive.",
            code="storage_checksum_mismatch",
        )
    return destination


def mark_portable_agent_import_failed(
    job: PortableAgentImport,
    *,
    code: str,
    message: str | None = None,
    fail_selected_items: bool = False,
) -> None:
    if fail_selected_items:
        job.items.filter(
            status__in=[PortableAgentImportItem.Status.SELECTED, PortableAgentImportItem.Status.PROVISIONING],
        ).update(
            status=PortableAgentImportItem.Status.FAILED,
            error_code=code[:64],
            error_message=(message or SAFE_FAILURE_MESSAGE)[:512],
        )
    job.status = PortableAgentImport.Status.FAILED
    job.processing_task_id = ""
    job.error_code = code[:64]
    job.error_message = (message or SAFE_FAILURE_MESSAGE)[:512]
    job.failed_agents = job.items.filter(status=PortableAgentImportItem.Status.FAILED).count()
    job.completed_at = timezone.now()
    job.expires_at = None
    storage_key = job.storage_key
    if storage_key and delete_portable_agent_import_artifact(storage_key, import_id=job.id):
        job.storage_key = ""
    job.save(update_fields=[
        "status", "processing_task_id", "error_code", "error_message", "failed_agents",
        "completed_at", "expires_at", "storage_key", "updated_at",
    ])
    if fail_selected_items:
        delete_failed_import_shells(job)


def _validate_import(job: PortableAgentImport) -> None:
    with tempfile.TemporaryDirectory(prefix=f"gobii-portable-import-validate-{job.id}-") as temp_dir:
        archive_path = _copy_archive_to_temp(job, temp_dir)
        validation = validate_portable_agent_archive(archive_path)
    with transaction.atomic():
        locked = PortableAgentImport.objects.select_for_update().get(pk=job.pk)
        if locked.status != PortableAgentImport.Status.VALIDATING:
            return
        PortableAgentImportItem.objects.bulk_create([
            PortableAgentImportItem(
                import_job=locked,
                source_agent_id=candidate.source_agent_id,
                source_agent_name=candidate.source_agent_name,
                folder_name=candidate.folder_name,
                snapshot_at=candidate.snapshot_at,
                status=(
                    PortableAgentImportItem.Status.AVAILABLE
                    if candidate.selectable
                    else PortableAgentImportItem.Status.UNAVAILABLE
                ),
                message_count=candidate.message_count,
                step_count=candidate.step_count,
                file_count=candidate.file_count,
                warnings=candidate.warnings,
                compatibility=candidate.compatibility,
                error_code="" if candidate.selectable else "unavailable",
                error_message=candidate.error,
            )
            for candidate in validation.candidates
        ])
        locked.format_version = validation.format_version
        locked.status = PortableAgentImport.Status.AWAITING_SELECTION
        locked.total_agents = len(validation.candidates)
        locked.warning_count = sum(len(candidate.warnings) for candidate in validation.candidates)
        locked.save(update_fields=[
            "format_version", "status", "total_agents", "warning_count", "updated_at",
        ])


@shared_task(name="api.tasks.portable_agent_imports.validate_portable_agent_import")
def validate_portable_agent_import(import_id: str) -> None:
    job = PortableAgentImport.objects.select_related("requester", "organization").filter(pk=import_id).first()
    if job is None or job.status != PortableAgentImport.Status.VALIDATING:
        return
    try:
        _validate_import(job)
    except PortableAgentImportArchiveError as exc:
        job.refresh_from_db()
        if job.status != PortableAgentImport.Status.VALIDATING:
            return
        logger.info("Portable import validation rejected import=%s code=%s", import_id, exc.code)
        mark_portable_agent_import_failed(job, code=exc.code, message=str(exc))
    except IMPORT_TASK_ERRORS as exc:
        job.refresh_from_db()
        if job.status != PortableAgentImport.Status.VALIDATING:
            return
        logger.warning("Portable import validation failed import=%s error=%s", import_id, type(exc).__name__)
        mark_portable_agent_import_failed(job, code=type(exc).__name__.lower())


def _mark_item_failed(item: PortableAgentImportItem, exc) -> None:
    item.status = PortableAgentImportItem.Status.FAILED
    item.error_code = type(exc).__name__.lower()[:64]
    item.error_message = "This agent could not be restored. Other selected agents were not affected."
    item.save(update_fields=["status", "error_code", "error_message", "updated_at"])
    PortableAgentImport.objects.filter(pk=item.import_job_id).update(
        failed_agents=F("failed_agents") + 1,
    )


def _sync_migration_report_warnings(item: PortableAgentImportItem) -> None:
    if not item.imported_agent_id:
        return
    migration_report = PortableAgentMigrationReport.objects.filter(agent_id=item.imported_agent_id).first()
    if migration_report is None:
        return
    report = dict(migration_report.report) if isinstance(migration_report.report, dict) else {}
    report["warnings"] = list(item.warnings) if isinstance(item.warnings, list) else []
    migration_report.report = report
    migration_report.save(update_fields=["report"])


def _item_warning_count(job: PortableAgentImport) -> int:
    return sum(
        len(value) if isinstance(value, list) else 0
        for value in job.items.values_list("warnings", flat=True)
    )


def _restore_relationships(archive, job: PortableAgentImport, successful_items: list[PortableAgentImportItem]) -> int:
    by_source_id = {str(item.source_agent_id): item.imported_agent for item in successful_items if item.imported_agent_id}
    created_pairs = set()
    warning_count = 0
    for item in successful_items:
        relationships_name = f"agents/{item.folder_name}/communications/relationships.json"
        if not archive.has(relationships_name):
            continue
        relationships = archive.json(relationships_name).get("peerAgents")
        for row in relationships if isinstance(relationships, list) else []:
            if not isinstance(row, dict):
                continue
            counterpart = by_source_id.get(str(row.get("counterpartAgentId") or ""))
            if counterpart is None or item.imported_agent is None:
                warning_count += 1
                continue
            pair = tuple(sorted([str(item.imported_agent_id), str(counterpart.id)]))
            if pair in created_pairs:
                continue
            created_pairs.add(pair)
            messages_per_window = row.get("messagesPerWindow") if isinstance(row.get("messagesPerWindow"), int) else 30
            window_hours = row.get("windowHours") if isinstance(row.get("windowHours"), int) else 6
            AgentPeerLink.objects.get_or_create(
                pair_key=AgentPeerLink.build_pair_key(*pair),
                defaults={
                    "agent_a": item.imported_agent,
                    "agent_b": counterpart,
                    "created_by": job.requester,
                    "messages_per_window": min(max(messages_per_window, 1), 500),
                    "window_hours": min(max(window_hours, 1), 168),
                    "is_enabled": False,
                },
            )
    return warning_count


def _restore_filespace_sharing(successful_items: list[PortableAgentImportItem]) -> int:
    successful_by_source = {
        str(item.source_agent_id): item
        for item in successful_items
        if item.imported_agent_id
    }
    entries_by_source_space: dict[str, list[tuple[PortableAgentImportItem, dict, str]]] = {}
    for item in successful_items:
        compatibility = item.compatibility if isinstance(item.compatibility, dict) else {}
        restored = compatibility.get("restoredFilespaces") if isinstance(compatibility.get("restoredFilespaces"), dict) else {}
        accesses = compatibility.get("filespaceAccess") if isinstance(compatibility.get("filespaceAccess"), list) else []
        for access in accesses:
            if not isinstance(access, dict):
                continue
            source_id = str(access.get("sourceFilespaceId") or "")
            private_id = str(restored.get(source_id) or "")
            if source_id and private_id:
                entries_by_source_space.setdefault(source_id, []).append((item, access, private_id))

    fallbacks = 0
    for source_id, entries in entries_by_source_space.items():
        relevant_source_ids = {
            str(row.get("agentId") or "")
            for _item, access, _private_id in entries
            for row in (
                access.get("agentAccess")
                if isinstance(access.get("agentAccess"), list)
                else []
            )
            if isinstance(row, dict) and row.get("agentId")
        }
        entry_source_ids = {str(item.source_agent_id) for item, _access, _private_id in entries}
        all_relevant_imported = bool(relevant_source_ids) and relevant_source_ids <= set(successful_by_source)
        complete_metadata = relevant_source_ids <= entry_source_ids
        owner_entries = [
            entry
            for entry in entries
            if entry[1].get("role") == AgentFileSpaceAccess.Role.OWNER
        ]
        if not all_relevant_imported or not complete_metadata or not owner_entries:
            for item, access, _private_id in entries:
                warnings = list(item.warnings) if isinstance(item.warnings, list) else []
                warning = (
                    f"Shared filespace `{access.get('name') or 'Imported shared files'}` was copied "
                    "into a private imported-files area."
                )
                if warning not in warnings:
                    warnings.append(warning)
                    item.warnings = warnings
                    item.save(update_fields=["warnings", "updated_at"])
                    _sync_migration_report_warnings(item)
                    fallbacks += 1
            continue

        canonical_item, _canonical_access, destination_id = owner_entries[0]
        if canonical_item.imported_agent is None:
            continue
        for item, access, private_id in entries:
            if item.imported_agent is None:
                continue
            if private_id != destination_id:
                private_space = AgentFileSpace.objects.filter(pk=private_id).first()
                if private_space:
                    storage_names = list(private_space.nodes.exclude(content="").values_list("content", flat=True))
                    private_space.delete()
                    for storage_name in {name for name in storage_names if name}:
                        delete_portable_agent_import_artifact(
                            storage_name,
                            import_id=item.import_job_id,
                            storage_alias="default",
                        )
            role = str(access.get("role") or AgentFileSpaceAccess.Role.READER)
            if role not in {choice for choice, _label in AgentFileSpaceAccess.Role.choices}:
                role = AgentFileSpaceAccess.Role.READER
            make_default = bool(access.get("isDefault"))
            if make_default:
                item.imported_agent.filespace_access.filter(is_default=True).exclude(
                    filespace_id=destination_id,
                ).update(is_default=False)
            AgentFileSpaceAccess.objects.update_or_create(
                filespace_id=destination_id,
                agent=item.imported_agent,
                defaults={"role": role, "is_default": make_default},
            )
            compatibility = dict(item.compatibility) if isinstance(item.compatibility, dict) else {}
            restored = dict(compatibility.get("restoredFilespaces") or {})
            restored[source_id] = destination_id
            compatibility["restoredFilespaces"] = restored
            item.compatibility = compatibility
            item.save(update_fields=["compatibility", "updated_at"])
    return fallbacks


def _begin_or_resume_import(job: PortableAgentImport, task_id: str) -> bool:
    interrupted = False
    with transaction.atomic():
        locked = PortableAgentImport.objects.select_for_update().get(pk=job.pk)
        if locked.status == PortableAgentImport.Status.QUEUED:
            locked.status = PortableAgentImport.Status.RUNNING
            locked.processing_task_id = task_id
        elif locked.status == PortableAgentImport.Status.RUNNING:
            if locked.processing_task_id and locked.processing_task_id != task_id:
                return False
            locked.processing_task_id = task_id
            interrupted_items = locked.items.select_for_update().filter(
                status=PortableAgentImportItem.Status.PROVISIONING,
            )
            interrupted = interrupted_items.update(
                status=PortableAgentImportItem.Status.FAILED,
                error_code="interrupted",
                error_message="This agent restore was interrupted and its reserved shell was removed.",
            ) > 0
            if interrupted:
                locked.failed_agents = locked.items.filter(
                    status=PortableAgentImportItem.Status.FAILED,
                ).count()
        else:
            return False
        locked.save(update_fields=["status", "processing_task_id", "failed_agents", "updated_at"])
    if interrupted:
        delete_failed_import_shells(job)
    return True


def _claim_next_item(job: PortableAgentImport) -> PortableAgentImportItem | None:
    with transaction.atomic():
        item = (
            PortableAgentImportItem.objects.select_for_update(of=("self",))
            .select_related("imported_agent")
            .filter(import_job=job, status=PortableAgentImportItem.Status.SELECTED)
            .first()
        )
        if item is None:
            return None
        item.status = PortableAgentImportItem.Status.PROVISIONING
        item.save(update_fields=["status", "updated_at"])
        return item


def _process_import(job: PortableAgentImport, task_id: str) -> None:
    if not user_can_import_to_target(job.requester, job):
        mark_portable_agent_import_failed(
            job,
            code="access_revoked",
            message="You no longer have permission to import agents into this workspace.",
            fail_selected_items=True,
        )
        return
    if not _begin_or_resume_import(job, task_id):
        return

    with tempfile.TemporaryDirectory(prefix=f"gobii-portable-import-{job.id}-") as temp_dir:
        archive_path = _copy_archive_to_temp(job, temp_dir)
        validate_portable_agent_archive(archive_path)
        with PortableAgentImportArchive(archive_path) as archive:
            while item := _claim_next_item(job):
                if not user_can_import_to_target(job.requester, job):
                    mark_portable_agent_import_failed(
                        job,
                        code="access_revoked",
                        message="Workspace access changed while the import was running.",
                        fail_selected_items=True,
                    )
                    return
                try:
                    restorer = PortableAgentRestorer(archive, job, item)
                    warnings = restorer.restore()
                    with transaction.atomic():
                        restorer.activate_for_web_chat()
                        completed = PortableAgentImportItem.objects.filter(
                            pk=item.pk,
                            status=PortableAgentImportItem.Status.PROVISIONING,
                        ).update(
                            status=PortableAgentImportItem.Status.READY,
                            warnings=warnings,
                            error_code="",
                            error_message="",
                            updated_at=timezone.now(),
                        )
                        if completed != 1:
                            raise PortableAgentRestoreError("The import item changed while it was being restored.")
                except RESTORE_ERRORS as exc:
                    logger.warning(
                        "Portable agent restore failed import=%s item=%s error=%s",
                        job.id,
                        item.id,
                        type(exc).__name__,
                    )
                    _mark_item_failed(item, exc)
                    delete_failed_import_shells(job, failed_item=item)
                    continue
                PortableAgentImport.objects.filter(pk=job.pk).update(
                    completed_agents=F("completed_agents") + 1,
                )
            successful = list(
                job.items.filter(status=PortableAgentImportItem.Status.READY).select_related("imported_agent")
            )
            _restore_filespace_sharing(successful)
            relationship_warnings = _restore_relationships(archive, job, successful)

    job.refresh_from_db()
    warning_count = _item_warning_count(job) + relationship_warnings
    if relationship_warnings:
        ready_item = job.items.filter(status=PortableAgentImportItem.Status.READY).first()
        if ready_item:
            ready_item.warnings = list(ready_item.warnings) + [
                f"{relationship_warnings} peer relationship(s) were not recreated because the counterpart was not imported."
            ]
            ready_item.save(update_fields=["warnings", "updated_at"])
            _sync_migration_report_warnings(ready_item)
            warning_count = _item_warning_count(job)
    now = timezone.now()
    if job.completed_agents == 0:
        final_status = PortableAgentImport.Status.FAILED
        error_code = "no_agents_imported"
        error_message = SAFE_FAILURE_MESSAGE
    elif warning_count or job.failed_agents:
        final_status = PortableAgentImport.Status.COMPLETED_WITH_WARNINGS
        error_code = ""
        error_message = ""
    else:
        final_status = PortableAgentImport.Status.COMPLETED
        error_code = ""
        error_message = ""
    storage_key = job.storage_key
    if storage_key and delete_portable_agent_import_artifact(storage_key, import_id=job.id):
        storage_key = ""
    job.status = final_status
    job.processing_task_id = ""
    job.warning_count = warning_count
    job.error_code = error_code
    job.error_message = error_message
    job.completed_at = now
    job.storage_key = storage_key
    job.save(update_fields=[
        "status", "processing_task_id", "warning_count", "error_code", "error_message",
        "completed_at", "storage_key", "updated_at",
    ])


@shared_task(bind=True, name="api.tasks.portable_agent_imports.process_portable_agent_import")
def process_portable_agent_import(self, import_id: str) -> None:
    job = PortableAgentImport.objects.select_related("requester", "organization").filter(pk=import_id).first()
    if job is None or job.status not in {PortableAgentImport.Status.QUEUED, PortableAgentImport.Status.RUNNING}:
        return
    task_id = str(self.request.id or f"local:{import_id}")
    try:
        _process_import(job, task_id)
    except PortableAgentImportArchiveError as exc:
        logger.warning("Portable import archive changed import=%s code=%s", import_id, exc.code)
        job.refresh_from_db()
        mark_portable_agent_import_failed(
            job,
            code=exc.code,
            message=str(exc),
            fail_selected_items=True,
        )
    except IMPORT_TASK_ERRORS as exc:
        logger.warning("Portable import job failed import=%s error=%s", import_id, type(exc).__name__)
        job.refresh_from_db()
        mark_portable_agent_import_failed(
            job,
            code=type(exc).__name__.lower(),
            fail_selected_items=True,
        )


@shared_task(name="api.tasks.portable_agent_imports.prune_portable_agent_imports")
def prune_portable_agent_imports() -> dict:
    now = timezone.now()
    expired = 0
    retry_portable_agent_import_artifact_cleanups()
    for job in PortableAgentImport.objects.filter(
        status__in=[
            PortableAgentImport.Status.VALIDATING,
            PortableAgentImport.Status.AWAITING_SELECTION,
        ],
        expires_at__lte=now,
    ).iterator(chunk_size=100):
        expire_portable_agent_import(job)
        expired += 1
    for job in PortableAgentImport.objects.filter(
        Q(status=PortableAgentImport.Status.FAILED) | Q(status=PortableAgentImport.Status.EXPIRED),
    ).exclude(storage_key="").iterator(chunk_size=100):
        if delete_portable_agent_import_artifact(job.storage_key, import_id=job.id):
            job.storage_key = ""
            job.save(update_fields=["storage_key", "updated_at"])
    cutoff = now - timedelta(days=settings.PORTABLE_AGENT_IMPORT_METADATA_TTL_DAYS)
    stale = PortableAgentImport.objects.exclude(status__in=PortableAgentImport.ACTIVE_STATUSES).filter(
        created_at__lt=cutoff,
        storage_key="",
    )
    deleted = stale.count()
    stale.delete()
    return {"expired": expired, "deletedMetadata": deleted}
