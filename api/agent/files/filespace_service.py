from __future__ import annotations

import hashlib
import os
import posixpath
from dataclasses import dataclass
from typing import Any, List

from celery.utils.log import get_task_logger
from django.core.exceptions import SuspiciousFileOperation
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.utils.text import get_valid_filename

from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource

from api.services.system_settings import get_max_file_size
from ...models import (
    PersistentAgentMessage,
    AgentFileSpace,
    AgentFileSpaceAccess,
    AgentFsNode,
)

logger = get_task_logger(__name__)
EXPORTS_DIR_NAME = "exports"
DOWNLOADS_DIR_NAME = "downloads"


@dataclass
class ImportedNodeInfo:
    node_id: str
    path: str
    filename: str


def get_or_create_default_filespace(agent) -> AgentFileSpace:
    access = (
        AgentFileSpaceAccess.objects.select_related("filespace")
        .filter(agent=agent)
        .order_by("-is_default", "-granted_at")
        .first()
    )
    if access:
        return access.filespace

    # Fallback: create a default filespace if none exists (older agents)
    fs = AgentFileSpace.objects.create(name=f"{agent.name} Files", owner_user=agent.user)
    AgentFileSpaceAccess.objects.create(
        filespace=fs,
        agent=agent,
        role=AgentFileSpaceAccess.Role.OWNER,
        is_default=True,
    )
    return fs


def get_or_create_dir(fs: AgentFileSpace, parent: AgentFsNode | None, name: str) -> AgentFsNode:
    node = (
        AgentFsNode.objects
        .filter(filespace=fs, parent=parent, name=name, node_type=AgentFsNode.NodeType.DIR, is_deleted=False)
        .first()
    )
    if node:
        return node
    node = AgentFsNode(
        filespace=fs,
        parent=parent,
        node_type=AgentFsNode.NodeType.DIR,
        name=name,
    )
    node.save()
    return node


def dedupe_name(fs: AgentFileSpace, parent: AgentFsNode | None, base_name: str) -> str:
    """Ensure unique filename within the parent by appending a suffix when needed."""
    if not AgentFsNode.objects.filter(
        filespace=fs, parent=parent, name=base_name, is_deleted=False
    ).exists():
        return base_name

    # Split extension
    if "." in base_name:
        stem, ext = base_name.rsplit(".", 1)
        ext = "." + ext
    else:
        stem, ext = base_name, ""

    # Fetch all existing names matching the pattern
    conflicting_names = set(AgentFsNode.objects.filter(
        filespace=fs, parent=parent, name__startswith=stem, name__endswith=ext, is_deleted=False
    ).values_list('name', flat=True))

    # Find the first available number in memory
    i = 2
    while True:
        candidate = f"{stem} ({i}){ext}"
        if candidate not in conflicting_names:
            return candidate
        i += 1


def _normalize_filename(raw_name: str | None, fallback_name: str, extension: str) -> str:
    name = (raw_name or "").strip()
    if not name:
        name = fallback_name
    name = get_valid_filename(os.path.basename(name)) or fallback_name
    if not name.lower().endswith(extension):
        name = f"{name}{extension}"
    return name


def _normalize_write_path(
    raw_path: str,
    extension: str,
) -> tuple[str | None, list[str], str, str] | None:
    path = raw_path.strip()
    if not path:
        return None
    basename = posixpath.basename(path)
    if basename in ("", ".", ".."):
        return None
    if not path.startswith("/"):
        path = f"/{path}"
    normalized = posixpath.normpath(path)
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    parts = [part for part in normalized.split("/") if part]
    if not parts:
        return None
    dir_parts: list[str] = []
    for part in parts[:-1]:
        safe_part = get_valid_filename(part)
        if not safe_part:
            return None
        dir_parts.append(safe_part)
    filename = parts[-1]
    if not filename:
        return None
    safe_filename = _normalize_filename(filename, filename, extension)
    if not safe_filename:
        return None
    full_path = "/" + "/".join(dir_parts + [safe_filename])
    root_dir = dir_parts[0] if dir_parts else None
    sub_dirs = dir_parts[1:] if dir_parts else []
    return root_dir, sub_dirs, safe_filename, full_path


