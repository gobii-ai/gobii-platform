"""
Helpers to render the agent filespace listing for prompt context.

Produces a compact, human-readable list of files that the agent can
access in its default filespace. Output is capped to ~30KB to keep
prompt size under control, similar to the SQLite schema helper.

Note: URLs are NOT shown to prevent LLM from copying/corrupting them.
Use $[/path] placeholders for embeds. For attachments, pass $[/path] via the
attachments param on send_* tools.
Charts/exports are referenced as file variables like $[/charts/...] and $[/exports/...].
"""
import logging
from typing import List, Sequence

from django.db.models import QuerySet

from api.models import PersistentAgent, AgentFileSpaceAccess, AgentFsNode

logger = logging.getLogger(__name__)
MAX_RECENT_FILES_IN_PROMPT = 30

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


def format_agent_filesystem_prompt(
    file_nodes: Sequence[object],
    *,
    has_filespace: bool,
    total_files: int | None = None,
    max_rows: int = MAX_RECENT_FILES_IN_PROMPT,
) -> str:
    if not has_filespace:
        return (
            "No filespace configured for this agent. "
            "Tool results/messages/file index live in SQLite __tool_results, __messages, and __files."
        )

    if not file_nodes:
        return (
            "No files available in the agent filesystem. "
            "Tool results/messages/file index live in SQLite __tool_results, __messages, and __files."
        )

    display_count = min(len(file_nodes), max_rows)
    if total_files is not None and total_files > display_count:
        header = (
            f"Most recent files in agent filespace (showing {display_count} of {total_files}; "
            "use read_file for contents; for attachments, pass $[/path] via the attachments param on send_* tools):"
        )
    else:
        header = (
            f"Most recent files in agent filespace (up to {max_rows}; "
            "use read_file for contents; for attachments, pass $[/path] via the attachments param on send_* tools):"
        )
    lines: List[str] = [header]
    total_bytes = len(header.encode("utf-8"))
    max_bytes = 30000

    for node in list(file_nodes)[:max_rows]:
        size = _format_size(getattr(node, "size_bytes", None))
        mime = (getattr(node, "mime_type", None) or "?")
        updated_raw = getattr(node, "updated_at", None)
        updated = updated_raw.isoformat() if hasattr(updated_raw, "isoformat") else (str(updated_raw) if updated_raw else "?")
        line = f"- $[{getattr(node, 'path', '')}] ({size}, {mime}, updated {updated})"

        line_len = len(line.encode("utf-8"))
        if lines:
            line_len += 1

        if total_bytes + line_len > max_bytes:
            lines.append("... (truncated – files listing exceeds 30KB limit)")
            break

        lines.append(line)
        total_bytes += line_len

    return "\n".join(lines)


def get_agent_filesystem_prompt(agent: PersistentAgent) -> str:
    """
    Return a human-readable list of recent file paths within the agent's filespace.

    - Lists only non-deleted file nodes from the agent's default filespace
    - Shows only the most recently updated files (up to MAX_RECENT_FILES_IN_PROMPT)
    - Includes size and mime type when available
    - Does NOT show URLs (prevents LLM from copying/corrupting signed URLs)
    - Caps the returned text to ~30KB with a truncation notice
    """
    fs_id = _get_default_filespace_id(agent)
    if not fs_id:
        return format_agent_filesystem_prompt([], has_filespace=False)

    files: QuerySet[AgentFsNode] = (
        AgentFsNode.objects
        .filter(filespace_id=fs_id, is_deleted=False, node_type=AgentFsNode.NodeType.FILE)
        .only("id", "path", "size_bytes", "mime_type", "updated_at")
        .order_by("-updated_at", "-created_at", "path")[:MAX_RECENT_FILES_IN_PROMPT]
    )
    file_nodes = list(files)
    return format_agent_filesystem_prompt(
        file_nodes,
        has_filespace=True,
        total_files=len(file_nodes),
        max_rows=MAX_RECENT_FILES_IN_PROMPT,
    )
