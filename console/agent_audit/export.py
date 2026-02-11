import json
import logging
from collections import defaultdict
from datetime import datetime, timezone as dt_timezone
from typing import Any

import zstandard as zstd
from django.core.files.storage import default_storage
from django.utils import timezone

from api.models import (
    PersistentAgent,
    PersistentAgentCompletion,
    PersistentAgentMessage,
    PersistentAgentStep,
)
from console.agent_audit.serializers import (
    serialize_message,
    serialize_prompt_meta,
    serialize_tool_call,
)


logger = logging.getLogger(__name__)


def _dt_to_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    dt = dt.astimezone(dt_timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def _load_prompt_archive_payload(archive) -> dict[str, Any] | None:
    storage_key = getattr(archive, "storage_key", "")
    if not storage_key:
        return None
    if not default_storage.exists(storage_key):
        return {"error": "missing_payload"}

    try:
        with default_storage.open(storage_key, "rb") as stored:
            dctx = zstd.ZstdDecompressor()
            payload_bytes = dctx.decompress(stored.read())
    except (FileNotFoundError, OSError, zstd.ZstdError):
        logger.warning("Failed to read prompt archive payload for %s", getattr(archive, "id", None), exc_info=True)
        return {"error": "read_failed"}

    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.warning("Failed to decode prompt archive payload for %s", getattr(archive, "id", None), exc_info=True)
        return {"error": "decode_failed"}

    return payload if isinstance(payload, dict) else {"raw_payload": payload}


def build_agent_audit_export_payload(agent: PersistentAgent) -> dict[str, Any]:
    completions = list(
        PersistentAgentCompletion.objects.filter(agent=agent).order_by("-created_at", "-id")
    )
    completion_ids = [completion.id for completion in completions]

    prompt_steps = list(
        PersistentAgentStep.objects.filter(
            agent=agent,
            completion_id__in=completion_ids,
            llm_prompt_archive__isnull=False,
        )
        .select_related("llm_prompt_archive")
        .order_by("-created_at", "-id")
    )
    prompt_archive_by_completion_id: dict[str, Any] = {}
    for step in prompt_steps:
        completion_id = str(step.completion_id) if step.completion_id else None
        if completion_id and completion_id not in prompt_archive_by_completion_id:
            prompt_archive_by_completion_id[completion_id] = step.llm_prompt_archive

    tool_steps = list(
        PersistentAgentStep.objects.filter(
            agent=agent,
            completion_id__in=completion_ids,
            tool_call__isnull=False,
        )
        .select_related("tool_call", "llm_prompt_archive")
        .order_by("-created_at", "-id")
    )
    tool_calls_by_completion_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for step in tool_steps:
        completion_id = str(step.completion_id) if step.completion_id else None
        if completion_id is None:
            continue
        tool_calls_by_completion_id[completion_id].append(serialize_tool_call(step))

    prompt_payload_cache: dict[str, dict[str, Any] | None] = {}
    serialized_completions: list[dict[str, Any]] = []
    for completion in completions:
        completion_id = str(completion.id)
        archive = prompt_archive_by_completion_id.get(completion_id)
        prompt_meta = serialize_prompt_meta(archive) if archive is not None else None
        prompt_payload: dict[str, Any] | None = None
        if archive is not None:
            archive_id = str(archive.id)
            if archive_id not in prompt_payload_cache:
                prompt_payload_cache[archive_id] = _load_prompt_archive_payload(archive)
            prompt_payload = prompt_payload_cache[archive_id]

        serialized_completions.append(
            {
                "kind": "completion",
                "id": completion_id,
                "timestamp": _dt_to_iso(completion.created_at),
                "completion_type": completion.completion_type,
                "response_id": completion.response_id,
                "request_duration_ms": completion.request_duration_ms,
                "prompt_tokens": completion.prompt_tokens,
                "completion_tokens": completion.completion_tokens,
                "total_tokens": completion.total_tokens,
                "cached_tokens": completion.cached_tokens,
                "llm_model": completion.llm_model,
                "llm_provider": completion.llm_provider,
                "thinking": completion.thinking_content,
                "prompt_archive": {
                    **(prompt_meta or {}),
                    "payload": prompt_payload,
                }
                if prompt_meta or prompt_payload
                else None,
                "tool_calls": tool_calls_by_completion_id.get(completion_id, []),
            }
        )

    messages = list(
        PersistentAgentMessage.objects.filter(owner_agent=agent)
        .select_related("from_endpoint", "to_endpoint", "conversation__peer_link", "peer_agent", "owner_agent")
        .prefetch_related("attachments__filespace_node")
        .order_by("-timestamp", "-seq")
    )
    serialized_messages = [serialize_message(message) for message in messages]

    return {
        "exported_at": _dt_to_iso(timezone.now()),
        "agent": {
            "id": str(agent.id),
            "name": agent.name or "",
            "color": agent.get_display_color(),
        },
        "counts": {
            "completions": len(serialized_completions),
            "messages": len(serialized_messages),
        },
        "completions": serialized_completions,
        "messages": serialized_messages,
    }
