import json
import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Any, BinaryIO, Iterable, Sequence

from botocore.exceptions import ClientError
from google.cloud.exceptions import NotFound as GoogleCloudNotFound
import zstandard as zstd
from django.core.files.storage import default_storage
from django.utils import timezone

from api.models import (
    PersistentAgent,
    PersistentAgentCompletion,
    PersistentAgentError,
    PersistentAgentMessage,
    PersistentAgentStep,
)
from console.agent_audit.serializers import (
    serialize_completion,
    serialize_error,
    serialize_message,
    serialize_prompt_meta,
    serialize_tool_call,
)


logger = logging.getLogger(__name__)

DEFAULT_CHUNK_SIZE = 200
PROMPT_ARCHIVE_LOAD_WORKERS = 8
S3_MISSING_OBJECT_CODES = {"404", "NoSuchKey", "NotFound"}
DEFAULT_EXPORT_RANGE_KEY = "all"


class InvalidAuditExportRange(ValueError):
    pass


@dataclass(frozen=True)
class AuditExportRange:
    key: str
    label: str
    start: datetime | None
    end: datetime

    def as_payload(self) -> dict[str, str | None]:
        return {
            "key": self.key,
            "label": self.label,
            "start": _dt_to_iso(self.start),
            "end": _dt_to_iso(self.end),
        }


AUDIT_EXPORT_RANGE_OPTIONS: dict[str, tuple[str, timedelta | None]] = {
    "1h": ("Last hour", timedelta(hours=1)),
    "24h": ("Last 24 hours", timedelta(hours=24)),
    "7d": ("Last 7 days", timedelta(days=7)),
    "30d": ("Last 30 days", timedelta(days=30)),
    "all": ("Full audit", None),
}


