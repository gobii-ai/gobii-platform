import posixpath
from typing import Any

SCRATCH_DIR_FILESPACE_PATH = "/.scratch"
SCRATCH_DIR_WORKSPACE_PATH = "/workspace/.scratch"
REPO_WORKDIR_FILESPACE_PATH = "/.scratch/repos"
REPO_WORKDIR_WORKSPACE_PATH = "/workspace/.scratch/repos"
GOBII_SCRATCH_DIR_ENV = "GOBII_SCRATCH_DIR"
GOBII_REPO_WORKDIR_ENV = "GOBII_REPO_WORKDIR"

_HEAVY_SYNC_DIR_NAMES = {
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


def normalize_filespace_path(path: Any) -> str | None:
    if not isinstance(path, str):
        return None
    cleaned = path.strip()
    if not cleaned:
        return None
    normalized = posixpath.normpath(cleaned)
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    return normalized


def is_ignored_filespace_path(path: Any) -> bool:
    normalized = normalize_filespace_path(path)
    if normalized is None:
        return False
    if normalized == SCRATCH_DIR_FILESPACE_PATH or normalized.startswith(f"{SCRATCH_DIR_FILESPACE_PATH}/"):
        return True
    parts = [part for part in normalized.split("/") if part]
    return any(part in _HEAVY_SYNC_DIR_NAMES for part in parts)
