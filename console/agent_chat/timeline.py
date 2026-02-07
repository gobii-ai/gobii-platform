from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as dt_timezone
import re
from urllib.parse import urlencode
import uuid
from typing import Iterable, Literal, Sequence, Mapping

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.humanize.templatetags.humanize import naturaltime
from django.db.models import Q
from django.utils import timezone
from django.utils.timesince import timesince
from django.urls import reverse

from bleach.sanitizer import ALLOWED_ATTRIBUTES as BLEACH_ALLOWED_ATTRIBUTES_BASE
from bleach.sanitizer import ALLOWED_PROTOCOLS as BLEACH_ALLOWED_PROTOCOLS_BASE
from bleach.sanitizer import ALLOWED_TAGS as BLEACH_ALLOWED_TAGS_BASE
from bleach.sanitizer import Cleaner

from api.agent.core.processing_flags import get_processing_heartbeat, is_processing_queued
from api.agent.core.schedule_parser import ScheduleParser
from api.agent.comms.email_content import convert_body_to_html_and_plaintext
from api.models import (
    BrowserUseAgentTask,
    BrowserUseAgentTaskQuerySet,
    PersistentAgent,
    PersistentAgentKanbanEvent,
    PersistentAgentCompletion,
    PersistentAgentMessage,
    PersistentAgentMessageAttachment,
    PersistentAgentStep,
    PersistentAgentToolCall,
    ToolFriendlyName,
    parse_web_user_address,
)

from .kanban_events import ensure_kanban_baseline_event

DEFAULT_PAGE_SIZE = 40
MAX_PAGE_SIZE = 100
COLLAPSE_THRESHOLD = 3
THINKING_COMPLETION_TYPES = (PersistentAgentCompletion.CompletionType.ORCHESTRATOR,)
HIDE_IN_CHAT_PAYLOAD_KEY = "hide_in_chat"


def is_chat_hidden_message(message: PersistentAgentMessage) -> bool:
    payload = message.raw_payload or {}
    return bool(payload.get(HIDE_IN_CHAT_PAYLOAD_KEY))


def _message_queryset(agent: PersistentAgent):
    hidden_key = f"raw_payload__{HIDE_IN_CHAT_PAYLOAD_KEY}"
    return PersistentAgentMessage.objects.filter(owner_agent=agent).filter(
        Q(**{hidden_key: False}) | Q(**{f"{hidden_key}__isnull": True}),
    )

def _build_html_cleaner() -> Cleaner:
    """Create a Bleach cleaner that preserves common email formatting."""

    allowed_tags = set(BLEACH_ALLOWED_TAGS_BASE).union(
        {
            "p",
            "br",
            "div",
            "span",
            "img",
            "ul",
            "ol",
            "li",
            "pre",
            # Table tags for rich data display
            "table",
            "thead",
            "tbody",
            "tfoot",
            "tr",
            "th",
            "td",
            "caption",
        }
    )

    allowed_attributes = dict(BLEACH_ALLOWED_ATTRIBUTES_BASE)
    anchor_attrs = set(allowed_attributes.get("a", ())).union({"href", "title", "target", "rel"})
    allowed_attributes["a"] = sorted(anchor_attrs)
    allowed_attributes.setdefault("span", [])
    allowed_attributes["img"] = ["src", "alt", "width", "height"]
    # Table cell attributes
    allowed_attributes["th"] = ["colspan", "rowspan", "scope", "headers"]
    allowed_attributes["td"] = ["colspan", "rowspan", "headers"]
    allowed_attributes["table"] = ["style"]

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
    kind: Literal["message", "step", "thinking", "kanban"]
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
class ThinkingEnvelope:
    sort_key: tuple[int, str, str]
    cursor: CursorPayload
    completion: PersistentAgentCompletion
    reasoning: str


@dataclass(slots=True)
class KanbanEnvelope:
    sort_key: tuple[int, str, str]
    cursor: CursorPayload
    event: PersistentAgentKanbanEvent


@dataclass(slots=True)
class ProcessingSnapshot:
    active: bool
    web_tasks: list[dict]
    next_scheduled_at: datetime | None = None


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