def _ensure_nested_dirs(
    filespace: AgentFileSpace,
    root_dir: AgentFsNode,
    dir_parts: list[str],
) -> AgentFsNode | None:
    current = root_dir
    for part in dir_parts:
        existing = AgentFsNode.objects.filter(
            filespace=filespace,
            parent=current,
            name=part,
            is_deleted=False,
        ).first()
        if existing:
            if existing.node_type != AgentFsNode.NodeType.DIR:
                return None
            current = existing
            continue
        current = get_or_create_dir(filespace, current, part)
    return current


def _save_node_content(
    node: AgentFsNode,
    content_bytes: bytes,
    dir_name: str,
    agent_id: str,
    *,
    delete_node_on_failure: bool,
) -> dict[str, Any] | None:
    try:
        node.content.save(node.name, ContentFile(content_bytes), save=False)
        node.save()
        node.refresh_from_db()
        return None
    except Exception:
        logger.exception("Failed to persist file to %s for agent %s", dir_name, agent_id)
        try:
            if node.content and getattr(node.content, "name", None):
                node.content.delete(save=False)
        except Exception:
            logger.exception("Failed to clean up file content for node %s", node.id)
        if delete_node_on_failure:
            node.delete()
        return {"status": "error", "message": "Failed to save the file in the filespace."}


def _agent_has_access(agent: "PersistentAgent", filespace_id: "uuid.UUID") -> bool:
    return AgentFileSpaceAccess.objects.filter(agent=agent, filespace_id=filespace_id).exists()


