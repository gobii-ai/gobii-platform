import posixpath
import re
from typing import Any

# Keep this path in sync with sandbox_server/server/internal_paths.py.
CUSTOM_TOOL_SQLITE_FILESPACE_PATH = "/.gobii/internal/custom_tool_agent_state.sqlite3"
SCRATCH_DIR_FILESPACE_PATH = "/.scratch"
SCRATCH_DIR_WORKSPACE_PATH = "/workspace/.scratch"
REPO_WORKDIR_WORKSPACE_PATH = "/workspace/.scratch/repos"
GOBII_SCRATCH_DIR_ENV = "GOBII_SCRATCH_DIR"
GOBII_REPO_WORKDIR_ENV = "GOBII_REPO_WORKDIR"

_FILESYSTEM_SYNC_IGNORED_NAMES = {
    ".cache",
    ".git",
    ".mypy_cache",
    ".next",
    ".nox",
    ".parcel-cache",
    ".pnpm-store",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".turbo",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "target",
    "venv",
}

_SANDBOX_INTERNAL_FILESPACE_PATHS = {
    "/.gobii",
    "/.uv-cache",
    CUSTOM_TOOL_SQLITE_FILESPACE_PATH,
}
_SANDBOX_INTERNAL_FILESPACE_PREFIXES = (
    "/.gobii/",
    "/.uv-cache/",
)


def _normalize_filespace_path(path: Any) -> str | None:
    if not isinstance(path, str):
        return None
    normalized = posixpath.normpath(path.strip() or "/")
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    return normalized


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
    normalized = _normalize_filespace_path(path)
    if normalized is None:
        return False
    if normalized in _SANDBOX_INTERNAL_FILESPACE_PATHS:
        return True
    return any(normalized.startswith(prefix) for prefix in _SANDBOX_INTERNAL_FILESPACE_PREFIXES)


def is_filespace_sync_ignored_path(path: Any) -> bool:
    normalized = _normalize_filespace_path(path)
    if normalized is None:
        return False
    if normalized == SCRATCH_DIR_FILESPACE_PATH or normalized.startswith(f"{SCRATCH_DIR_FILESPACE_PATH}/"):
        return True
    return any(part in _FILESYSTEM_SYNC_IGNORED_NAMES for part in normalized.split("/") if part)
