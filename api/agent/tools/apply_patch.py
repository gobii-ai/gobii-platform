import logging
import posixpath
from dataclasses import dataclass
from typing import Any, Dict, Optional

from django.core.files.storage import default_storage
from django.utils import timezone
from django.utils.text import get_valid_filename

from api.agent.files.filespace_service import get_or_create_default_filespace, write_bytes_to_dir
from api.models import AgentFileSpaceAccess, AgentFsNode, PersistentAgent, PersistentAgentCustomTool
from api.services.system_settings import get_max_file_size

logger = logging.getLogger(__name__)


BEGIN_PATCH = "*** Begin Patch"
END_PATCH = "*** End Patch"
ADD_FILE = "*** Add File: "
DELETE_FILE = "*** Delete File: "
UPDATE_FILE = "*** Update File: "
MOVE_TO = "*** Move to: "


@dataclass
class _PatchOp:
    kind: str
    path: str
    lines: list[str]
    move_to: str | None = None


def _resolve_path(value: Any) -> tuple[str | None, str | None]:
    if not isinstance(value, str):
        return None, "Path must be a string."
    path = value.strip()
    if path.startswith("$[") and path.endswith("]"):
        path = path[2:-1].strip()
    if not path:
        return None, "Path must be non-empty."
    if not path.startswith("/"):
        return None, "Path must be an absolute filespace path like /tools/my_tool.py."
    if "\x00" in path:
        return None, "Path must not contain null bytes."

    normalized = posixpath.normpath(path)
    parts = [part for part in normalized.split("/") if part]
    if not parts:
        return None, "Path must include a filename."
    if any(part in {".", ".."} for part in path.split("/")):
        return None, "Path must not contain '.' or '..' components."
    if parts[-1] in {".", ".."}:
        return None, "Path must include a filename."
    for part in parts:
        if get_valid_filename(part) != part:
            return None, "Path contains unsafe characters."
    return normalized, None


def _agent_has_access(agent: PersistentAgent, filespace_id) -> bool:
    return AgentFileSpaceAccess.objects.filter(agent=agent, filespace_id=filespace_id).exists()


def _patch_tool_description() -> str:
    return (
        "Apply a unified, reviewable patch to UTF-8 filespace files. Prefer this over exact string replacement for "
        "source code, HTML/CSS/JS, config, and custom tool edits. The patch must be a single string using this format:\n"
        "*** Begin Patch\n"
        "*** Add File: /path/to/file\n"
        "+new file line\n"
        "*** Delete File: /path/to/file\n"
        "*** Update File: /path/to/file\n"
        "*** Move to: /new/path\n"
        "@@\n"
        " context line\n"
        "-old line\n"
        "+new line\n"
        "*** End Patch\n"
        "Use Add File for new files, Delete File to trash files, and Update File for line-based edits. "
        "For Update File, include enough unchanged context lines (prefixed with a space) to make the edit unique. "
        "Paths are filespace paths such as /tools/my_tool.py or $[/tools/my_tool.py]."
    )


def get_apply_patch_tool() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "apply_patch",
            "description": _patch_tool_description(),
            "parameters": {
                "type": "object",
                "properties": {
                    "patch": {
                        "type": "string",
                        "description": "Patch text beginning with *** Begin Patch and ending with *** End Patch.",
                    },
                },
                "required": ["patch"],
            },
        },
    }


