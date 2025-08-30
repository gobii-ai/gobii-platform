"""
Helpers to render the agent filespace listing for prompt context.

Produces a compact, human-readable list of files that the agent can
access in its default filespace. Output is capped to ~30KB to keep
prompt size under control, similar to the SQLite schema helper.
"""
import logging
from typing import List

from django.db.models import QuerySet

from api.models import PersistentAgent, AgentFileSpaceAccess, AgentFsNode

logger = logging.getLogger(__name__)

def _get_default_filespace_id(agent: PersistentAgent) -> str | None:
    """
    Return the default filespace ID for the agent, or any if none marked default.
    """
    access = (
        AgentFileSpaceAccess.objects.select_related("filespace")
        .filter(agent=agent)
        .order_by("-is_default", "-granted_at")
        .first()
    )
    return str(access.filespace_id) if access else None


def _format_size(size_bytes: int | None) -> str:
    """
    Formats a size in bytes into a human-readable string.
    """
    if size_bytes is None:
        return "?"
    try:
        # Simple human-readable format; keep it short
        units = ["B", "KB", "MB", "GB", "TB"]
        size = float(size_bytes)
        idx = 0
        while size >= 1024 and idx < len(units) - 1:
            size /= 1024.0
            idx += 1
        return f"{size:.1f} {units[idx]}"
    except Exception as e:
        logger.warning("Failed to format size %s: %s", size_bytes, e)
        return str(size_bytes)


def get_agent_filesystem_prompt(agent: PersistentAgent) -> str:
    """
    Return a human-readable list of file paths within the agent's filespace.

    - Lists only non-deleted file nodes from the agent's default filespace
    - Includes size and mime type when available
    - Caps the returned text to ~30KB with a truncation notice, similar to the sqlite prompt
    """
    fs_id = _get_default_filespace_id(agent)
    if not fs_id:
        return "No filespace configured for this agent."

    # Fetch files ordered by path for readability
    files: QuerySet[AgentFsNode] = (
        AgentFsNode.objects
        .filter(filespace_id=fs_id, is_deleted=False, node_type=AgentFsNode.NodeType.FILE)
        .only("path", "size_bytes", "mime_type")
        .order_by("path")
    )

    if not files.exists():
        return "No files available in the agent filesystem."

    lines: List[str] = []
    total_bytes = 0
    max_bytes = 30000

    for node in files.iterator():
        size = _format_size(node.size_bytes)
        mime = (node.mime_type or "?")
        line = f"- {node.path} (size: {size}, type: {mime})"

        line_len = len(line.encode("utf-8"))
        if lines:  # Add 1 for the newline character
            line_len += 1

        if total_bytes + line_len > max_bytes:
            lines.append("... (truncated – files listing exceeds 30KB limit)")
            break

        lines.append(line)
        total_bytes += line_len

    return "\n".join(lines)

