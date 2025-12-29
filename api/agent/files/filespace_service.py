from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import List

from celery.utils.log import get_task_logger
from django.conf import settings
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.utils.text import get_valid_filename

from ...models import (
    PersistentAgentMessage,
    AgentFileSpace,
    AgentFileSpaceAccess,
    AgentFsNode,
)

logger = get_task_logger(__name__)
EXPORTS_DIR_NAME = "exports"


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


def _agent_has_access(agent, filespace_id) -> bool:
    return AgentFileSpaceAccess.objects.filter(agent=agent, filespace_id=filespace_id).exists()


def write_bytes_to_exports(
    agent,
    content_bytes: bytes,
    filename: str | None,
    fallback_name: str,
    extension: str,
    mime_type: str,
):
    if not isinstance(content_bytes, (bytes, bytearray)):
        return {"status": "error", "message": "File content must be bytes."}

    content_bytes = bytes(content_bytes)
    max_size = getattr(settings, "MAX_FILE_SIZE", None)
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

    try:
        exports_dir = get_or_create_dir(filespace, None, EXPORTS_DIR_NAME)
    except Exception as exc:
        logger.exception("Failed to resolve exports directory for agent %s: %s", agent.id, exc)
        return {"status": "error", "message": "Failed to access the exports directory."}

    base_name = _normalize_filename(filename, fallback_name, extension)
    checksum = hashlib.sha256(content_bytes).hexdigest()
    node = None
    max_attempts = 5
    for attempt in range(max_attempts):
        name = dedupe_name(filespace, exports_dir, base_name)
        node = AgentFsNode(
            filespace=filespace,
            parent=exports_dir,
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
            logger.warning(
                "Filename collision for agent %s on %s (attempt %s)",
                agent.id,
                name,
                attempt + 1,
            )
            if attempt == max_attempts - 1:
                return {
                    "status": "error",
                    "message": "Failed to allocate a unique filename for this export.",
                }

    try:
        node.content.save(name, ContentFile(content_bytes), save=True)
        node.refresh_from_db()
    except Exception:
        logger.exception("Failed to persist file to exports for agent %s", agent.id)
        try:
            if node.content and getattr(node.content, "name", None):
                node.content.delete(save=False)
        except Exception:
            logger.exception("Failed to clean up file content for node %s", node.id)
        node.delete()
        return {"status": "error", "message": "Failed to save the file in the filespace."}

    return {
        "status": "ok",
        "path": node.path,
        "node_id": str(node.id),
        "filename": node.name,
    }


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
        from console.agent_chat.timeline import serialize_message_event
        from console.agent_audit.serializers import serialize_message as serialize_audit_message
        from console.agent_audit.realtime import send_audit_event
    except Exception:
        logger.exception("Failed to import realtime modules for message %s", message_id)
        return

    try:
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