def _parse_patch(patch_text: str) -> tuple[list[_PatchOp] | None, str | None]:
    raw_lines = patch_text.splitlines()
    if not raw_lines or raw_lines[0].strip() != BEGIN_PATCH:
        return None, f"Patch must start with {BEGIN_PATCH}."
    if raw_lines[-1].strip() != END_PATCH:
        return None, f"Patch must end with {END_PATCH}."

    ops: list[_PatchOp] = []
    index = 1
    while index < len(raw_lines) - 1:
        line = raw_lines[index]
        if line.startswith(ADD_FILE):
            path, path_error = _resolve_path(line[len(ADD_FILE):])
            if path_error:
                return None, f"Add File path error: {path_error}"
            index += 1
            content_lines: list[str] = []
            while index < len(raw_lines) - 1 and not raw_lines[index].startswith("*** "):
                add_line = raw_lines[index]
                if not add_line.startswith("+"):
                    return None, f"Add File lines must start with '+': {add_line!r}"
                content_lines.append(add_line[1:])
                index += 1
            ops.append(_PatchOp(kind="add", path=path or "", lines=content_lines))
            continue

        if line.startswith(DELETE_FILE):
            path, path_error = _resolve_path(line[len(DELETE_FILE):])
            if path_error:
                return None, f"Delete File path error: {path_error}"
            ops.append(_PatchOp(kind="delete", path=path or "", lines=[]))
            index += 1
            continue

        if line.startswith(UPDATE_FILE):
            path, path_error = _resolve_path(line[len(UPDATE_FILE):])
            if path_error:
                return None, f"Update File path error: {path_error}"
            index += 1
            move_to = None
            if index < len(raw_lines) - 1 and raw_lines[index].startswith(MOVE_TO):
                move_to, move_error = _resolve_path(raw_lines[index][len(MOVE_TO):])
                if move_error:
                    return None, f"Move target path error: {move_error}"
                index += 1
            change_lines: list[str] = []
            while index < len(raw_lines) - 1 and not raw_lines[index].startswith("*** "):
                change_line = raw_lines[index]
                if change_line.startswith("@@"):
                    change_lines.append(change_line)
                elif change_line.startswith(("+", "-", " ")):
                    change_lines.append(change_line)
                else:
                    return None, f"Update lines must start with ' ', '+', '-', or '@@': {change_line!r}"
                index += 1
            if not change_lines and not move_to:
                return None, f"Update File {path} has no changes."
            ops.append(_PatchOp(kind="update", path=path or "", lines=change_lines, move_to=move_to))
            continue

        return None, f"Unknown patch section: {line!r}"

    if not ops:
        return None, "Patch contains no file operations."
    return ops, None


def _get_filespace(agent: PersistentAgent):
    try:
        filespace = get_or_create_default_filespace(agent)
    except Exception as exc:
        logger.error("Failed to resolve default filespace for agent %s: %s", agent.id, exc)
        return None, {"status": "error", "message": "No filespace configured for this agent."}

    if not _agent_has_access(agent, filespace.id):
        return None, {"status": "error", "message": "Agent lacks access to the filespace."}
    return filespace, None


def _get_alive_node(filespace, path: str) -> AgentFsNode | None:
    return AgentFsNode.objects.alive().filter(filespace=filespace, path=path).first()