def _humanize_body(body: str, channel: str | None = None) -> str:
    """Convert message body to sanitized HTML for display.

    For email channel: Converts markdown to HTML for email client rendering.
    For all other channels: Returns empty string - the frontend's markdown
    renderer handles the content, including inline HTML like <br> tags.
    """
    body = body or ""
    if channel and channel.lower() == "email":
        try:
            html_snippet, _ = convert_body_to_html_and_plaintext(body, emit_logs=False)
        except Exception:
            html_snippet = body
        return HTML_CLEANER.clean(html_snippet) if html_snippet else ""
    # Non-email channels: Let frontend render markdown (handles <br> naturally)
    return ""


def _build_user_display_name(user) -> str | None:
    full_name = ""
    if hasattr(user, "get_full_name"):
        full_name = (user.get_full_name() or "").strip()
    if full_name:
        return full_name
    email = (getattr(user, "email", "") or "").strip()
    if email:
        return email
    username = (getattr(user, "username", "") or "").strip()
    if username:
        return username
    return None


def _build_web_user_lookup(messages: Iterable[PersistentAgentMessage]) -> dict[int, str | None]:
    user_ids: set[int] = set()
    for message in messages:
        channel = "web"
        if message.conversation_id:
            channel = message.conversation.channel
        elif message.from_endpoint_id:
            channel = message.from_endpoint.channel
        if channel.lower() != "web":
            continue
        from_endpoint = message.from_endpoint
        if not from_endpoint:
            continue
        user_id, agent_id = parse_web_user_address(from_endpoint.address)
        if user_id is None:
            continue
        if agent_id and message.owner_agent_id and str(message.owner_agent_id) != agent_id:
            continue
        user_ids.add(user_id)

    if not user_ids:
        return {}

    user_model = get_user_model()
    users = user_model.objects.filter(id__in=user_ids).only(
        "id",
        "email",
        "username",
        "first_name",
        "last_name",
    )
    return {user.id: _build_user_display_name(user) for user in users}


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


def _serialize_message(env: MessageEnvelope, user_lookup: Mapping[int, str | None] | None = None) -> dict:
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
    sender_user_id: int | None = None
    sender_name: str | None = None
    sender_address = message.from_endpoint.address if message.from_endpoint_id else None
    if channel.lower() == "web" and sender_address:
        user_id, agent_id = parse_web_user_address(sender_address)
        if user_id is not None and (not agent_id or not message.owner_agent_id or str(message.owner_agent_id) == agent_id):
            sender_user_id = user_id
            if user_lookup is not None and user_id in user_lookup:
                sender_name = user_lookup[user_id]
            else:
                user_model = get_user_model()
                user = user_model.objects.filter(id=user_id).only(
                    "id",
                    "email",
                    "username",
                    "first_name",
                    "last_name",
                ).first()
                if user:
                    sender_name = _build_user_display_name(user)
    if not sender_name:
        sender_name = (conversation.display_name or "").strip() if conversation else ""
        if not sender_name:
            sender_name = sender_address

    return {
        "kind": "message",
        "cursor": env.cursor.encode(),
        "timestamp": _format_timestamp(timestamp),
        "message": {
            "id": str(message.id),
            "cursor": env.cursor.encode(),
            "bodyHtml": _humanize_body(message.body or "", channel),
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
            "senderUserId": sender_user_id,
            "senderName": sender_name,
            "senderAddress": sender_address,
        },
    }


def _serialize_thinking(env: ThinkingEnvelope) -> dict:
    completion = env.completion
    return {
        "kind": "thinking",
        "cursor": env.cursor.encode(),
        "timestamp": _format_timestamp(completion.created_at),
        "reasoning": env.reasoning,
        "completionId": str(completion.id),
    }


_FILE_REF_RE = re.compile(r"^\$\[(.+)\]$")
_INLINE_IMG_SRC_RE = re.compile(r"<img[^>]+src=['\"]([^'\"]+)['\"]", re.IGNORECASE)
_MARKDOWN_IMG_RE = re.compile(r"!\[[^\]]*]\(([^)]+)\)")


