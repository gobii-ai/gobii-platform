import hashlib
import logging
import mimetypes
import os
from typing import Any, Optional

from django.core.files import File as DjangoFile
from django.db import IntegrityError, transaction
from django.utils.text import get_valid_filename

from api.agent.files.filespace_service import dedupe_name, get_or_create_default_filespace, get_or_create_dir
from api.models import AgentFsNode, PersistentAgent
from api.services.system_settings import get_max_file_size

logger = logging.getLogger(__name__)


def collect_browser_history_attachment_paths(history: Any) -> list[str]:
    """Return unique local attachment paths explicitly emitted by browser-use actions."""
    action_results = getattr(history, "action_results", None)
    if not callable(action_results):
        return []

    paths: list[str] = []
    seen: set[str] = set()
    for result in action_results() or []:
        attachments = getattr(result, "attachments", None) or []
        if isinstance(attachments, str):
            attachments = [attachments]
        if not isinstance(attachments, (list, tuple)):
            continue
        for attachment in attachments:
            if not isinstance(attachment, str):
                continue
            local_path = attachment.strip()
            if not local_path or local_path in seen:
                continue
            seen.add(local_path)
            paths.append(local_path)
    return paths


def _compute_sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _delete_stale_artifact_node(node: AgentFsNode) -> None:
    try:
        node.delete()
    except Exception:
        logger.warning("Failed to delete stale browser task artifact node %s", node.id, exc_info=True)


def _create_artifact_node(
    *,
    agent: PersistentAgent,
    task_id: str,
    local_path: str,
    filename: str,
    mime_type: str,
) -> AgentFsNode | None:
    filespace = get_or_create_default_filespace(agent)
    browser_tasks_dir = get_or_create_dir(filespace, None, "browser_tasks")
    task_dir = get_or_create_dir(filespace, browser_tasks_dir, task_id)
    try:
        checksum = _compute_sha256_file(local_path)
    except OSError:
        logger.warning(
            "Skipping browser task artifact for task %s because file checksum could not be read: %s",
            task_id,
            local_path,
            exc_info=True,
        )
        return None

    for attempt in range(5):
        name = dedupe_name(filespace, task_dir, filename)
        node = AgentFsNode(
            filespace=filespace,
            parent=task_dir,
            node_type=AgentFsNode.NodeType.FILE,
            name=name,
            created_by_agent=agent,
            mime_type=mime_type,
            checksum_sha256=checksum,
        )
        try:
            with transaction.atomic():
                node.save()
            break
        except IntegrityError:
            if attempt == 4:
                logger.warning(
                    "Failed to allocate browser task artifact path for task %s from %s",
                    task_id,
                    local_path,
                )
                return None

    try:
        with open(local_path, "rb") as artifact_file:
            node.content.save(node.name, DjangoFile(artifact_file), save=True)
        node.refresh_from_db()
    except Exception:
        logger.warning(
            "Skipping browser task artifact for task %s because file could not be saved: %s",
            task_id,
            local_path,
            exc_info=True,
        )
        _delete_stale_artifact_node(node)
        return None

    return node


def persist_browser_task_artifacts_sync(
    *,
    history: Any,
    persistent_agent_id: Optional[str],
    task_id: str,
    excluded_local_paths: Optional[set[str]] = None,
) -> list[dict[str, Any]]:
    """Copy explicit browser-use attachment files from the worker filesystem to filespace."""
    if not persistent_agent_id:
        return []

    attachment_paths = collect_browser_history_attachment_paths(history)
    if not attachment_paths:
        return []

    try:
        agent = PersistentAgent.objects.get(id=persistent_agent_id)
    except PersistentAgent.DoesNotExist:
        logger.warning(
            "Cannot persist browser task artifacts for task %s: persistent agent %s not found",
            task_id,
            persistent_agent_id,
        )
        return []

    artifacts: list[dict[str, Any]] = []
    max_file_size = get_max_file_size()
    excluded_local_paths = excluded_local_paths or set()
    for local_path in attachment_paths:
        if local_path in excluded_local_paths:
            logger.info(
                "Skipping browser task artifact for task %s because download listener owns it: %s",
                task_id,
                local_path,
            )
            continue
        if not os.path.isfile(local_path):
            logger.info("Skipping browser task artifact for task %s because path is not a file: %s", task_id, local_path)
            continue

        try:
            size_bytes = os.path.getsize(local_path)
        except OSError:
            logger.warning(
                "Skipping browser task artifact for task %s because file size could not be read: %s",
                task_id,
                local_path,
                exc_info=True,
            )
            continue

        if max_file_size and size_bytes > max_file_size:
            logger.info(
                "Skipping oversized browser task artifact for task %s: %s (%s bytes > %s)",
                task_id,
                local_path,
                size_bytes,
                max_file_size,
            )
            continue

        raw_filename = os.path.basename(local_path) or "attachment"
        filename = get_valid_filename(raw_filename) or "attachment"
        mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        node = _create_artifact_node(
            agent=agent,
            task_id=task_id,
            local_path=local_path,
            filename=filename,
            mime_type=mime_type,
        )
        if not node:
            continue

        artifacts.append(
            {
                "filename": node.name,
                "path": node.path,
                "node_id": str(node.id),
                "mime_type": node.mime_type or mime_type,
                "size_bytes": node.size_bytes if node.size_bytes is not None else size_bytes,
            }
        )

    return artifacts