def _read_utf8_node(node: AgentFsNode) -> tuple[str | None, dict[str, Any] | None]:
    if node.node_type != AgentFsNode.NodeType.FILE:
        return None, {"status": "error", "message": f"Path is a directory: {node.path}"}
    if not node.content or not getattr(node.content, "name", None):
        return None, {"status": "error", "message": f"File has no stored content: {node.path}"}

    max_size = get_max_file_size()
    if max_size and node.size_bytes and node.size_bytes > max_size:
        return None, {"status": "error", "message": f"File exceeds maximum allowed size ({node.size_bytes} bytes)."}

    try:
        with default_storage.open(node.content.name, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        logger.error("Failed to read %s: %s", node.path, exc)
        return None, {"status": "error", "message": f"Failed to read the target file: {node.path}"}

    try:
        return raw.decode("utf-8"), None
    except UnicodeDecodeError:
        return None, {"status": "error", "message": f"apply_patch only supports UTF-8 text files: {node.path}"}


def _split_text(text: str) -> tuple[list[str], bool]:
    return text.splitlines(), text.endswith("\n")


def _join_text(lines: list[str], trailing_newline: bool) -> str:
    if not lines:
        return "\n" if trailing_newline else ""
    text = "\n".join(lines)
    return f"{text}\n" if trailing_newline else text


def _change_groups(change_lines: list[str]) -> list[list[str]]:
    groups: list[list[str]] = []
    current: list[str] = []
    saw_marker = False
    for line in change_lines:
        if line.startswith("@@"):
            saw_marker = True
            if current:
                groups.append(current)
                current = []
            continue
        current.append(line)
    if current or not saw_marker:
        groups.append(current)
    return groups


def _find_subsequence(lines: list[str], needle: list[str], start: int) -> int:
    if not needle:
        return start
    max_start = len(lines) - len(needle)
    for index in range(start, max_start + 1):
        if lines[index:index + len(needle)] == needle:
            return index
    return -1


def _apply_update(text: str, change_lines: list[str]) -> tuple[str | None, str | None, int]:
    if not change_lines:
        return text, None, 0

    lines, trailing_newline = _split_text(text)
    search_start = 0
    applied = 0

    for group in _change_groups(change_lines):
        if not group:
            continue

        old_lines: list[str] = []
        new_lines: list[str] = []
        changed = False
        for line in group:
            prefix = line[:1]
            value = line[1:]
            if prefix == " ":
                old_lines.append(value)
                new_lines.append(value)
            elif prefix == "-":
                old_lines.append(value)
                changed = True
            elif prefix == "+":
                new_lines.append(value)
                changed = True

        if not changed:
            continue
        match_index = _find_subsequence(lines, old_lines, search_start)
        if match_index < 0:
            preview = "\n".join(old_lines[:5])
            return None, f"Patch context not found. First unmatched old/context lines:\n{preview}", applied

        lines = lines[:match_index] + new_lines + lines[match_index + len(old_lines):]
        search_start = match_index + len(new_lines)
        applied += 1

    return _join_text(lines, trailing_newline), None, applied


def _touch_custom_tool_sources(agent: PersistentAgent, paths: list[str]) -> None:
    if paths:
        PersistentAgentCustomTool.objects.filter(agent=agent, source_path__in=paths).update(updated_at=timezone.now())


def execute_apply_patch(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    patch_text = params.get("patch")
    if not isinstance(patch_text, str) or not patch_text.strip():
        return {"status": "error", "message": "patch must be a non-empty string."}

    ops, parse_error = _parse_patch(patch_text.strip())
    if parse_error:
        return {"status": "error", "message": parse_error}

    filespace, filespace_error = _get_filespace(agent)
    if filespace_error:
        return filespace_error

    changed_paths: list[str] = []
    created_paths: list[str] = []
    updated_paths: list[str] = []
    deleted_paths: list[str] = []

    for op in ops or []:
        node = _get_alive_node(filespace, op.path)

        if op.kind == "add":
            if node is not None:
                return {"status": "error", "message": f"File already exists: {op.path}"}
            content = "\n".join(op.lines)
            if op.lines:
                content += "\n"
            write_result = write_bytes_to_dir(
                agent=agent,
                content_bytes=content.encode("utf-8"),
                extension="",
                mime_type="text/plain",
                path=op.path,
                overwrite=False,
            )
            if write_result.get("status") != "ok":
                return write_result
            created_paths.append(op.path)
            changed_paths.append(op.path)
            continue

        if node is None:
            return {"status": "error", "message": f"File not found: {op.path}"}

        if op.kind == "delete":
            if node.node_type != AgentFsNode.NodeType.FILE:
                return {"status": "error", "message": f"Path is a directory: {op.path}"}
            node.trash_subtree()
            deleted_paths.append(op.path)
            changed_paths.append(op.path)
            continue

        if op.kind == "update":
            original_text, read_error = _read_utf8_node(node)
            if read_error:
                return read_error
            updated_text, apply_error, hunks_applied = _apply_update(original_text or "", op.lines)
            if apply_error:
                return {"status": "error", "message": apply_error, "path": op.path, "hunks_applied": hunks_applied}
            target_path = op.move_to or op.path
            if op.move_to and op.move_to != op.path and _get_alive_node(filespace, target_path) is not None:
                return {"status": "error", "message": f"Move target already exists: {target_path}"}
            write_result = write_bytes_to_dir(
                agent=agent,
                content_bytes=(updated_text or "").encode("utf-8"),
                extension="",
                mime_type=node.mime_type or "text/plain",
                path=target_path,
                overwrite=True,
            )
            if write_result.get("status") != "ok":
                return write_result
            if op.move_to and op.move_to != op.path:
                node.trash_subtree()
            updated_paths.append(target_path)
            changed_paths.append(target_path)
            continue

    _touch_custom_tool_sources(agent, changed_paths)

    return {
        "status": "ok",
        "message": f"Applied patch to {len(changed_paths)} file operation(s).",
        "created": created_paths,
        "updated": updated_paths,
        "deleted": deleted_paths,
        "paths": changed_paths,
    }