def write_bytes_to_dir(
    agent: "PersistentAgent",
    content_bytes: bytes,
    path: str,
    mime_type: str,
    extension: str = "",
    overwrite: bool = False,
) -> dict[str, Any]:
    if not isinstance(content_bytes, (bytes, bytearray)):
        return {"status": "error", "message": "File content must be bytes."}

    content_bytes = bytes(content_bytes)
    max_size = get_max_file_size()
    if max_size and len(content_bytes) > max_size:
        return {
            "status": "error",
            "message": f"File exceeds maximum allowed size ({len(content_bytes)} bytes > {max_size} bytes).",
        }

    try:
        filespace = get_or_create_default_filespace(agent)
    except Exception as exc:
        logger.error("Failed to resolve default filespace for agent %s: %s", agent.id, exc)
        return {"status": "error", "message": "No filespace configured for this agent."}

    if not _agent_has_access(agent, filespace.id):
        return {"status": "error", "message": "Agent lacks access to the filespace."}

    if not isinstance(path, str):
        return {"status": "error", "message": "path must be a string."}

    extension = (extension or "").strip()
    if extension and not extension.startswith("."):
        extension = f".{extension}"

    try:
        normalized_path = _normalize_write_path(path, extension)
    except SuspiciousFileOperation:
        return {
            "status": "error",
            "message": "Invalid file path. The path may contain unsafe components or invalid characters.",
        }
    if not normalized_path:
        return {"status": "error", "message": "path must include a filename."}

    root_dir_name, sub_dirs, safe_filename, full_path = normalized_path
    if root_dir_name:
        try:
            target_dir = get_or_create_dir(filespace, None, root_dir_name)
        except Exception as exc:
            logger.exception("Failed to resolve %s directory for agent %s: %s", root_dir_name, agent.id, exc)
            return {"status": "error", "message": f"Failed to access the {root_dir_name} directory."}

        target_dir = _ensure_nested_dirs(filespace, target_dir, sub_dirs)
        if target_dir is None:
            return {"status": "error", "message": "Path conflicts with an existing file."}
    else:
        target_dir = None

    checksum = hashlib.sha256(content_bytes).hexdigest()

    existing = AgentFsNode.objects.filter(
        filespace=filespace,
        path=full_path,
        is_deleted=False,
    ).first()
    if existing:
        if existing.node_type != AgentFsNode.NodeType.FILE:
            return {"status": "error", "message": "Path points to a directory, not a file."}
        if overwrite:
            old_content_name = getattr(existing.content, "name", None)
            existing.mime_type = mime_type
            existing.checksum_sha256 = checksum
            existing.size_bytes = len(content_bytes)
            error = _save_node_content(
                existing,
                content_bytes,
                root_dir_name or "filespace root",
                agent.id,
                delete_node_on_failure=False,
            )
            if error:
                return error
            if old_content_name and old_content_name != existing.content.name:
                try:
                    existing.content.storage.delete(old_content_name)
                except Exception:
                    logger.exception("Failed to delete prior content for node %s", existing.id)
            node = existing
            return {
                "status": "ok",
                "path": node.path,
                "node_id": str(node.id),
                "filename": node.name,
            }

    node = None
    max_attempts = 5
    for attempt in range(max_attempts):
        name = dedupe_name(filespace, target_dir, safe_filename)
        node = AgentFsNode(
            filespace=filespace,
            parent=target_dir,
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
            if attempt == max_attempts - 1:
                return {"status": "error", "message": "Failed to allocate the requested file path."}
    error = _save_node_content(
        node,
        content_bytes,
        root_dir_name or "filespace root",
        agent.id,
        delete_node_on_failure=True,
    )
    if error:
        return error

    result = {
        "status": "ok",
        "path": node.path,
        "node_id": str(node.id),
        "filename": node.name,
    }
    try:
        parent_path = node.parent.path if node.parent else "/"
        props = Analytics.with_org_properties(
            {
                "agent_id": str(agent.id),
                "filespace_id": str(filespace.id),
                "node_id": str(node.id),
                "parent_path": parent_path,
                "path": node.path,
                "size_bytes": len(content_bytes),
                "mime_type": mime_type,
                "extension": (node.name.rsplit(".", 1)[-1].lower() if "." in node.name else None),
            },
            organization=getattr(agent, "organization", None),
        )
        Analytics.track_event(
            user_id=str(agent.user_id),
            event=AnalyticsEvent.AGENT_FILE_EXPORTED,
            source=AnalyticsSource.AGENT,
            properties=props.copy(),
        )
    except Exception:
        logger.debug("Failed to emit file exported analytics for agent %s", getattr(agent, "id", None), exc_info=True)
    return result


def import_message_attachments_to_filespace(message_id: str) -> List[ImportedNodeInfo]:
    """
    Copy PersistentAgentMessageAttachment files into the owning agent's default filespace.

    Returns a list of ImportedNodeInfo for created nodes. No-op if message is
    outbound, has no owner agent, or has no attachments.
    """
    # Avoid holding a transaction across storage I/O
    with transaction.atomic():
        message = (
            PersistentAgentMessage.objects
            .select_related("owner_agent")
            .prefetch_related("attachments")
            .get(id=message_id)
        )
        agent = message.owner_agent
        if message.is_outbound or agent is None:
            return []

        attachments = list(message.attachments.all())

    if not attachments:
        return []

    fs = get_or_create_default_filespace(agent)

    # Create Inbox/YYYY-MM-DD structure
    inbox = get_or_create_dir(fs, None, "Inbox")
    date_dir = get_or_create_dir(fs, inbox, message.timestamp.date().isoformat())

    created: List[ImportedNodeInfo] = []
    for att in attachments:
        try:
            base_name = att.filename or "attachment"
            name = dedupe_name(fs, date_dir, base_name)
            node = AgentFsNode(
                filespace=fs,
                parent=date_dir,
                node_type=AgentFsNode.NodeType.FILE,
                name=name,
                created_by_agent=agent,
                mime_type=att.content_type or "",
            )
            node.save()  # Ensure PK exists for upload_to path
            # Save file content (storage handles copying)
            if not att.file or not getattr(att.file, "name", None):
                raise ValueError("Attachment has no stored file content.")
            with att.file.storage.open(att.file.name, "rb") as stored_file:
                node.content.save(att.filename or name, stored_file, save=True)
            node.refresh_from_db()
            # Link the original attachment to this filespace node and clean up original
            try:
                att.filespace_node = node
                att.save(update_fields=["filespace_node"])
                if att.file and getattr(att.file, "name", None):
                    # Remove the stored blob from the original attachment
                    att.file.delete(save=False)
                    # Clear the DB field to avoid a dangling filename reference
                    type(att).objects.filter(id=att.id).update(file="")
            except Exception:
                logger.exception(
                    f"Failed to link new filespace node or delete source file for attachment {att.id} (message {message_id})"
                )

            created.append(ImportedNodeInfo(node_id=str(node.id), path=node.path, filename=name))
        except Exception:
            # Skip failed items but continue others
            logger.exception("Failed to import attachment %s for message %s", att.filename, message_id)
            continue

    # Record provenance back onto the message (best-effort)
    if created:
        try:
            message = PersistentAgentMessage.objects.only("id", "raw_payload").get(id=message_id)
            payload = dict(message.raw_payload or {})
            nodes = payload.get("filespace_nodes") or []
            nodes += [{"id": n.node_id, "path": n.path, "filename": n.filename} for n in created]
            payload["filespace_nodes"] = nodes
            message.raw_payload = payload
            message.save(update_fields=["raw_payload"])
        except Exception:
            logger.exception("Failed to record provenance for message %s", message_id)
            pass
        broadcast_message_attachment_update(message_id)

        try:
            total_bytes = 0
            for item in created:
                try:
                    node_obj = AgentFsNode.objects.filter(id=item.node_id).only("size_bytes", "filespace_id").first()
                    if node_obj and node_obj.size_bytes:
                        total_bytes += int(node_obj.size_bytes)
                except Exception:
                    continue
            props = {
                "message_id": message_id,
                "attachment_count": len(created),
                "total_bytes": total_bytes or None,
            }
            # Use the owner agent from earlier in the function
            props = Analytics.with_org_properties(props, organization=getattr(agent, "organization", None))
            Analytics.track_event(
                user_id=str(getattr(agent, "user_id", "")),
                event=AnalyticsEvent.AGENT_ATTACHMENT_IMPORTED,
                source=AnalyticsSource.AGENT,
                properties=props.copy(),
            )
        except Exception:
            logger.debug("Failed to emit attachment import analytics for message %s", message_id, exc_info=True)

    return created


def broadcast_message_attachment_update(message_id: str) -> None:
    try:
        message = (
            PersistentAgentMessage.objects
            .select_related("from_endpoint", "to_endpoint", "conversation__peer_link", "peer_agent", "owner_agent")
            .prefetch_related("attachments__filespace_node")
            .get(id=message_id)
        )
    except PersistentAgentMessage.DoesNotExist:
        return
    except Exception:
        logger.exception("Failed to load message %s for attachment broadcast", message_id)
        return

    agent_id = message.owner_agent_id
    if not agent_id:
        return

    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer
        from console.agent_chat.timeline import is_chat_hidden_message, serialize_message_event
        from console.agent_audit.serializers import serialize_message as serialize_audit_message
        from console.agent_audit.realtime import send_audit_event
    except Exception:
        logger.exception("Failed to import realtime modules for message %s", message_id)
        return

    try:
        if not is_chat_hidden_message(message):
            payload = serialize_message_event(message)
            channel_layer = get_channel_layer()
            if channel_layer is not None:
                async_to_sync(channel_layer.group_send)(
                    f"agent-chat-{agent_id}",
                    {"type": "timeline_event", "payload": payload},
                )
    except Exception:
        logger.exception("Failed to broadcast chat attachment update for message %s", message_id)

    try:
        audit_payload = serialize_audit_message(message)
        send_audit_event(str(agent_id), audit_payload)
    except Exception:
        logger.exception("Failed to broadcast audit attachment update for message %s", message_id)


def enqueue_import_after_commit(message_id: str) -> None:
    """Schedule an attachments -> filespace import after the surrounding transaction commits."""

    def _schedule():
        try:
            from api.agent.tasks.filespace_imports import (
                import_message_attachments_to_filespace_task,
            )
            import_message_attachments_to_filespace_task.delay(str(message_id))
        except Exception:
            # Best-effort scheduling; ignore failures here
            logger.exception("Failed to enqueue filespace import for message %s", message_id)
            pass

    transaction.on_commit(_schedule)