def _resolve_tool_image_candidate(
    tool_call: PersistentAgentToolCall,
    candidate: str | Mapping[str, object] | None,
) -> str | None:
    if isinstance(candidate, Mapping):
        raw_candidate = candidate.get("url") or candidate.get("image_url")
        candidate = str(raw_candidate).strip() if raw_candidate is not None else None
    if not isinstance(candidate, str):
        return None
    normalized = candidate.strip()
    if not normalized:
        return None

    inline_match = _INLINE_IMG_SRC_RE.search(normalized)
    if inline_match:
        normalized = inline_match.group(1).strip()
    else:
        markdown_match = _MARKDOWN_IMG_RE.search(normalized)
        if markdown_match:
            normalized = markdown_match.group(1).strip()

    match = _FILE_REF_RE.match(normalized)
    file_path = match.group(1).strip() if match else None
    if not file_path and normalized.startswith("/"):
        file_path = normalized

    if file_path:
        agent_id = tool_call.step.agent_id
        query = urlencode({"path": file_path})
        return f"{reverse('console_agent_fs_download', kwargs={'agent_id': agent_id})}?{query}"

    if normalized.startswith(("http://", "https://", "data:image/")):
        return normalized

    return None


def _extract_tool_image_url(tool_call: PersistentAgentToolCall) -> str | None:
    """Resolve chart/image tool outputs to an image URL suitable for timeline previews."""
    import json as _json

    tool_name = (tool_call.tool_name or "").lower()
    if tool_name not in {"create_chart", "create_image"}:
        return None
    if (getattr(tool_call, "status", None) or "complete") != "complete":
        return None
    raw = tool_call.result
    if not raw:
        return None
    try:
        result = _json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return None
    if not isinstance(result, dict) or result.get("status") == "error":
        return None
    if tool_name == "create_chart":
        candidate_keys = ("file", "chart_url", "image_url", "url", "inline_html", "inline")
    else:
        candidate_keys = ("file", "image_url", "url", "inline_html", "inline")

    for key in candidate_keys:
        resolved = _resolve_tool_image_candidate(tool_call, result.get(key))
        if resolved:
            return resolved
    return None


def _serialize_step_entry(env: StepEnvelope, labels: Mapping[str, str]) -> dict:
    step = env.step
    tool_call = env.tool_call
    tool_name = tool_call.tool_name or ""
    meta = _tool_icon_for(tool_name)
    meta["label"] = _friendly_tool_label(tool_name, labels)
    status = getattr(tool_call, "status", None) or "complete"
    entry: dict = {
        "id": str(step.id),
        "cursor": env.cursor.encode(),
        "timestamp": _format_timestamp(step.created_at),
        "caption": step.description or meta["label"],
        "toolName": tool_name,
        "meta": meta,
        "parameters": tool_call.tool_params,
        "result": tool_call.result,
        "status": status,
    }
    preview_image_url = _extract_tool_image_url(tool_call)
    if preview_image_url:
        lowered_tool_name = tool_name.lower()
        if lowered_tool_name == "create_chart":
            entry["chartImageUrl"] = preview_image_url
        elif lowered_tool_name == "create_image":
            entry["createImageUrl"] = preview_image_url
    return entry


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
        _message_queryset(agent)
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


def _thinking_queryset(
    agent: PersistentAgent,
    direction: TimelineDirection,
    cursor: CursorPayload | None,
) -> Sequence[PersistentAgentCompletion]:
    limit = MAX_PAGE_SIZE * 3
    qs = (
        PersistentAgentCompletion.objects.filter(
            agent=agent,
            completion_type__in=THINKING_COMPLETION_TYPES,
        )
        .exclude(thinking_content__isnull=True)
        .exclude(thinking_content__exact="")
        .order_by("-created_at", "-id")
    )
    if direction == "older" and cursor is not None:
        qs = qs.filter(created_at__lte=_dt_from_cursor(cursor))
    elif direction == "newer" and cursor is not None:
        dt = _dt_from_cursor(cursor)
        if cursor.kind == "thinking":
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


