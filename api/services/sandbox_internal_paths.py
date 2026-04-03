import posixpath
import re
from typing import Any

# Keep this path in sync with sandbox_server/server/internal_paths.py.
CUSTOM_TOOL_SQLITE_FILESPACE_PATH = "/.gobii/internal/custom_tool_agent_state.sqlite3"

_SANDBOX_INTERNAL_FILESPACE_PATHS = {
    "/.gobii",
    "/.uv-cache",
    CUSTOM_TOOL_SQLITE_FILESPACE_PATH,
}
_SANDBOX_INTERNAL_FILESPACE_PREFIXES = (
    "/.gobii/",
    "/.uv-cache/",
)


def _safe_workspace_identity_segment(value: Any, *, fallback: str = "agent") -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]", "_", str(value or "").strip())
    return cleaned or fallback


def sandbox_workspace_root_for_agent(agent_id: Any) -> str:
    return posixpath.join("/workspace", _safe_workspace_identity_segment(agent_id))


def custom_tool_sqlite_workspace_path(agent_id: Any) -> str:
    return posixpath.join(
        sandbox_workspace_root_for_agent(agent_id),
        CUSTOM_TOOL_SQLITE_FILESPACE_PATH.lstrip("/"),
    )


def is_sandbox_internal_path(path: Any) -> bool:
    if not isinstance(path, str):
        return False
    normalized = posixpath.normpath(path.strip() or "/")
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    if normalized in _SANDBOX_INTERNAL_FILESPACE_PATHS:
        return True
    return any(normalized.startswith(prefix) for prefix in _SANDBOX_INTERNAL_FILESPACE_PREFIXES)
