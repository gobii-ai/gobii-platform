import base64
import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from django.db import DatabaseError

from api.agent.files.filespace_service import get_or_create_default_filespace
from api.models import (
    AgentFileSpaceAccess,
    AgentFsNode,
    PersistentAgent,
    PersistentAgentCompletion,
    PersistentAgentToolCall,
)
from api.services.system_settings import get_max_file_size

logger = logging.getLogger(__name__)

MAX_MULTIMODAL_READ_FILE_IMAGES = 3

SUPPORTED_READ_FILE_IMAGE_MIME_TYPES = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/webp",
        "image/gif",
    }
)
SUPPORTED_READ_FILE_IMAGE_EXTENSIONS = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


@dataclass(frozen=True)
class ReadFileImageAttachment:
    path: str
    mime_type: str
    data_url: str


def _resolve_read_file_path(params: Mapping[str, Any] | None) -> str | None:
    if not isinstance(params, Mapping):
        return None
    for key in ("path", "file_path", "filename"):
        value = params.get(key)
        if not isinstance(value, str):
            continue
        cleaned = value.strip()
        if cleaned.startswith("$[") and cleaned.endswith("]"):
            cleaned = cleaned[2:-1].strip()
        if cleaned:
            if not cleaned.startswith("/"):
                cleaned = f"/{cleaned}"
            return cleaned
    return None


def _is_successful_read_file_result(result_text: str) -> bool:
    if not result_text:
        return False
    try:
        result = json.loads(result_text)
    except json.JSONDecodeError:
        return False
    return isinstance(result, dict) and result.get("status") == "ok"


def _supported_image_mime_type(node: AgentFsNode) -> str | None:
    mime_type = (node.mime_type or "").split(";", 1)[0].strip().lower()
    if mime_type in SUPPORTED_READ_FILE_IMAGE_MIME_TYPES:
        return mime_type

    extension = os.path.splitext(node.name or "")[1].lower()
    extension_mime_type = SUPPORTED_READ_FILE_IMAGE_EXTENSIONS.get(extension)
    if extension_mime_type and mime_type in {"", "application/octet-stream"}:
        return extension_mime_type
    return None


def _node_size_bytes(node: AgentFsNode) -> int | None:
    if node.size_bytes is not None:
        return int(node.size_bytes)
    content = getattr(node, "content", None)
    if content is None:
        return None
    try:
        return int(content.size)
    except (OSError, TypeError, ValueError):
        return None


def _read_node_bytes(node: AgentFsNode, *, max_size: int | None) -> bytes:
    content = getattr(node, "content", None)
    if content is None or not getattr(content, "name", None):
        raise ValueError("Node has no stored content.")

    chunks: list[bytes] = []
    total_bytes = 0
    with content.storage.open(content.name, "rb") as src:
        for chunk in iter(lambda: src.read(64 * 1024), b""):
            total_bytes += len(chunk)
            if max_size and total_bytes > max_size:
                raise ValueError("File exceeds maximum allowed size.")
            chunks.append(chunk)
    return b"".join(chunks)


