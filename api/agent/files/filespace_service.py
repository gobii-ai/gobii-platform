from __future__ import annotations

from celery.utils.log import get_task_logger

"""Service helpers for importing message attachments into an agent filespace."""

from typing import List
from dataclasses import dataclass
from django.db import transaction

from ...models import (
    PersistentAgentMessage,
    AgentFileSpace,
    AgentFileSpaceAccess,
    AgentFsNode,
)

logger = get_task_logger(__name__)


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

    return created


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
