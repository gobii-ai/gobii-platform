import logging
from typing import Any, Dict, Optional

from django.core.files.storage import default_storage
from django.utils import timezone

from api.agent.files.filespace_service import get_or_create_default_filespace, write_bytes_to_dir
from api.models import AgentFileSpaceAccess, AgentFsNode, PersistentAgent, PersistentAgentCustomTool
from api.services.system_settings import get_max_file_size

logger = logging.getLogger(__name__)


def _resolve_path(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    path = value.strip()
    if path.startswith("$[") and path.endswith("]"):
        path = path[2:-1].strip()
    return path or None


def _agent_has_access(agent: PersistentAgent, filespace_id) -> bool:
    return AgentFileSpaceAccess.objects.filter(agent=agent, filespace_id=filespace_id).exists()


def get_file_str_replace_tool() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "file_str_replace",
            "description": (
                "Replace exact UTF-8 text inside a filespace file without rewriting the whole file. "
                "Works on any text file -- source code, configs, exports, custom tool sources, etc. "
                "By default only the first match is replaced; set replace_all=true to update every match."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Filespace path to the text file to edit.",
                    },
                    "old_text": {
                        "type": "string",
                        "description": "Exact text to replace.",
                    },
                    "new_text": {
                        "type": "string",
                        "description": "Replacement text. Use an empty string to delete the matched text.",
                    },
                    "replace_all": {
                        "type": "boolean",
                        "description": "When true, replace every exact match instead of just the first one.",
                    },
                    "expected_replacements": {
                        "type": "integer",
                        "description": "Optional safety check. If provided, fail unless exactly this many replacements are made.",
                    },
                },
                "required": ["path", "old_text", "new_text"],
            },
        },
    }


def execute_file_str_replace(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    path = _resolve_path(params.get("path"))
    if not path:
        return {"status": "error", "message": "path must be a non-empty string."}

    old_text = params.get("old_text")
    if not isinstance(old_text, str) or not old_text:
        return {"status": "error", "message": "old_text must be a non-empty string."}

    new_text = params.get("new_text")
    if not isinstance(new_text, str):
        return {"status": "error", "message": "new_text must be a string."}

    replace_all_value = params.get("replace_all", False)
    replace_all = replace_all_value is True or str(replace_all_value).lower() == "true"

    expected_value = params.get("expected_replacements")
    if expected_value in (None, ""):
        expected_replacements = None
    else:
        try:
            expected_replacements = int(expected_value)
        except (TypeError, ValueError):
            return {"status": "error", "message": "expected_replacements must be an integer when provided."}
        if expected_replacements < 0:
            return {"status": "error", "message": "expected_replacements must be zero or greater."}

    try:
        filespace = get_or_create_default_filespace(agent)
    except Exception as exc:
        logger.error("Failed to resolve default filespace for agent %s: %s", agent.id, exc)
        return {"status": "error", "message": "No filespace configured for this agent."}

    if not _agent_has_access(agent, filespace.id):
        return {"status": "error", "message": "Agent lacks access to the filespace."}

    node = (
        AgentFsNode.objects.alive()
        .filter(filespace=filespace, path=path)
        .first()
    )
    if node is None:
        return {"status": "error", "message": f"File not found: {path}"}
    if node.node_type != AgentFsNode.NodeType.FILE:
        return {"status": "error", "message": f"Path is a directory: {path}"}
    if not node.content or not getattr(node.content, "name", None):
        return {"status": "error", "message": "File has no stored content."}

    max_size = get_max_file_size()
    if max_size and node.size_bytes and node.size_bytes > max_size:
        return {"status": "error", "message": f"File exceeds maximum allowed size ({node.size_bytes} bytes)."}

    try:
        with default_storage.open(node.content.name, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        logger.error("Failed to read %s for agent %s: %s", path, agent.id, exc)
        return {"status": "error", "message": "Failed to read the target file."}

    try:
        original_text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return {"status": "error", "message": "file_str_replace only supports UTF-8 text files."}

    if replace_all:
        replacement_count = original_text.count(old_text)
        updated_text = original_text.replace(old_text, new_text)
    else:
        replacement_count = 1 if old_text in original_text else 0
        updated_text = original_text.replace(old_text, new_text, 1)

    if replacement_count == 0:
        return {"status": "error", "message": "old_text was not found in the target file."}
    if expected_replacements is not None and replacement_count != expected_replacements:
        return {
            "status": "error",
            "message": (
                f"Replacement count mismatch: expected {expected_replacements}, "
                f"but would replace {replacement_count}."
            ),
        }

    write_result = write_bytes_to_dir(
        agent=agent,
        content_bytes=updated_text.encode("utf-8"),
        extension="",
        mime_type=node.mime_type or "text/plain",
        path=path,
        overwrite=True,
    )
    if write_result.get("status") != "ok":
        return write_result

    PersistentAgentCustomTool.objects.filter(agent=agent, source_path=path).update(updated_at=timezone.now())

    return {
        "status": "ok",
        "message": f"Updated `{path}` with {replacement_count} replacement(s).",
        "path": path,
        "replacements": replacement_count,
    }
