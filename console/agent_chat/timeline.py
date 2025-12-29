from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone as dt_timezone
import re
from urllib.parse import urlencode
import uuid
from typing import Iterable, Literal, Sequence, Mapping

from django.contrib.humanize.templatetags.humanize import naturaltime
from django.db.models import Q
from django.utils import timezone
from django.utils.timesince import timesince
from django.urls import reverse

from bleach.sanitizer import ALLOWED_ATTRIBUTES as BLEACH_ALLOWED_ATTRIBUTES_BASE
from bleach.sanitizer import ALLOWED_PROTOCOLS as BLEACH_ALLOWED_PROTOCOLS_BASE
from bleach.sanitizer import ALLOWED_TAGS as BLEACH_ALLOWED_TAGS_BASE
from bleach.sanitizer import Cleaner

from api.agent.core.processing_flags import is_processing_queued
from api.models import (
    BrowserUseAgentTask,
    BrowserUseAgentTaskQuerySet,
    PersistentAgent,
    PersistentAgentMessage,
    PersistentAgentMessageAttachment,
    PersistentAgentStep,
    PersistentAgentToolCall,
    ToolFriendlyName,
)

DEFAULT_PAGE_SIZE = 40
MAX_PAGE_SIZE = 100
COLLAPSE_THRESHOLD = 3

HTML_TAG_PATTERN = re.compile(r"<([a-z][\w-]*)(?:\s[^>]*)?>", re.IGNORECASE)


def _build_html_cleaner() -> Cleaner:
    """Create a Bleach cleaner that preserves common email formatting."""

    allowed_tags = set(BLEACH_ALLOWED_TAGS_BASE).union(
        {
            "p",
            "br",
            "div",
            "span",
            "ul",
            "ol",
            "li",
            "pre",
        }
    )

    allowed_attributes = dict(BLEACH_ALLOWED_ATTRIBUTES_BASE)
    anchor_attrs = set(allowed_attributes.get("a", ())).union({"href", "title", "target", "rel"})
    allowed_attributes["a"] = sorted(anchor_attrs)
    allowed_attributes.setdefault("span", [])

    allowed_protocols = set(BLEACH_ALLOWED_PROTOCOLS_BASE).union({"mailto", "tel"})

    return Cleaner(
        tags=sorted(allowed_tags),
        attributes=allowed_attributes,
        protocols=allowed_protocols,
        strip=True,
    )


HTML_CLEANER = _build_html_cleaner()

TimelineDirection = Literal["initial", "older", "newer"]


@dataclass(slots=True)
class CursorPayload:
    value: int
    kind: Literal["message", "step"]
    identifier: str

    def encode(self) -> str:
        return f"{self.value}:{self.kind}:{self.identifier}"

    @staticmethod
    def decode(raw: str | None) -> "CursorPayload | None":
        if not raw:
            return None
        try:
            value_str, kind, identifier = raw.split(":", 2)
            return CursorPayload(value=int(value_str), kind=kind, identifier=identifier)
        except Exception:
            return None


@dataclass(slots=True)
class MessageEnvelope:
    sort_key: tuple[int, str, str]
    cursor: CursorPayload
    message: PersistentAgentMessage


@dataclass(slots=True)
class StepEnvelope:
    sort_key: tuple[int, str, str]
    cursor: CursorPayload
    step: PersistentAgentStep
    tool_call: PersistentAgentToolCall


@dataclass(slots=True)
class ProcessingSnapshot:
    active: bool
    web_tasks: list[dict]


@dataclass(slots=True)
class TimelineWindow:
    events: list[dict]
    oldest_cursor: str | None
    newest_cursor: str | None
    has_more_older: bool
    has_more_newer: bool
    processing_snapshot: ProcessingSnapshot

    @property
    def processing_active(self) -> bool:
        return self.processing_snapshot.active


def _looks_like_html(body: str) -> bool:
    return bool(HTML_TAG_PATTERN.search(body))


def _humanize_body(body: str) -> str:
    body = body or ""
    if _looks_like_html(body):
        return HTML_CLEANER.clean(body)
    return ""


