import json
import logging
from collections import defaultdict
from datetime import datetime, timezone as dt_timezone
from typing import Any, BinaryIO, Iterable, Sequence

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
    serialize_completion,
    serialize_message,
    serialize_prompt_meta,
    serialize_tool_call,
)


logger = logging.getLogger(__name__)

DEFAULT_CHUNK_SIZE = 200


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


def _iter_completion_chunks(agent: PersistentAgent, chunk_size: int = DEFAULT_CHUNK_SIZE) -> Iterable[Sequence[PersistentAgentCompletion]]:
    chunk: list[PersistentAgentCompletion] = []
    queryset = PersistentAgentCompletion.objects.filter(agent=agent).order_by("-created_at", "-id")
    for completion in queryset.iterator(chunk_size=chunk_size):
        chunk.append(completion)
        if len(chunk) >= chunk_size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def _serialize_completion_chunk(
    agent: PersistentAgent,
    completions: Sequence[PersistentAgentCompletion],
    *,
    prompt_payload_cache: dict[str, dict[str, Any] | None],
) -> list[dict[str, Any]]:
    completion_ids = [completion.id for completion in completions]
    if not completion_ids:
        return []

    prompt_archive_by_completion_id: dict[str, Any] = {}
    prompt_steps = (
        PersistentAgentStep.objects.filter(
            agent=agent,
            completion_id__in=completion_ids,
            llm_prompt_archive__isnull=False,
        )
        .select_related("llm_prompt_archive")
        .order_by("completion_id", "-created_at", "-id")
        .iterator(chunk_size=DEFAULT_CHUNK_SIZE)
    )
    for step in prompt_steps:
        completion_id = str(step.completion_id) if step.completion_id else None
        if completion_id and completion_id not in prompt_archive_by_completion_id:
            prompt_archive_by_completion_id[completion_id] = step.llm_prompt_archive

    tool_calls_by_completion_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    tool_steps = (
        PersistentAgentStep.objects.filter(
            agent=agent,
            completion_id__in=completion_ids,
            tool_call__isnull=False,
        )
        .select_related("tool_call", "llm_prompt_archive")
        .order_by("completion_id", "-created_at", "-id")
        .iterator(chunk_size=DEFAULT_CHUNK_SIZE)
    )
    for step in tool_steps:
        completion_id = str(step.completion_id) if step.completion_id else None
        if completion_id is None:
            continue
        tool_calls_by_completion_id[completion_id].append(serialize_tool_call(step))

    serialized: list[dict[str, Any]] = []
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

        completion_payload = serialize_completion(
            completion,
            prompt_archive=None,
            tool_calls=tool_calls_by_completion_id.get(completion_id, []),
        )
        completion_payload["request_duration_ms"] = completion.request_duration_ms
        completion_payload["prompt_archive"] = (
            {
                **(prompt_meta or {}),
                "payload": prompt_payload,
            }
            if prompt_meta or prompt_payload
            else None
        )
        serialized.append(completion_payload)
    return serialized


def _iter_serialized_messages(agent: PersistentAgent, chunk_size: int = DEFAULT_CHUNK_SIZE) -> Iterable[dict[str, Any]]:
    queryset = (
        PersistentAgentMessage.objects.filter(owner_agent=agent)
        .select_related("from_endpoint", "to_endpoint", "conversation__peer_link", "peer_agent", "owner_agent")
        .prefetch_related("attachments__filespace_node")
        .order_by("-timestamp", "-seq")
    )
    for message in queryset.iterator(chunk_size=chunk_size):
        yield serialize_message(message)


def _write_json_bytes(file_obj: BinaryIO, value: str) -> None:
    file_obj.write(value.encode("utf-8"))


def _write_json_value(file_obj: BinaryIO, value: Any) -> None:
    _write_json_bytes(file_obj, json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def write_agent_audit_export_json(
    agent: PersistentAgent,
    file_obj: BinaryIO,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> dict[str, Any]:
    """Write the full audit export JSON to a binary file-like object incrementally."""
    exported_at = _dt_to_iso(timezone.now())
    counts = {
        "completions": PersistentAgentCompletion.objects.filter(agent=agent).count(),
        "messages": PersistentAgentMessage.objects.filter(owner_agent=agent).count(),
    }
    agent_payload = {
        "id": str(agent.id),
        "name": agent.name or "",
        "color": agent.get_display_color(),
    }

    _write_json_bytes(file_obj, "{")
    _write_json_bytes(file_obj, '"exported_at":')
    _write_json_value(file_obj, exported_at)
    _write_json_bytes(file_obj, ',"agent":')
    _write_json_value(file_obj, agent_payload)
    _write_json_bytes(file_obj, ',"counts":')
    _write_json_value(file_obj, counts)

    _write_json_bytes(file_obj, ',"completions":[')
    first_completion = True
    prompt_payload_cache: dict[str, dict[str, Any] | None] = {}
    for completion_chunk in _iter_completion_chunks(agent, chunk_size=chunk_size):
        serialized_chunk = _serialize_completion_chunk(
            agent,
            completion_chunk,
            prompt_payload_cache=prompt_payload_cache,
        )
        for payload in serialized_chunk:
            if not first_completion:
                _write_json_bytes(file_obj, ",")
            _write_json_value(file_obj, payload)
            first_completion = False
    _write_json_bytes(file_obj, "]")

    _write_json_bytes(file_obj, ',"messages":[')
    first_message = True
    for message_payload in _iter_serialized_messages(agent, chunk_size=chunk_size):
        if not first_message:
            _write_json_bytes(file_obj, ",")
        _write_json_value(file_obj, message_payload)
        first_message = False
    _write_json_bytes(file_obj, "]")

    _write_json_bytes(file_obj, "}")
    file_obj.flush()
    file_obj.seek(0)

    return {
        "exported_at": exported_at,
        "counts": counts,
        "agent": agent_payload,
    }
