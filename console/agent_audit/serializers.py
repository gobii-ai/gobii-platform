from datetime import datetime, timezone as dt_timezone

from urllib.parse import urlencode

from django.utils import timezone
from django.urls import reverse

from api.models import (
    PersistentAgentMessage,
    PersistentAgentStep,
    PersistentAgentCompletion,
    PersistentAgentPromptArchive,
    PersistentAgentSystemStep,
    PersistentAgentSystemMessage,
)


def _dt_to_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    dt = dt.astimezone(dt_timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def serialize_message(message: PersistentAgentMessage) -> dict:
    timestamp = _dt_to_iso(message.timestamp)
    channel = "web"
    conversation = getattr(message, "conversation", None)
    if conversation:
        channel = conversation.channel
    elif message.from_endpoint_id:
        channel = message.from_endpoint.channel
    attachments = []
    for att in message.attachments.all():
        size_label = None
        try:
            from django.template.defaultfilters import filesizeformat

            size_label = filesizeformat(att.file_size)
        except (TypeError, ValueError):
            size_label = None
        filespace_path = None
        filespace_node_id = None
        download_url = None
        node = getattr(att, "filespace_node", None)
        if node:
            filespace_path = node.path
            filespace_node_id = str(node.id)
        if (filespace_path or filespace_node_id) and message.owner_agent_id:
            query = urlencode({"node_id": filespace_node_id} if filespace_node_id else {"path": filespace_path})
            download_url = f"{reverse('console_agent_fs_download', kwargs={'agent_id': message.owner_agent_id})}?{query}"
        attachments.append(
            {
                "id": str(att.id),
                "filename": att.filename,
                "url": att.file.url if att.file else "",
                "download_url": download_url,
                "filespace_path": filespace_path,
                "filespace_node_id": filespace_node_id,
                "file_size_label": size_label,
            }
        )
    peer_payload = None
    if message.peer_agent_id:
        peer_agent = getattr(message, "peer_agent", None)
        peer_payload = {
            "id": str(message.peer_agent_id),
            "name": getattr(peer_agent, "name", None),
        }
    self_agent = getattr(message, "owner_agent", None)
    self_agent_name = getattr(self_agent, "name", None)
    peer_link_id = getattr(conversation, "peer_link_id", None) if conversation else None
    return {
        "kind": "message",
        "id": str(message.id),
        "timestamp": timestamp,
        "is_outbound": bool(message.is_outbound),
        "channel": channel,
        "body_html": None,
        "body_text": message.body or "",
        "attachments": attachments,
        "peer_agent": peer_payload,
        "peer_link_id": str(peer_link_id) if peer_link_id else None,
        "self_agent_name": self_agent_name,
    }


def serialize_prompt_meta(archive: PersistentAgentPromptArchive | None) -> dict | None:
    if archive is None:
        return None
    return {
        "id": str(archive.id),
        "rendered_at": _dt_to_iso(archive.rendered_at),
        "tokens_before": archive.tokens_before,
        "tokens_after": archive.tokens_after,
        "tokens_saved": archive.tokens_saved,
    }


def serialize_tool_call(step: PersistentAgentStep) -> dict:
    tool_call = getattr(step, "tool_call", None)
    if tool_call is None:
        raise ValueError("Step is missing tool_call relation")
    return {
        "kind": "tool_call",
        "id": str(step.id),
        "timestamp": _dt_to_iso(step.created_at),
        "completion_id": str(step.completion_id) if step.completion_id else None,
        "tool_name": tool_call.tool_name,
        "parameters": tool_call.tool_params,
        "result": tool_call.result,
        "prompt_archive": serialize_prompt_meta(getattr(step, "llm_prompt_archive", None)),
    }


def serialize_completion(completion: PersistentAgentCompletion, prompt_archive: PersistentAgentPromptArchive | None = None, tool_calls: list[dict] | None = None) -> dict:
    return {
        "kind": "completion",
        "id": str(completion.id),
        "timestamp": _dt_to_iso(completion.created_at),
        "completion_type": completion.completion_type,
        "prompt_tokens": completion.prompt_tokens,
        "completion_tokens": completion.completion_tokens,
        "total_tokens": completion.total_tokens,
        "cached_tokens": completion.cached_tokens,
        "llm_model": completion.llm_model,
        "llm_provider": completion.llm_provider,
        "thinking": completion.thinking_content,
        "prompt_archive": serialize_prompt_meta(prompt_archive),
        "tool_calls": tool_calls or [],
    }


def serialize_step(step: PersistentAgentStep) -> dict:
    system_step: PersistentAgentSystemStep | None = getattr(step, "system_step", None)
    return {
        "kind": "step",
        "id": str(step.id),
        "timestamp": _dt_to_iso(step.created_at),
        "description": step.description or "",
        "completion_id": str(step.completion_id) if step.completion_id else None,
        "is_system": bool(system_step),
        "system_code": system_step.code if system_step else None,
        "system_notes": system_step.notes if system_step else None,
    }


def serialize_system_message(message: PersistentAgentSystemMessage) -> dict:
    timestamp = _dt_to_iso(message.created_at)
    delivered_at = _dt_to_iso(message.delivered_at)
    created_by = getattr(message, "created_by", None)
    return {
        "kind": "system_message",
        "id": str(message.id),
        "timestamp": timestamp,
        "delivered_at": delivered_at,
        "body": message.body or "",
        "is_active": bool(message.is_active),
        "broadcast_id": str(message.broadcast_id) if message.broadcast_id else None,
        "created_by": {
            "id": str(created_by.id),
            "email": getattr(created_by, "email", None),
            "name": getattr(created_by, "get_full_name", lambda: None)() or getattr(created_by, "username", None),
        }
        if created_by
        else None,
    }