def _kanban_queryset(
    agent: PersistentAgent,
    direction: TimelineDirection,
    cursor: CursorPayload | None,
) -> Sequence[PersistentAgentKanbanEvent]:
    limit = MAX_PAGE_SIZE * 3
    qs = (
        PersistentAgentKanbanEvent.objects.filter(agent=agent)
        .prefetch_related("changes", "titles")
        .order_by("-cursor_value", "-cursor_identifier")
    )
    if direction == "older" and cursor is not None:
        qs = qs.filter(cursor_value__lte=cursor.value)
    elif direction == "newer" and cursor is not None:
        if cursor.kind == "kanban":
            try:
                cursor_uuid = uuid.UUID(cursor.identifier)
                qs = qs.filter(
                    Q(cursor_value__gt=cursor.value)
                    | Q(cursor_value=cursor.value, cursor_identifier__gt=cursor_uuid)
                )
            except Exception:
                qs = qs.filter(cursor_value__gt=cursor.value)
        else:
            qs = qs.filter(cursor_value__gt=cursor.value)
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


def _envelop_thinking(completions: Iterable[PersistentAgentCompletion]) -> list[ThinkingEnvelope]:
    envelopes: list[ThinkingEnvelope] = []
    for completion in completions:
        if completion.completion_type not in THINKING_COMPLETION_TYPES:
            continue
        reasoning = (completion.thinking_content or "").strip()
        if not reasoning:
            continue
        sort_value = _microsecond_epoch(completion.created_at)
        cursor = CursorPayload(value=sort_value, kind="thinking", identifier=str(completion.id))
        envelopes.append(
            ThinkingEnvelope(
                sort_key=(sort_value, "thinking", str(completion.id)),
                cursor=cursor,
                completion=completion,
                reasoning=reasoning,
            )
        )
    return envelopes


def _envelop_kanban_events(events: Iterable[PersistentAgentKanbanEvent]) -> list[KanbanEnvelope]:
    envelopes: list[KanbanEnvelope] = []
    for event in events:
        sort_value = event.cursor_value
        cursor = CursorPayload(
            value=sort_value,
            kind="kanban",
            identifier=str(event.cursor_identifier),
        )
        envelopes.append(
            KanbanEnvelope(
                sort_key=(sort_value, "kanban", str(event.cursor_identifier)),
                cursor=cursor,
                event=event,
            )
        )
    return envelopes


def _filter_by_direction(
    envelopes: Sequence[MessageEnvelope | StepEnvelope | ThinkingEnvelope | KanbanEnvelope],
    direction: TimelineDirection,
    cursor: CursorPayload | None,
) -> list[MessageEnvelope | StepEnvelope | ThinkingEnvelope | KanbanEnvelope]:
    if not cursor or direction == "initial":
        return list(envelopes)
    pivot = (cursor.value, cursor.kind, cursor.identifier)
    filtered: list[MessageEnvelope | StepEnvelope | ThinkingEnvelope | KanbanEnvelope] = []
    for env in envelopes:
        key = env.sort_key
        if direction == "older" and key < pivot:
            filtered.append(env)
        elif direction == "newer" and key > pivot:
            filtered.append(env)
    return filtered


