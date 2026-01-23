import base64
import logging
from datetime import datetime
from typing import Any, Dict, Iterable, Optional

from django.utils import timezone
from django.db import DatabaseError

from api.agent.files.attachment_helpers import build_signed_filespace_download_url
from api.agent.files.filespace_service import get_or_create_default_filespace, write_bytes_to_dir
from api.models import AgentFsNode, PersistentAgent

logger = logging.getLogger(__name__)


def _coerce_sync_timestamp(value: Optional[datetime]) -> datetime:
    if isinstance(value, datetime):
        return value
    return timezone.now()


def _decode_change_content(change: Dict[str, Any]) -> Optional[bytes]:
    if "content_b64" in change and isinstance(change["content_b64"], str):
        try:
            return base64.b64decode(change["content_b64"], validate=True)
        except (ValueError, TypeError):
            return None
    content = change.get("content")
    if isinstance(content, bytes):
        return content
    if isinstance(content, str):
        return content.encode("utf-8")
    return None


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return False


def apply_filespace_push(
    agent: PersistentAgent,
    changes: Iterable[Dict[str, Any]],
    *,
    sync_timestamp: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Apply workspace changes into AgentFsNode with LWW resolution."""
    stamp = _coerce_sync_timestamp(sync_timestamp)
    try:
        filespace = get_or_create_default_filespace(agent)
    except (DatabaseError, ValueError) as exc:
        logger.warning("Filespace push failed to resolve filespace for %s: %s", agent.id, exc)
        return {"status": "error", "message": "Filespace unavailable."}

    created = 0
    updated = 0
    deleted = 0
    skipped = 0
    errors = 0

    for change in changes:
        if not isinstance(change, dict):
            skipped += 1
            continue
        path = change.get("path")
        if not isinstance(path, str) or not path.strip():
            skipped += 1
            continue

        existing = AgentFsNode.objects.filter(filespace=filespace, path=path).first()
        if existing and existing.updated_at and existing.updated_at >= stamp:
            skipped += 1
            continue

        if _coerce_bool(change.get("is_deleted")):
            if not existing:
                skipped += 1
                continue
            AgentFsNode.objects.filter(id=existing.id).update(
                is_deleted=True,
                deleted_at=stamp,
                updated_at=stamp,
            )
            deleted += 1
            continue

        content_bytes = _decode_change_content(change)
        if content_bytes is None:
            errors += 1
            continue

        mime_type = change.get("mime_type")
        if not isinstance(mime_type, str) or not mime_type.strip():
            mime_type = "application/octet-stream"

        result = write_bytes_to_dir(
            agent=agent,
            content_bytes=content_bytes,
            extension="",
            mime_type=mime_type,
            path=path,
            overwrite=True,
        )
        if result.get("status") != "ok":
            errors += 1
            continue

        node_id = result.get("node_id")
        if node_id:
            AgentFsNode.objects.filter(id=node_id).update(updated_at=stamp)

        if existing:
            updated += 1
        else:
            created += 1

    return {
        "status": "ok",
        "created": created,
        "updated": updated,
        "deleted": deleted,
        "skipped": skipped,
        "errors": errors,
        "sync_timestamp": stamp.isoformat(),
    }


def build_filespace_pull_manifest(
    agent: PersistentAgent,
    *,
    since: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Build a pull manifest for syncing filespace into a workspace."""
    try:
        filespace = get_or_create_default_filespace(agent)
    except (DatabaseError, ValueError) as exc:
        logger.warning("Filespace pull failed to resolve filespace for %s: %s", agent.id, exc)
        return {"status": "error", "message": "Filespace unavailable."}

    queryset = AgentFsNode.objects.filter(filespace=filespace)
    if since:
        queryset = queryset.filter(updated_at__gte=since)

    entries = []
    for node in queryset.iterator():
        if node.node_type != AgentFsNode.NodeType.FILE:
            continue
        entry = {
            "node_id": str(node.id),
            "path": node.path,
            "updated_at": node.updated_at.isoformat() if node.updated_at else None,
            "is_deleted": bool(node.is_deleted),
        }
        if not node.is_deleted:
            entry.update(
                {
                    "mime_type": node.mime_type,
                    "size_bytes": node.size_bytes,
                    "download_url": build_signed_filespace_download_url(
                        agent_id=str(agent.id),
                        node_id=str(node.id),
                    ),
                }
            )
        entries.append(entry)

    return {"status": "ok", "files": entries}