def collect_fresh_read_file_image_attachments(
    agent: PersistentAgent,
    fresh_tool_call_step_ids: Sequence[str] | set[str] | None,
    *,
    max_images: int = MAX_MULTIMODAL_READ_FILE_IMAGES,
) -> list[ReadFileImageAttachment]:
    if not fresh_tool_call_step_ids or max_images <= 0:
        return []

    step_ids = [str(step_id) for step_id in fresh_tool_call_step_ids if step_id]
    if not step_ids:
        return []

    calls = list(
        PersistentAgentToolCall.objects.filter(
            step_id__in=step_ids,
            tool_name="read_file",
            status="complete",
        )
        .select_related("step", "step__completion")
        .order_by("step__created_at", "step_id")
    )
    if not calls:
        return []

    latest_completion = (
        PersistentAgentCompletion.objects.filter(
            agent=agent,
            completion_type=PersistentAgentCompletion.CompletionType.ORCHESTRATOR,
        )
        .only("id")
        .order_by("-created_at")
        .first()
    )
    latest_completion_id = str(latest_completion.id) if latest_completion is not None else None

    requested_paths: list[str] = []
    seen_paths: set[str] = set()
    for call in calls:
        call_completion_id = str(call.step.completion_id) if call.step.completion_id else None
        if call_completion_id and latest_completion_id and call_completion_id != latest_completion_id:
            continue
        if not _is_successful_read_file_result(call.result or ""):
            continue
        path = _resolve_read_file_path(call.tool_params)
        if not path or path in seen_paths:
            continue
        requested_paths.append(path)
        seen_paths.add(path)
        if len(requested_paths) >= max_images:
            break

    if not requested_paths:
        return []

    try:
        filespace = get_or_create_default_filespace(agent)
    except (DatabaseError, ValueError):
        logger.debug("Unable to resolve filespace for multimodal read_file context", exc_info=True)
        return []

    if not AgentFileSpaceAccess.objects.filter(agent=agent, filespace=filespace).exists():
        return []

    nodes = (
        AgentFsNode.objects.alive()
        .filter(
            filespace=filespace,
            path__in=requested_paths,
            node_type=AgentFsNode.NodeType.FILE,
        )
    )
    nodes_by_path = {node.path: node for node in nodes}
    max_size = get_max_file_size()

    attachments: list[ReadFileImageAttachment] = []
    for path in requested_paths:
        node = nodes_by_path.get(path)
        if node is None:
            continue
        mime_type = _supported_image_mime_type(node)
        if not mime_type:
            continue
        size_bytes = _node_size_bytes(node)
        if max_size and size_bytes and size_bytes > max_size:
            continue
        try:
            image_bytes = _read_node_bytes(node, max_size=max_size)
        except (OSError, ValueError):
            logger.debug("Unable to read image bytes for multimodal context: %s", path, exc_info=True)
            continue
        encoded = base64.b64encode(image_bytes).decode("ascii")
        attachments.append(
            ReadFileImageAttachment(
                path=path,
                mime_type=mime_type,
                data_url=f"data:{mime_type};base64,{encoded}",
            )
        )
    return attachments


def filter_vision_capable_failover_configs(
    failover_configs: Sequence[tuple[str, str, dict]] | None,
) -> list[tuple[str, str, dict]]:
    return [
        config
        for config in failover_configs or []
        if len(config) >= 3 and bool((config[2] or {}).get("supports_vision"))
    ]


def attach_read_file_images_to_messages(
    messages: Sequence[dict[str, Any]],
    attachments: Sequence[ReadFileImageAttachment],
) -> list[dict[str, Any]]:
    if not attachments:
        return list(messages)

    updated_messages = [dict(message) for message in messages]
    target_index = next(
        (idx for idx in range(len(updated_messages) - 1, -1, -1) if updated_messages[idx].get("role") == "user"),
        None,
    )
    if target_index is None:
        updated_messages.append({"role": "user", "content": []})
        target_index = len(updated_messages) - 1

    target = dict(updated_messages[target_index])
    content = target.get("content", "")
    if isinstance(content, list):
        parts = list(content)
    elif isinstance(content, str):
        parts = [{"type": "text", "text": content}]
    else:
        parts = [{"type": "text", "text": str(content)}]

    for attachment in attachments:
        parts.append({"type": "text", "text": f"Image from read_file: {attachment.path}"})
        parts.append({"type": "image_url", "image_url": {"url": attachment.data_url}})

    target["content"] = parts
    updated_messages[target_index] = target
    return updated_messages


def prepare_multimodal_read_file_request(
    messages: Sequence[dict[str, Any]],
    failover_configs: Sequence[tuple[str, str, dict]] | None,
    attachments: Sequence[ReadFileImageAttachment],
) -> tuple[list[dict[str, Any]], list[tuple[str, str, dict]], bool]:
    if not attachments:
        return list(messages), list(failover_configs or []), False

    vision_configs = filter_vision_capable_failover_configs(failover_configs)
    if not vision_configs:
        return list(messages), list(failover_configs or []), False

    return attach_read_file_images_to_messages(messages, attachments), vision_configs, True