def _truncate_for_direction(
    envelopes: list[MessageEnvelope | StepEnvelope | ThinkingEnvelope | KanbanEnvelope],
    direction: TimelineDirection,
    limit: int,
) -> list[MessageEnvelope | StepEnvelope | ThinkingEnvelope | KanbanEnvelope]:
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
    message_qs = _message_queryset(agent)
    message_exists = message_qs.filter(timestamp__lt=dt).exists()
    if cursor.kind == "message":
        message_exists = message_exists or message_qs.filter(
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
    completion_exists = (
        PersistentAgentCompletion.objects.filter(
            agent=agent,
            completion_type__in=THINKING_COMPLETION_TYPES,
        )
        .exclude(thinking_content__isnull=True)
        .exclude(thinking_content__exact="")
        .filter(created_at__lt=dt)
        .exists()
    )
    if cursor.kind == "thinking":
        try:
            cursor_uuid = uuid.UUID(cursor.identifier)
            completion_exists = completion_exists or (
                PersistentAgentCompletion.objects.filter(
                    agent=agent,
                    completion_type__in=THINKING_COMPLETION_TYPES,
                )
                .exclude(thinking_content__isnull=True)
                .exclude(thinking_content__exact="")
                .filter(created_at=dt, id__lt=cursor_uuid)
                .exists()
            )
        except Exception:
            pass

    kanban_exists = PersistentAgentKanbanEvent.objects.filter(
        agent=agent,
        cursor_value__lt=cursor.value,
    ).exists()
    if cursor.kind == "kanban":
        try:
            cursor_uuid = uuid.UUID(cursor.identifier)
            kanban_exists = kanban_exists or PersistentAgentKanbanEvent.objects.filter(
                agent=agent,
                cursor_value=cursor.value,
                cursor_identifier__lt=cursor_uuid,
            ).exists()
        except Exception:
            pass

    return message_exists or step_exists or completion_exists or kanban_exists


def _has_more_after(agent: PersistentAgent, cursor: CursorPayload | None) -> bool:
    if cursor is None:
        return False
    dt = _dt_from_cursor(cursor)

    message_qs = _message_queryset(agent)
    message_exists = message_qs.filter(timestamp__gt=dt).exists()
    if cursor.kind == "message":
        message_exists = message_exists or message_qs.filter(
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
    completion_exists = (
        PersistentAgentCompletion.objects.filter(
            agent=agent,
            completion_type__in=THINKING_COMPLETION_TYPES,
        )
        .exclude(thinking_content__isnull=True)
        .exclude(thinking_content__exact="")
        .filter(created_at__gt=dt)
        .exists()
    )
    if cursor.kind == "thinking":
        try:
            cursor_uuid = uuid.UUID(cursor.identifier)
            completion_exists = completion_exists or (
                PersistentAgentCompletion.objects.filter(
                    agent=agent,
                    completion_type__in=THINKING_COMPLETION_TYPES,
                )
                .exclude(thinking_content__isnull=True)
                .exclude(thinking_content__exact="")
                .filter(created_at=dt, id__gt=cursor_uuid)
                .exists()
            )
        except Exception:
            pass
    kanban_exists = PersistentAgentKanbanEvent.objects.filter(
        agent=agent,
        cursor_value__gt=cursor.value,
    ).exists()
    if cursor.kind == "kanban":
        try:
            cursor_uuid = uuid.UUID(cursor.identifier)
            kanban_exists = kanban_exists or PersistentAgentKanbanEvent.objects.filter(
                agent=agent,
                cursor_value=cursor.value,
                cursor_identifier__gt=cursor_uuid,
            ).exists()
        except Exception:
            pass

    return message_exists or step_exists or completion_exists or kanban_exists


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


def _coerce_schedule_eta_to_timedelta(eta: object) -> timedelta | None:
    if eta is None:
        return None
    if isinstance(eta, timedelta):
        return eta
    if isinstance(eta, (int, float)):
        return timedelta(seconds=float(eta))
    return None


def _compute_next_scheduled_run(agent: PersistentAgent, *, now: datetime | None = None) -> datetime | None:
    """Return the next known scheduled wake-up for an agent."""

    if not agent.is_active or agent.life_state != PersistentAgent.LifeState.ACTIVE:
        return None

    current_env = getattr(settings, "GOBII_RELEASE_ENV", "local")
    if (agent.execution_environment or "local") != current_env:
        return None

    schedule_value = (agent.schedule or "").strip()
    if not schedule_value:
        return None

    try:
        schedule_obj = ScheduleParser.parse(schedule_value)
    except ValueError:
        return None

    if schedule_obj is None:
        return None

    current_time = now or timezone.now()
    try:
        eta = schedule_obj.remaining_estimate(current_time)
    except (AttributeError, TypeError, ValueError):
        return None

    delay = _coerce_schedule_eta_to_timedelta(eta)
    if delay is None:
        return None
    if delay.total_seconds() < 0:
        delay = timedelta(seconds=0)
    return current_time + delay


def build_processing_snapshot(agent: PersistentAgent) -> ProcessingSnapshot:
    """Compute current processing activity and active web tasks for an agent."""

    # Check if the agent event processing lock is held
    # Note: Redlock prefixes keys with "redlock:" internally
    from config.redis_client import get_redis_client

    lock_key = f"redlock:agent-event-processing:{agent.id}"
    lock_active = False
    queued_flag = False
    heartbeat_active = False
    try:
        redis_client = get_redis_client()
        lock_active = bool(redis_client.exists(lock_key))
        queued_flag = is_processing_queued(agent.id, client=redis_client)
        heartbeat_active = bool(get_processing_heartbeat(agent.id, client=redis_client))
    except Exception:
        lock_active = False
        queued_flag = False
        heartbeat_active = False

    web_tasks: list[dict] = []
    if getattr(agent, "browser_use_agent_id", None):
        task_qs: BrowserUseAgentTaskQuerySet = BrowserUseAgentTask.objects
        active_tasks = task_qs.filter(
            agent=agent.browser_use_agent,
            status__in=WEB_TASK_ACTIVE_STATUSES,
            is_deleted=False,
        ).order_by("created_at")
        now = timezone.now()
        max_age_seconds = int(getattr(settings, "AGENT_WEB_TASK_ACTIVE_MAX_AGE_SECONDS", 0))
        if max_age_seconds > 0:
            cutoff = now - timedelta(seconds=max_age_seconds)
            active_tasks = active_tasks.filter(updated_at__gte=cutoff)
        web_tasks = [_build_web_task_payload(task, now=now) for task in active_tasks]

    active = bool(heartbeat_active or lock_active or queued_flag or web_tasks)
    next_scheduled_at = _compute_next_scheduled_run(agent)
    return ProcessingSnapshot(active=active, web_tasks=web_tasks, next_scheduled_at=next_scheduled_at)


def serialize_processing_snapshot(snapshot: ProcessingSnapshot) -> dict:
    return {
        "active": snapshot.active,
        "webTasks": snapshot.web_tasks,
        "nextScheduledAt": _format_timestamp(snapshot.next_scheduled_at),
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
    thinking_envelopes = _envelop_thinking(_thinking_queryset(agent, direction, cursor_payload))
    kanban_envelopes = _envelop_kanban_events(_kanban_queryset(agent, direction, cursor_payload))
    if direction == "initial" and not kanban_envelopes:
        baseline_event = ensure_kanban_baseline_event(agent)
        if baseline_event:
            kanban_envelopes = _envelop_kanban_events([baseline_event])

    merged: list[MessageEnvelope | StepEnvelope | ThinkingEnvelope | KanbanEnvelope] = sorted(
        [*message_envelopes, *step_envelopes, *thinking_envelopes, *kanban_envelopes],
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
    agent_name = getattr(agent, "name", None) or "Agent"
    if " " in agent_name:
        agent_name = agent_name.split()[0]
    user_lookup = _build_web_user_lookup(env.message for env in message_envelopes)
    for env in truncated:
        if isinstance(env, StepEnvelope):
            cluster_buffer.append(env)
            continue
        if cluster_buffer:
            timeline_events.append(_build_cluster(cluster_buffer, tool_label_map))
            cluster_buffer = []
        if isinstance(env, ThinkingEnvelope):
            timeline_events.append(_serialize_thinking(env))
        elif isinstance(env, KanbanEnvelope):
            timeline_events.append(serialize_persisted_kanban_event(env, agent_name))
        else:
            timeline_events.append(_serialize_message(env, user_lookup))
    if cluster_buffer:
        timeline_events.append(_build_cluster(cluster_buffer, tool_label_map))

    oldest_cursor = truncated[0].cursor if truncated else None
    newest_cursor = truncated[-1].cursor if truncated else None

    has_more_older = False
    if oldest_cursor and (direction != "initial" or len(filtered) >= limit):
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


def serialize_thinking_event(completion: PersistentAgentCompletion) -> dict | None:
    envelopes = _envelop_thinking([completion])
    if not envelopes:
        return None
    return _serialize_thinking(envelopes[0])


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


def serialize_persisted_kanban_event(env: KanbanEnvelope, agent_name: str) -> dict:
    event = env.event
    timestamp = _format_timestamp(_dt_from_cursor(env.cursor))

    titles_by_status = {"todo": [], "doing": [], "done": []}
    for title in event.titles.all():
        if title.status in titles_by_status:
            titles_by_status[title.status].append(title.title)

    serialized_changes = [
        {
            "cardId": str(change.card_id),
            "title": change.title,
            "action": change.action,
            "fromStatus": change.from_status,
            "toStatus": change.to_status,
        }
        for change in event.changes.all()
    ]

    serialized_snapshot = {
        "todoCount": event.todo_count,
        "doingCount": event.doing_count,
        "doneCount": event.done_count,
        "todoTitles": titles_by_status["todo"],
        "doingTitles": titles_by_status["doing"],
        "doneTitles": titles_by_status["done"],
    }

    return {
        "kind": "kanban",
        "cursor": env.cursor.encode(),
        "timestamp": timestamp,
        "agentName": agent_name,
        "displayText": event.display_text,
        "primaryAction": event.primary_action,
        "changes": serialized_changes,
        "snapshot": serialized_snapshot,
    }


def serialize_kanban_event(
    agent_name: str,
    changes: Sequence,  # Sequence[KanbanCardChange]
    snapshot,  # KanbanBoardSnapshot
) -> dict:
    """Serialize a kanban timeline event for frontend display.

    Args:
        agent_name: The name of the agent (for display text)
        changes: Sequence of KanbanCardChange objects
        snapshot: KanbanBoardSnapshot with current board state
    """
    now = timezone.now()
    timestamp = _format_timestamp(now)
    cursor_value = _microsecond_epoch(now)
    cursor = CursorPayload(value=cursor_value, kind="kanban", identifier=str(uuid.uuid4()))

    # Determine primary action for display
    completed_count = sum(1 for c in changes if c.action == "completed")
    started_count = sum(1 for c in changes if c.action == "started")
    created_count = sum(1 for c in changes if c.action == "created")
    updated_count = sum(1 for c in changes if c.action == "updated")
    deleted_count = sum(1 for c in changes if c.action == "deleted")
    archived_count = sum(1 for c in changes if c.action == "archived")

    # Build display text
    if completed_count > 0 and completed_count == len(changes):
        if completed_count == 1:
            display_text = f'{agent_name} completed "{changes[0].title}"'
        else:
            display_text = f"{agent_name} completed {completed_count} tasks"
        primary_action = "completed"
    elif started_count > 0 and started_count == len(changes):
        if started_count == 1:
            display_text = f'{agent_name} started "{changes[0].title}"'
        else:
            display_text = f"{agent_name} started {started_count} tasks"
        primary_action = "started"
    elif created_count > 0 and created_count == len(changes):
        if created_count == 1:
            display_text = f'{agent_name} added "{changes[0].title}"'
        else:
            display_text = f"{agent_name} added {created_count} tasks"
        primary_action = "created"
    elif deleted_count > 0 and deleted_count == len(changes):
        if deleted_count == 1:
            display_text = f'{agent_name} removed "{changes[0].title}"'
        else:
            display_text = f"{agent_name} removed {deleted_count} tasks"
        primary_action = "deleted"
    elif archived_count > 0 and archived_count == len(changes):
        if archived_count == 1:
            display_text = f'{agent_name} archived "{changes[0].title}"'
        else:
            display_text = f"{agent_name} archived {archived_count} tasks"
        primary_action = "archived"
    elif updated_count > 0 and updated_count == len(changes):
        if updated_count == 1:
            display_text = f'{agent_name} updated "{changes[0].title}"'
        else:
            display_text = f"{agent_name} updated {updated_count} tasks"
        primary_action = "updated"
    else:
        display_text = f"{agent_name} updated kanban"
        primary_action = "updated"

    # Serialize changes for animation
    serialized_changes = [
        {
            "cardId": c.card_id,
            "title": c.title,
            "action": c.action,
            "fromStatus": c.from_status,
            "toStatus": c.to_status,
        }
        for c in changes
    ]

    # Serialize snapshot
    serialized_snapshot = {
        "todoCount": snapshot.todo_count,
        "doingCount": snapshot.doing_count,
        "doneCount": snapshot.done_count,
        "todoTitles": list(snapshot.todo_titles),
        "doingTitles": list(snapshot.doing_titles),
        "doneTitles": list(snapshot.done_titles),
    }

    return {
        "kind": "kanban",
        "cursor": cursor.encode(),
        "timestamp": timestamp,
        "agentName": agent_name,
        "displayText": display_text,
        "primaryAction": primary_action,
        "changes": serialized_changes,
        "snapshot": serialized_snapshot,
    }
