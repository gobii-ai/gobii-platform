from dataclasses import dataclass
from typing import Iterable, List
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.sites.models import Site
from django.urls import reverse

from api.models import AgentFileSpaceAccess, AgentFsNode, PersistentAgentMessageAttachment
from .filespace_service import get_or_create_default_filespace


class AttachmentResolutionError(Exception):
    pass


@dataclass(frozen=True)
class ResolvedAttachment:
    node: AgentFsNode
    path: str
    filename: str
    content_type: str
    size_bytes: int


def normalize_attachment_paths(raw_paths: object) -> List[str]:
    if raw_paths is None:
        return []
    if isinstance(raw_paths, str):
        paths = [raw_paths]
    elif isinstance(raw_paths, (list, tuple)):
        paths = list(raw_paths)
    else:
        raise AttachmentResolutionError("Attachments must be a list of filespace paths.")

    normalized: List[str] = []
    seen: set[str] = set()
    for item in paths:
        if not isinstance(item, str):
            raise AttachmentResolutionError("Attachment paths must be strings.")
        value = item.strip()
        if not value:
            raise AttachmentResolutionError("Attachment path cannot be empty.")
        if not value.startswith("/"):
            value = f"/{value}"
        if value not in seen:
            normalized.append(value)
            seen.add(value)
    return normalized


def resolve_filespace_attachments(agent, raw_paths: object) -> List[ResolvedAttachment]:
    paths = normalize_attachment_paths(raw_paths)
    if not paths:
        return []

    filespace = get_or_create_default_filespace(agent)
    if not AgentFileSpaceAccess.objects.filter(agent=agent, filespace=filespace).exists():
        raise AttachmentResolutionError("Agent lacks access to the default filespace.")

    nodes = (
        AgentFsNode.objects
        .filter(
            filespace=filespace,
            path__in=paths,
            node_type=AgentFsNode.NodeType.FILE,
            is_deleted=False,
        )
    )
    nodes_by_path = {node.path: node for node in nodes}
    missing = [path for path in paths if path not in nodes_by_path]
    if missing:
        raise AttachmentResolutionError(f"Attachment not found in default filespace: {missing[0]}")

    max_bytes = getattr(settings, "MAX_FILE_SIZE", None)
    resolved: List[ResolvedAttachment] = []
    for path in paths:
        node = nodes_by_path[path]
        file_field = getattr(node, "content", None)
        if not file_field or not getattr(file_field, "name", None):
            raise AttachmentResolutionError(f"Attachment has no stored content: {path}")

        size_bytes = node.size_bytes
        if size_bytes is None and hasattr(file_field, "size"):
            try:
                size_bytes = int(file_field.size)
            except Exception:
                size_bytes = None
        if max_bytes and size_bytes and int(size_bytes) > int(max_bytes):
            raise AttachmentResolutionError(
                f"Attachment exceeds max size of {max_bytes} bytes: {path}"
            )

        filename = node.name or "attachment"
        content_type = node.mime_type or "application/octet-stream"
        resolved.append(
            ResolvedAttachment(
                node=node,
                path=node.path,
                filename=filename,
                content_type=content_type,
                size_bytes=int(size_bytes or 0),
            )
        )
    return resolved


def create_message_attachments(message, attachments: Iterable[ResolvedAttachment]) -> None:
    for att in attachments:
        try:
            size_bytes = int(att.size_bytes or 0)
        except Exception:
            size_bytes = 0
        PersistentAgentMessageAttachment.objects.create(
            message=message,
            file="",
            content_type=att.content_type,
            file_size=size_bytes,
            filename=att.filename,
            filespace_node=att.node,
        )


def build_filespace_download_url(agent_id, node_id) -> str:
    current_site = Site.objects.get_current()
    base = f"https://{current_site.domain}"
    path = reverse("console_agent_fs_download", kwargs={"agent_id": agent_id})
    query = urlencode({"node_id": node_id})
    return f"{base}{path}?{query}"
