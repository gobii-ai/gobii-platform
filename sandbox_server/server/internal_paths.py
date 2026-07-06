"""Sandbox-internal path constants.

Keep this module free of Django app imports so the standalone sandbox image can
boot without the main application package on sys.path.
"""

import posixpath
from typing import Any

CUSTOM_TOOL_SQLITE_FILESPACE_PATH = "/.gobii/internal/custom_tool_agent_state.sqlite3"
SCRATCH_DIR_NAME = ".scratch"
SCRATCH_DIR_FILESPACE_PATH = "/.scratch"
GOBII_SCRATCH_DIR_ENV = "GOBII_SCRATCH_DIR"
GOBII_REPO_WORKDIR_ENV = "GOBII_REPO_WORKDIR"

FILESYSTEM_SYNC_IGNORED_NAMES = {
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


def _normalize_filespace_path(path: Any) -> str | None:
    if not isinstance(path, str):
        return None
    normalized = posixpath.normpath(path.strip() or "/")
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    return normalized


def is_filespace_sync_ignored_path(path: Any) -> bool:
    normalized = _normalize_filespace_path(path)
    if normalized is None:
        return False
    if normalized == SCRATCH_DIR_FILESPACE_PATH or normalized.startswith(f"{SCRATCH_DIR_FILESPACE_PATH}/"):
        return True
    return any(part in FILESYSTEM_SYNC_IGNORED_NAMES for part in normalized.split("/") if part)
