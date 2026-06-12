from typing import Any

from api.services.sandbox_internal_paths import sandbox_workspace_root_for_agent

WORKSPACE_ALIAS_PREFIX = "/workspace"


def unwrap_filespace_reference(value: str) -> str | None:
    cleaned = value.strip()
    if cleaned.startswith("$[") or cleaned.endswith("]"):
        if not (cleaned.startswith("$[") and cleaned.endswith("]")):
            return None
    if cleaned.startswith("$[") and cleaned.endswith("]"):
        cleaned = cleaned[2:-1].strip()
    return cleaned


def strip_workspace_alias(path: str, *, agent_id: Any = None) -> str:
    if agent_id is not None:
        workspace_root = sandbox_workspace_root_for_agent(agent_id)
        if path == workspace_root:
            return "/"
        if path.startswith(f"{workspace_root}/"):
            return path[len(workspace_root):] or "/"
    if path == WORKSPACE_ALIAS_PREFIX:
        return "/"
    if path.startswith(f"{WORKSPACE_ALIAS_PREFIX}/"):
        return path[len(WORKSPACE_ALIAS_PREFIX):] or "/"
    return path


def normalize_filespace_tool_path(value: Any, *, agent_id: Any = None) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = unwrap_filespace_reference(value)
    if not cleaned:
        return None
    return strip_workspace_alias(cleaned, agent_id=agent_id)
