import posixpath
from typing import Any

# Keep this path in sync with sandbox_server/server/internal_paths.py.
CUSTOM_TOOL_SQLITE_FILESPACE_PATH = "/.gobii/internal/custom_tool_agent_state.sqlite3"
CUSTOM_TOOL_SQLITE_WORKSPACE_PATH = f"/workspace{CUSTOM_TOOL_SQLITE_FILESPACE_PATH}"

_SANDBOX_INTERNAL_FILESPACE_PATHS = {
    "/.gobii",
    "/.uv-cache",
    CUSTOM_TOOL_SQLITE_FILESPACE_PATH,
}
_SANDBOX_INTERNAL_FILESPACE_PREFIXES = (
    "/.gobii/",
    "/.uv-cache/",
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
