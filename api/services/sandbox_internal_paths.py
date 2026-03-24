from typing import Any

CUSTOM_TOOL_SQLITE_FILESPACE_PATH = "/.gobii/internal/custom_tool_agent_state.sqlite3"
CUSTOM_TOOL_SQLITE_WORKSPACE_PATH = f"/workspace{CUSTOM_TOOL_SQLITE_FILESPACE_PATH}"

_SANDBOX_INTERNAL_FILESPACE_PATHS = {
    CUSTOM_TOOL_SQLITE_FILESPACE_PATH,
}


def is_sandbox_internal_path(path: Any) -> bool:
    if not isinstance(path, str):
        return False
    return path.strip() in _SANDBOX_INTERNAL_FILESPACE_PATHS