def _dt_to_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    dt = dt.astimezone(dt_timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def build_audit_export_range(range_key: str | None, *, now: datetime | None = None) -> AuditExportRange:
    key = (range_key or DEFAULT_EXPORT_RANGE_KEY).strip() or DEFAULT_EXPORT_RANGE_KEY
    option = AUDIT_EXPORT_RANGE_OPTIONS.get(key)
    if option is None:
        valid_keys = ", ".join(AUDIT_EXPORT_RANGE_OPTIONS.keys())
        raise InvalidAuditExportRange(f"range must be one of: {valid_keys}")

    label, delta = option
    end = now or timezone.now()
    if timezone.is_naive(end):
        end = timezone.make_aware(end, timezone.get_current_timezone())
    start = end - delta if delta is not None else None
    return AuditExportRange(key=key, label=label, start=start, end=end)


def _apply_export_range(queryset, export_range: AuditExportRange, field_name: str):
    filters = {f"{field_name}__lte": export_range.end}
    if export_range.start is not None:
        filters[f"{field_name}__gte"] = export_range.start
    return queryset.filter(**filters)


def _load_prompt_archive_payload(archive) -> dict[str, Any] | None:
    storage_key = getattr(archive, "storage_key", "")
    if not storage_key:
        return None

    try:
        with default_storage.open(storage_key, "rb") as stored:
            dctx = zstd.ZstdDecompressor()
            payload_bytes = dctx.decompress(stored.read())
    except FileNotFoundError:
        return {"error": "missing_payload"}
    except GoogleCloudNotFound:
        return {"error": "missing_payload"}
    except ClientError as exc:
        error_code = str((exc.response.get("Error") or {}).get("Code") or "")
        if error_code in S3_MISSING_OBJECT_CODES:
            return {"error": "missing_payload"}
        logger.warning("Failed to read prompt archive payload for %s", getattr(archive, "id", None), exc_info=True)
        return {"error": "read_failed"}
    except (OSError, zstd.ZstdError):
        logger.warning("Failed to read prompt archive payload for %s", getattr(archive, "id", None), exc_info=True)
        return {"error": "read_failed"}

    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.warning("Failed to decode prompt archive payload for %s", getattr(archive, "id", None), exc_info=True)
        return {"error": "decode_failed"}

    return payload if isinstance(payload, dict) else {"raw_payload": payload}


def _prime_prompt_payload_cache(
    archives: Iterable[Any],
    prompt_payload_cache: dict[str, dict[str, Any] | None],
) -> None:
    missing_archives = []
    for archive in archives:
        archive_id = str(archive.id)
        if archive_id not in prompt_payload_cache:
            missing_archives.append(archive)

    if not missing_archives:
        return

    if len(missing_archives) == 1:
        archive = missing_archives[0]
        prompt_payload_cache[str(archive.id)] = _load_prompt_archive_payload(archive)
        return

    worker_count = min(PROMPT_ARCHIVE_LOAD_WORKERS, len(missing_archives))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        payloads = executor.map(_load_prompt_archive_payload, missing_archives)
        for archive, payload in zip(missing_archives, payloads):
            prompt_payload_cache[str(archive.id)] = payload


def _iter_completion_chunks(
    agent: PersistentAgent,
    *,
    export_range: AuditExportRange,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> Iterable[Sequence[PersistentAgentCompletion]]:
    chunk: list[PersistentAgentCompletion] = []
    queryset = _apply_export_range(
        PersistentAgentCompletion.objects.filter(agent=agent),
        export_range,
        "created_at",
    ).order_by("-created_at", "-id")
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
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> list[dict[str, Any]]:
    completion_ids = [completion.id for completion in completions]
    if not completion_ids:
        return []

    prompt_archive_by_completion_id: dict[str, Any] = {}
    tool_calls_by_completion_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    steps = (
        PersistentAgentStep.objects.filter(
            agent=agent,
            completion_id__in=completion_ids,
        )
        .select_related("tool_call", "llm_prompt_archive")
        .order_by("completion_id", "-created_at", "-id")
        .iterator(chunk_size=chunk_size)
    )
    for step in steps:
        completion_id = str(step.completion_id) if step.completion_id else None
        if completion_id is None:
            continue

        archive = getattr(step, "llm_prompt_archive", None)
        if archive is not None and completion_id not in prompt_archive_by_completion_id:
            prompt_archive_by_completion_id[completion_id] = archive

        tool_call = getattr(step, "tool_call", None)
        if tool_call is not None:
            tool_calls_by_completion_id[completion_id].append(serialize_tool_call(step))

    _prime_prompt_payload_cache(prompt_archive_by_completion_id.values(), prompt_payload_cache)

    serialized: list[dict[str, Any]] = []
    for completion in completions:
        completion_id = str(completion.id)
        archive = prompt_archive_by_completion_id.get(completion_id)
        prompt_meta = serialize_prompt_meta(archive) if archive is not None else None
        prompt_payload: dict[str, Any] | None = None
        if archive is not None:
            archive_id = str(archive.id)
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


def _iter_serialized_messages(
    agent: PersistentAgent,
    *,
    export_range: AuditExportRange,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> Iterable[dict[str, Any]]:
    queryset = (
        _apply_export_range(PersistentAgentMessage.objects.filter(owner_agent=agent), export_range, "timestamp")
        .select_related("from_endpoint", "to_endpoint", "conversation__peer_link", "peer_agent", "owner_agent")
        .prefetch_related("attachments__filespace_node")
        .order_by("-timestamp", "-seq")
    )
    for message in queryset.iterator(chunk_size=chunk_size):
        yield serialize_message(message)


def _iter_serialized_errors(
    agent: PersistentAgent,
    *,
    export_range: AuditExportRange,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> Iterable[dict[str, Any]]:
    queryset = _apply_export_range(
        PersistentAgentError.objects.filter(agent=agent),
        export_range,
        "created_at",
    ).order_by("-created_at", "-id")
    for error in queryset.iterator(chunk_size=chunk_size):
        yield serialize_error(error)


def _write_json_bytes(file_obj: BinaryIO, value: str) -> None:
    file_obj.write(value.encode("utf-8"))


def _write_json_value(file_obj: BinaryIO, value: Any) -> None:
    _write_json_bytes(file_obj, json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def write_agent_audit_export_json(
    agent: PersistentAgent,
    file_obj: BinaryIO,
    *,
    export_range: AuditExportRange | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> dict[str, Any]:
    """Write the audit export JSON to a binary file-like object incrementally."""
    resolved_range = export_range or build_audit_export_range(DEFAULT_EXPORT_RANGE_KEY)
    exported_at = _dt_to_iso(resolved_range.end)
    counts = {
        "completions": _apply_export_range(
            PersistentAgentCompletion.objects.filter(agent=agent),
            resolved_range,
            "created_at",
        ).count(),
        "messages": _apply_export_range(
            PersistentAgentMessage.objects.filter(owner_agent=agent),
            resolved_range,
            "timestamp",
        ).count(),
        "errors": _apply_export_range(
            PersistentAgentError.objects.filter(agent=agent),
            resolved_range,
            "created_at",
        ).count(),
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
    _write_json_bytes(file_obj, ',"range":')
    _write_json_value(file_obj, resolved_range.as_payload())
    _write_json_bytes(file_obj, ',"counts":')
    _write_json_value(file_obj, counts)

    _write_json_bytes(file_obj, ',"completions":[')
    first_completion = True
    prompt_payload_cache: dict[str, dict[str, Any] | None] = {}
    for completion_chunk in _iter_completion_chunks(agent, export_range=resolved_range, chunk_size=chunk_size):
        serialized_chunk = _serialize_completion_chunk(
            agent,
            completion_chunk,
            prompt_payload_cache=prompt_payload_cache,
            chunk_size=chunk_size,
        )
        for payload in serialized_chunk:
            if not first_completion:
                _write_json_bytes(file_obj, ",")
            _write_json_value(file_obj, payload)
            first_completion = False
    _write_json_bytes(file_obj, "]")

    _write_json_bytes(file_obj, ',"errors":[')
    first_error = True
    for error_payload in _iter_serialized_errors(agent, export_range=resolved_range, chunk_size=chunk_size):
        if not first_error:
            _write_json_bytes(file_obj, ",")
        _write_json_value(file_obj, error_payload)
        first_error = False
    _write_json_bytes(file_obj, "]")

    _write_json_bytes(file_obj, ',"messages":[')
    first_message = True
    for message_payload in _iter_serialized_messages(agent, export_range=resolved_range, chunk_size=chunk_size):
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
        "range": resolved_range.as_payload(),
    }