def _format_timestamp(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt.astimezone(dt_timezone.utc).isoformat().replace("+00:00", "Z")


def _relative_timestamp(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    now = timezone.now()
    if dt > now:
        return "moments ago"
    try:
        # `naturaltime` may return a lazy translation object; convert to plain str for serialization.
        humanized = naturaltime(dt)
    except Exception:
        # Fallback to timesince when humanize isn't available
        return f"{timesince(dt, now)} ago"
    return str(humanized)


def _microsecond_epoch(dt: datetime) -> int:
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    dt_utc = dt.astimezone(dt_timezone.utc)
    return int(dt_utc.timestamp() * 1_000_000)


def _load_tool_label_map(tool_names: Iterable[str | None]) -> dict[str, str]:
    """Fetch display labels for the provided tool names in a single query."""
    unique_names = {name for name in tool_names if name}
    if not unique_names:
        return {}
    return {
        tool_name: display_name
        for tool_name, display_name in ToolFriendlyName.objects.filter(tool_name__in=unique_names)
        .values_list("tool_name", "display_name")
    }


def _friendly_tool_label(tool_name: str | None, labels: Mapping[str, str] | None = None) -> str:
    if not tool_name:
        return "Tool call"
    if labels and tool_name in labels:
        return labels[tool_name]
    return tool_name.replace("_", " ").title()


TOOL_ICON_LIBRARY: dict[str, dict[str, object]] = {
    "email": {
        "iconPaths": [
            "M3 8l9 6 9-6",
            "M5 5h14a2 2 0 012 2v10a2 2 0 01-2 2H5a2 2 0 01-2-2V7a2 2 0 012-2z",
        ],
        "iconBg": "bg-indigo-50",
        "iconColor": "text-indigo-600",
    },
    "slack": {
        "iconPaths": [
            "M7 9h4a2 2 0 002-2V5a2 2 0 10-4 0v2H7a2 2 0 100 4z",
            "M9 17v-4a2 2 0 00-2-2H5a2 2 0 100 4h2v2a2 2 0 104 0z",
            "M15 7v2h2a2 2 0 100-4h-2V5a2 2 0 10-4 0v2a2 2 0 002 2z",
            "M15 15h-2v2a2 2 0 104 0v-2a2 2 0 00-2-2z",
        ],
        "iconBg": "bg-fuchsia-50",
        "iconColor": "text-fuchsia-600",
    },
    "browser": {
        "iconPaths": [
            "M4 6h16a2 2 0 012 2v8a2 2 0 01-2 2H4a2 2 0 01-2-2V8a2 2 0 012-2z",
            "M2 10h20",
        ],
        "iconBg": "bg-emerald-50",
        "iconColor": "text-emerald-600",
    },
    "database": {
        "iconPaths": [
            "M5 7c0-2.21 3.582-4 8-4s8 1.79 8 4-3.582 4-8 4-8-1.79-8-4z",
            "M5 12c0 2.21 3.582 4 8 4s8-1.79 8-4",
            "M5 17c0 2.21 3.582 4 8 4s8-1.79 8-4",
        ],
        "iconBg": "bg-sky-50",
        "iconColor": "text-sky-600",
    },
    "doc": {
        "iconPaths": [
            "M7 4h7l5 5v11a2 2 0 01-2 2H7a2 2 0 01-2-2V6a2 2 0 012-2z",
            "M14 3v6h6",
        ],
        "iconBg": "bg-amber-50",
        "iconColor": "text-amber-600",
    },
    "default": {
        "iconPaths": [
            "M4 6h16",
            "M4 12h16",
            "M4 18h16",
        ],
        "iconBg": "bg-slate-100",
        "iconColor": "text-slate-600",
    },
}


def _tool_icon_for(name: str | None) -> dict[str, object]:
    if not name:
        return TOOL_ICON_LIBRARY["default"].copy()
    lower = name.lower()
    if "email" in lower or "mail" in lower:
        key = "email"
    elif "slack" in lower or "discord" in lower:
        key = "slack"
    elif any(word in lower for word in ("http", "browser", "crawl", "fetch")):
        key = "browser"
    elif any(word in lower for word in ("sql", "database", "db")):
        key = "database"
    elif any(word in lower for word in ("doc", "sheet", "drive", "notion")):
        key = "doc"
    else:
        key = "default"
    data = TOOL_ICON_LIBRARY[key].copy()
    data.setdefault("iconPaths", TOOL_ICON_LIBRARY["default"]["iconPaths"])
    return data


def _serialize_attachment(att: PersistentAgentMessageAttachment, agent_id: uuid.UUID | None) -> dict:
    size_label = None
    try:
        from django.template.defaultfilters import filesizeformat

        size_label = filesizeformat(att.file_size)
    except Exception:
        size_label = None
    filespace_path = None
    filespace_node_id = None
    download_url = None
    node = getattr(att, "filespace_node", None)
    if node:
        filespace_path = node.path
        filespace_node_id = str(node.id)
    if (filespace_path or filespace_node_id) and agent_id:
        query = urlencode({"node_id": filespace_node_id} if filespace_node_id else {"path": filespace_path})
        download_url = f"{reverse('console_agent_fs_download', kwargs={'agent_id': agent_id})}?{query}"
    return {
        "id": str(att.id),
        "filename": att.filename,
        "url": att.file.url if att.file else "",
        "downloadUrl": download_url,
        "filespacePath": filespace_path,
        "filespaceNodeId": filespace_node_id,
        "fileSizeLabel": size_label,
    }


def _serialize_message(env: MessageEnvelope) -> dict:
    message = env.message
    timestamp = message.timestamp
    channel = "web"
    if message.conversation_id:
        channel = message.conversation.channel
    elif message.from_endpoint_id:
        channel = message.from_endpoint.channel
    attachments = [_serialize_attachment(att, message.owner_agent_id) for att in message.attachments.all()]
    conversation = message.conversation
    peer_link_id: str | None = None
    is_peer_dm = False
    if conversation and conversation.is_peer_dm:
        is_peer_dm = True
        if conversation.peer_link_id:
            peer_link_id = str(conversation.peer_link_id)

    peer_payload: dict | None = None
    if message.peer_agent_id:
        peer_agent = getattr(message, "peer_agent", None)
        peer_payload = {
            "id": str(message.peer_agent_id),
            "name": getattr(peer_agent, "name", None),
        }
        is_peer_dm = True

    self_agent = getattr(message, "owner_agent", None)
    self_agent_name = getattr(self_agent, "name", None)

    return {
        "kind": "message",
        "cursor": env.cursor.encode(),
        "timestamp": _format_timestamp(timestamp),
        "message": {
            "id": str(message.id),
            "cursor": env.cursor.encode(),
            "bodyHtml": _humanize_body(message.body or ""),
            "bodyText": message.body or "",
            "isOutbound": bool(message.is_outbound),
            "channel": channel,
            "attachments": attachments,
            "timestamp": _format_timestamp(timestamp),
            "relativeTimestamp": _relative_timestamp(timestamp),
            "isPeer": is_peer_dm,
            "peerAgent": peer_payload,
            "peerLinkId": peer_link_id,
            "selfAgentName": self_agent_name,
        },
    }


def _serialize_step_entry(env: StepEnvelope, labels: Mapping[str, str]) -> dict:
    step = env.step
    tool_call = env.tool_call
    tool_name = tool_call.tool_name or ""
    meta = _tool_icon_for(tool_name)
    meta["label"] = _friendly_tool_label(tool_name, labels)
    return {
        "id": str(step.id),
        "cursor": env.cursor.encode(),
        "timestamp": _format_timestamp(step.created_at),
        "caption": step.description or meta["label"],
        "toolName": tool_name,
        "meta": meta,
        "parameters": tool_call.tool_params,
        "result": tool_call.result,
    }


def _build_cluster(entries: Sequence[StepEnvelope], labels: Mapping[str, str]) -> dict:
    serialized_entries = [_serialize_step_entry(env, labels) for env in entries]
    earliest = entries[0]
    latest = entries[-1]
    return {
        "kind": "steps",
        "cursor": earliest.cursor.encode(),
        "entryCount": len(serialized_entries),
        "collapsible": len(serialized_entries) >= COLLAPSE_THRESHOLD,
        "collapseThreshold": COLLAPSE_THRESHOLD,
        "earliestTimestamp": serialized_entries[0]["timestamp"],
        "latestTimestamp": serialized_entries[-1]["timestamp"],
        "entries": serialized_entries,
    }


def _messages_queryset(agent: PersistentAgent, direction: TimelineDirection, cursor: CursorPayload | None) -> Sequence[PersistentAgentMessage]:
    limit = MAX_PAGE_SIZE * 3
    qs = (
        PersistentAgentMessage.objects.filter(owner_agent=agent)
        .select_related(
            "from_endpoint",
            "to_endpoint",
            "conversation__peer_link",
            "peer_agent",
            "owner_agent",
        )
        .prefetch_related("attachments__filespace_node")
        .order_by("-timestamp", "-seq")
    )
    if direction == "older" and cursor is not None:
        qs = qs.filter(timestamp__lte=_dt_from_cursor(cursor))
    elif direction == "newer" and cursor is not None:
        # For "newer", we need messages AFTER the cursor
        # This includes: timestamp > cursor_time OR (timestamp == cursor_time AND seq > cursor_seq)
        dt = _dt_from_cursor(cursor)
        if cursor.kind == "message":
            qs = qs.filter(
                Q(timestamp__gt=dt) | Q(timestamp=dt, seq__gt=cursor.identifier)
            )
        else:
            qs = qs.filter(timestamp__gt=dt)
    return list(qs[:limit])


def _steps_queryset(agent: PersistentAgent, direction: TimelineDirection, cursor: CursorPayload | None) -> Sequence[PersistentAgentStep]:
    limit = MAX_PAGE_SIZE * 3
    qs = (
        PersistentAgentStep.objects.filter(agent=agent, tool_call__isnull=False)
        .select_related("tool_call")
        .order_by("-created_at", "-id")
    )
    if direction == "older" and cursor is not None:
        qs = qs.filter(created_at__lte=_dt_from_cursor(cursor))
    elif direction == "newer" and cursor is not None:
        # For "newer", we need events AFTER the cursor
        # This includes: created_at > cursor_time OR (created_at == cursor_time AND id > cursor_id)
        dt = _dt_from_cursor(cursor)
        if cursor.kind == "step":
            try:
                cursor_uuid = uuid.UUID(cursor.identifier)
                qs = qs.filter(
                    Q(created_at__gt=dt) | Q(created_at=dt, id__gt=cursor_uuid)
                )
            except Exception:
                qs = qs.filter(created_at__gt=dt)
        else:
            qs = qs.filter(created_at__gt=dt)
    return list(qs[:limit])


def _dt_from_cursor(cursor: CursorPayload) -> datetime:
    micros = cursor.value
    return datetime.fromtimestamp(micros / 1_000_000, tz=dt_timezone.utc)


def _envelop_messages(messages: Iterable[PersistentAgentMessage]) -> list[MessageEnvelope]:
    envelopes: list[MessageEnvelope] = []
    for message in messages:
        sort_value = _microsecond_epoch(message.timestamp)
        cursor = CursorPayload(value=sort_value, kind="message", identifier=message.seq)
        envelopes.append(
            MessageEnvelope(
                sort_key=(sort_value, "message", message.seq),
                cursor=cursor,
                message=message,
            )
        )
    return envelopes


def _envelop_steps(steps: Iterable[PersistentAgentStep]) -> list[StepEnvelope]:
    envelopes: list[StepEnvelope] = []
    for step in steps:
        if not hasattr(step, "tool_call") or step.tool_call is None:
            continue
        sort_value = _microsecond_epoch(step.created_at)
        cursor = CursorPayload(value=sort_value, kind="step", identifier=str(step.id))
        envelopes.append(
            StepEnvelope(
                sort_key=(sort_value, "step", str(step.id)),
                cursor=cursor,
                step=step,
                tool_call=step.tool_call,
            )
        )
    return envelopes


def _filter_by_direction(
    envelopes: Sequence[MessageEnvelope | StepEnvelope],
    direction: TimelineDirection,
    cursor: CursorPayload | None,
) -> list[MessageEnvelope | StepEnvelope]:
    if not cursor or direction == "initial":
        return list(envelopes)
    pivot = (cursor.value, cursor.kind, cursor.identifier)
    filtered: list[MessageEnvelope | StepEnvelope] = []
    for env in envelopes:
        key = env.sort_key
        if direction == "older" and key < pivot:
            filtered.append(env)
        elif direction == "newer" and key > pivot:
            filtered.append(env)
    return filtered


def _truncate_for_direction(
    envelopes: list[MessageEnvelope | StepEnvelope],
    direction: TimelineDirection,
    limit: int,
) -> list[MessageEnvelope | StepEnvelope]:
    if not envelopes:
        return []
    if direction == "older":
        return envelopes[-limit:]
    if direction == "newer":
        return envelopes[:limit]
    # initial snapshot -> latest `limit` events
    return envelopes[-limit:]


def _has_more_before(agent: PersistentAgent, cursor: CursorPayload | None) -> bool:
    if cursor is None:
        return False
    dt = _dt_from_cursor(cursor)
    message_exists = PersistentAgentMessage.objects.filter(
        owner_agent=agent,
        timestamp__lt=dt,
    ).exists()
    if cursor.kind == "message":
        message_exists = message_exists or PersistentAgentMessage.objects.filter(
            owner_agent=agent,
            timestamp=dt,
            seq__lt=cursor.identifier,
        ).exists()
    step_exists = PersistentAgentStep.objects.filter(
        agent=agent,
        tool_call__isnull=False,
        created_at__lt=dt,
    ).exists()
    if cursor.kind == "step":
        try:
            uuid_identifier = uuid.UUID(cursor.identifier)
            step_exists = step_exists or PersistentAgentStep.objects.filter(
                agent=agent,
                tool_call__isnull=False,
                created_at=dt,
                id__lt=uuid_identifier,
            ).exists()
        except Exception:
            pass
    return message_exists or step_exists


def _has_more_after(agent: PersistentAgent, cursor: CursorPayload | None) -> bool:
    if cursor is None:
        return False
    dt = _dt_from_cursor(cursor)

    message_exists = PersistentAgentMessage.objects.filter(
        owner_agent=agent,
        timestamp__gt=dt,
    ).exists()
    if cursor.kind == "message":
        message_exists = message_exists or PersistentAgentMessage.objects.filter(
            owner_agent=agent,
            timestamp=dt,
            seq__gt=cursor.identifier,
        ).exists()

    step_exists = PersistentAgentStep.objects.filter(
        agent=agent,
        tool_call__isnull=False,
        created_at__gt=dt,
    ).exists()
    if cursor.kind == "step":
        try:
            uuid_identifier = uuid.UUID(cursor.identifier)
            step_exists = step_exists or PersistentAgentStep.objects.filter(
                agent=agent,
                tool_call__isnull=False,
                created_at=dt,
                id__gt=uuid_identifier,
            ).exists()
        except Exception:
            pass

    return message_exists or step_exists


WEB_TASK_ACTIVE_STATUSES = (
    BrowserUseAgentTask.StatusChoices.PENDING,
    BrowserUseAgentTask.StatusChoices.IN_PROGRESS,
)


def _build_web_task_payload(task: BrowserUseAgentTask, *, now: datetime | None = None) -> dict:
    """Serialize an active browser task for frontend consumption."""

    if now is None:
        now = timezone.now()

    started_at = task.created_at
    updated_at = task.updated_at
    elapsed_seconds: float | None = None
    if started_at:
        elapsed_seconds = max((now - started_at).total_seconds(), 0.0)

    prompt = (task.prompt or "").strip()
    prompt_preview = prompt if len(prompt) <= 160 else f"{prompt[:157].rstrip()}…"

    return {
        "id": str(task.id),
        "status": task.status,
        "statusLabel": task.get_status_display(),
        "prompt": prompt,
        "promptPreview": prompt_preview,
        "startedAt": _format_timestamp(started_at),
        "updatedAt": _format_timestamp(updated_at),
        "elapsedSeconds": elapsed_seconds,
    }


def build_processing_snapshot(agent: PersistentAgent) -> ProcessingSnapshot:
    """Compute current processing activity and active web tasks for an agent."""

    # Check if the agent event processing lock is held
    # Note: Redlock prefixes keys with "redlock:" internally
    from config.redis_client import get_redis_client

    lock_key = f"redlock:agent-event-processing:{agent.id}"
    lock_active = False
    queued_flag = False
    try:
        redis_client = get_redis_client()
        lock_active = bool(redis_client.exists(lock_key))
        queued_flag = is_processing_queued(agent.id, client=redis_client)
    except Exception:
        lock_active = False
        queued_flag = False

    web_tasks: list[dict] = []
    if getattr(agent, "browser_use_agent_id", None):
        task_qs: BrowserUseAgentTaskQuerySet = BrowserUseAgentTask.objects
        active_tasks = task_qs.filter(
            agent=agent.browser_use_agent,
            status__in=WEB_TASK_ACTIVE_STATUSES,
            is_deleted=False,
        ).order_by("created_at")
        now = timezone.now()
        web_tasks = [_build_web_task_payload(task, now=now) for task in active_tasks]

    active = bool(lock_active or queued_flag or web_tasks)
    return ProcessingSnapshot(active=active, web_tasks=web_tasks)


def serialize_processing_snapshot(snapshot: ProcessingSnapshot) -> dict:
    return {
        "active": snapshot.active,
        "webTasks": snapshot.web_tasks,
    }


def fetch_timeline_window(
    agent: PersistentAgent,
    *,
    cursor: str | None = None,
    direction: TimelineDirection = "initial",
    limit: int = DEFAULT_PAGE_SIZE,
) -> TimelineWindow:
    limit = max(1, min(limit, MAX_PAGE_SIZE))
    cursor_payload = CursorPayload.decode(cursor)

    message_envelopes = _envelop_messages(_messages_queryset(agent, direction, cursor_payload))
    step_envelopes = _envelop_steps(_steps_queryset(agent, direction, cursor_payload))

    merged: list[MessageEnvelope | StepEnvelope] = sorted(
        [*message_envelopes, *step_envelopes],
        key=lambda env: env.sort_key,
    )

    filtered = _filter_by_direction(merged, direction, cursor_payload)
    truncated = _truncate_for_direction(filtered, direction, limit)

    # Ensure chronological order for presentation
    truncated.sort(key=lambda env: env.sort_key)

    tool_label_map = _load_tool_label_map(
        env.tool_call.tool_name for env in truncated if isinstance(env, StepEnvelope)
    )

    timeline_events: list[dict] = []
    cluster_buffer: list[StepEnvelope] = []
    for env in truncated:
        if isinstance(env, StepEnvelope):
            cluster_buffer.append(env)
            continue
        if cluster_buffer:
            timeline_events.append(_build_cluster(cluster_buffer, tool_label_map))
            cluster_buffer = []
        timeline_events.append(_serialize_message(env))
    if cluster_buffer:
        timeline_events.append(_build_cluster(cluster_buffer, tool_label_map))

    oldest_cursor = truncated[0].cursor if truncated else None
    newest_cursor = truncated[-1].cursor if truncated else None

    has_more_older = _has_more_before(agent, oldest_cursor)
    has_more_newer = False if direction == "initial" else _has_more_after(agent, newest_cursor)

    processing_snapshot = build_processing_snapshot(agent)

    return TimelineWindow(
        events=timeline_events,
        oldest_cursor=oldest_cursor.encode() if oldest_cursor else None,
        newest_cursor=newest_cursor.encode() if newest_cursor else None,
        has_more_older=has_more_older,
        has_more_newer=has_more_newer,
        processing_snapshot=processing_snapshot,
    )


def serialize_message_event(message: PersistentAgentMessage) -> dict:
    envelope = _envelop_messages([message])[0]
    return _serialize_message(envelope)


def serialize_step_entry(step: PersistentAgentStep) -> dict:
    envelopes = _envelop_steps([step])
    if not envelopes:
        raise ValueError("Step does not include a tool call")
    label_map = _load_tool_label_map(
        [envelopes[0].tool_call.tool_name] if envelopes[0].tool_call else []
    )
    return _serialize_step_entry(envelopes[0], label_map)


def compute_processing_status(agent: PersistentAgent) -> bool:
    """Expose processing state computation for external callers."""
    return build_processing_snapshot(agent).active


def build_tool_cluster_from_steps(steps: Sequence[PersistentAgentStep]) -> dict:
    envelopes = _envelop_steps(steps)
    if not envelopes:
        raise ValueError("No tool calls available")
    label_map = _load_tool_label_map(
        env.tool_call.tool_name for env in envelopes if env.tool_call
    )
    return _build_cluster(envelopes, label_map)
