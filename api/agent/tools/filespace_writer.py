import hashlib
import logging
import os
from typing import Any, Dict, Optional

from django.conf import settings
from django.core.files.base import ContentFile
from django.utils.text import get_valid_filename

from api.agent.files.filespace_service import (
    dedupe_name,
    get_or_create_default_filespace,
    get_or_create_dir,
)
from api.models import AgentFileSpaceAccess, AgentFsNode, PersistentAgent

logger = logging.getLogger(__name__)

EXPORTS_DIR_NAME = "exports"


def _get_filespace(agent: PersistentAgent):
    try:
        return get_or_create_default_filespace(agent)
    except Exception as exc:
        logger.error("Failed to resolve default filespace for agent %s: %s", agent.id, exc)
        return None


def _agent_has_access(agent: PersistentAgent, filespace_id) -> bool:
    return AgentFileSpaceAccess.objects.filter(agent=agent, filespace_id=filespace_id).exists()


def _normalize_filename(
    raw_name: Optional[str],
    fallback_name: str,
    extension: str,
) -> str:
    name = (raw_name or "").strip()
    if not name:
        name = fallback_name

    name = get_valid_filename(os.path.basename(name)) or fallback_name
    if not name.lower().endswith(extension):
        name = f"{name}{extension}"
    return name


def write_bytes_to_exports(
    agent: PersistentAgent,
    content_bytes: bytes,
    filename: Optional[str],
    fallback_name: str,
    extension: str,
    mime_type: str,
) -> Dict[str, Any]:
    if not isinstance(content_bytes, (bytes, bytearray)):
        return {"status": "error", "message": "File content must be bytes."}

    content_bytes = bytes(content_bytes)
    max_size = getattr(settings, "MAX_FILE_SIZE", None)
    if max_size and len(content_bytes) > max_size:
        return {
            "status": "error",
            "message": f"File exceeds maximum allowed size ({len(content_bytes)} bytes > {max_size} bytes).",
        }

    filespace = _get_filespace(agent)
    if not filespace:
        return {"status": "error", "message": "No filespace configured for this agent."}

    if not _agent_has_access(agent, filespace.id):
        return {"status": "error", "message": "Agent lacks access to the filespace."}

    try:
        exports_dir = get_or_create_dir(filespace, None, EXPORTS_DIR_NAME)
    except Exception as exc:
        logger.exception("Failed to resolve exports directory for agent %s: %s", agent.id, exc)
        return {"status": "error", "message": "Failed to access the exports directory."}

    base_name = _normalize_filename(filename, fallback_name, extension)
    name = dedupe_name(filespace, exports_dir, base_name)
    checksum = hashlib.sha256(content_bytes).hexdigest()

    node = AgentFsNode(
        filespace=filespace,
        parent=exports_dir,
        node_type=AgentFsNode.NodeType.FILE,
        name=name,
        created_by_agent=agent,
        mime_type=mime_type,
        checksum_sha256=checksum,
    )
    node.save()

    try:
        node.content.save(name, ContentFile(content_bytes), save=True)
        node.refresh_from_db()
    except Exception:
        logger.exception("Failed to persist file to exports for agent %s", agent.id)
        node.delete()
        return {"status": "error", "message": "Failed to save the file in the filespace."}

    return {
        "status": "ok",
        "path": node.path,
        "node_id": str(node.id),
        "filename": node.name,
    }
