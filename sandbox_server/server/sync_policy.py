import posixpath
from pathlib import Path
from typing import Any

SCRATCH_DIR_RELATIVE_PATH = ".scratch"
REPO_WORKDIR_RELATIVE_PATH = ".scratch/repos"
SCRATCH_DIR_FILESPACE_PATH = "/.scratch"
REPO_WORKDIR_FILESPACE_PATH = "/.scratch/repos"
GOBII_SCRATCH_DIR_ENV = "GOBII_SCRATCH_DIR"
GOBII_REPO_WORKDIR_ENV = "GOBII_REPO_WORKDIR"

HEAVY_SYNC_DIR_NAMES = {
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


def scratch_dir_for_agent_root(agent_root: Path) -> Path:
    return agent_root / SCRATCH_DIR_RELATIVE_PATH


def repo_workdir_for_agent_root(agent_root: Path) -> Path:
    return scratch_dir_for_agent_root(agent_root) / "repos"


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
    return any(part in HEAVY_SYNC_DIR_NAMES for part in parts)


def should_prune_workspace_dir(agent_root: Path, path: Path) -> bool:
    try:
        resolved = path.resolve()
        root = agent_root.resolve()
    except OSError:
        return False
    scratch_dir = scratch_dir_for_agent_root(root)
    if resolved == scratch_dir or scratch_dir in resolved.parents:
        return True
    return path.name in HEAVY_SYNC_DIR_NAMES


def is_ignored_workspace_path(agent_root: Path, path: Path) -> bool:
    try:
        resolved = path.resolve()
        root = agent_root.resolve()
    except OSError:
        return False
    scratch_dir = scratch_dir_for_agent_root(root)
    if resolved == scratch_dir or scratch_dir in resolved.parents:
        return True
    relative_parts = resolved.relative_to(root).parts
    return any(part in HEAVY_SYNC_DIR_NAMES for part in relative_parts)
