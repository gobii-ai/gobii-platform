from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html import escape
from html.parser import HTMLParser
from typing import Any, Iterable
from urllib.parse import urlsplit

from django import template
from django.utils.safestring import mark_safe

from api.models import CommsChannel
from util.markdown_render import render_agent_markdown

register = template.Library()


_CHANNEL_LABELS = {
    CommsChannel.EMAIL.value: "Email",
    CommsChannel.SMS.value: "SMS",
    CommsChannel.SLACK.value: "Slack",
    CommsChannel.DISCORD.value: "Discord",
    CommsChannel.WEB.value: "Web",
    CommsChannel.OTHER.value: "Other",
}


@register.filter
def channel_label(channel: str | CommsChannel) -> str:
    if isinstance(channel, CommsChannel):
        key = channel.value
    else:
        key = str(channel)
    return _CHANNEL_LABELS.get(key, key.replace("_", " ").title())


@register.filter
def agent_markdown(value: str | None) -> str:
    if not value:
        return ""
    return mark_safe(render_agent_markdown(value))


@register.filter
def pretty_json(value) -> str:
    try:
        return json.dumps(value, indent=2, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


_BASIC_HTML_RE = re.compile(r"</?([a-zA-Z][\w-]*)(?=[\s>/])[^>]*>")
_ALLOWED_TAGS = {
    "p",
    "br",
    "strong",
    "em",
    "b",
    "i",
    "ul",
    "ol",
    "li",
    "blockquote",
    "code",
    "pre",
    "span",
    "a",
    "del",
    "s",
    "input",
}
_SELF_CLOSING_TAGS = {"br", "input"}
_ALLOWED_ATTRS = {
    "a": {"href", "title"},
    "span": set(),
    "input": {"type", "checked", "disabled"},
}
_ALLOWED_PROTOCOLS = {"http", "https", "mailto", "tel", ""}
_TAG_FINDER_RE = re.compile(r"</?([a-zA-Z0-9]+)[^>]*>")
_INLINE_ONLY_TAGS = {"strong", "em", "b", "i", "code", "span", "a", "br", "del", "s", "input"}


def _is_safe_href(value: str) -> bool:
    href = (value or "").strip()
    if not href:
        return False
    scheme = urlsplit(href).scheme.lower()
    return scheme in _ALLOWED_PROTOCOLS


class _BasicHTMLSanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        tag_lower = tag.lower()
        if tag_lower not in _ALLOWED_TAGS:
            return

        allowed = _ALLOWED_ATTRS.get(tag_lower)
        attr_parts: list[str] = []
        attr_dict = dict(attrs or [])

        if tag_lower == "input":
            type_value = (attr_dict.get("type") or "").strip().lower()
            if type_value != "checkbox":
                return

        if allowed:
            for name, value in attrs:
                if name not in allowed:
                    continue
                if name == "href" and value is not None and not _is_safe_href(value):
                    continue
                if value is None:
                    attr_parts.append(name)
                else:
                    attr_parts.append(f'{name}="{escape(value, quote=True)}"')

        attr_str = f" {' '.join(attr_parts)}" if attr_parts else ""
        self.parts.append(f"<{tag_lower}{attr_str}>")

    def handle_endtag(self, tag):
        tag_lower = tag.lower()
        if tag_lower in _ALLOWED_TAGS and tag_lower not in _SELF_CLOSING_TAGS:
            self.parts.append(f"</{tag_lower}>")

    def handle_startendtag(self, tag, attrs):
        # Treat self-closing tags the same as a start tag
        self.handle_starttag(tag, attrs)

    def handle_data(self, data):
        if data:
            self.parts.append(escape(data))

    def handle_entityref(self, name):
        self.parts.append(f"&{name};")

    def handle_charref(self, name):
        self.parts.append(f"&#{name};")

    def get_html(self) -> str:
        return "".join(self.parts)


def _render_agent_html(value: str) -> str:
    sanitizer = _BasicHTMLSanitizer()
    sanitizer.feed(value)
    sanitizer.close()
    return sanitizer.get_html()


def _contains_only_inline_tags(html: str) -> bool:
    for name in _TAG_FINDER_RE.findall(html):
        if name and name.lower() not in _INLINE_ONLY_TAGS:
            return False
    return True


def _preserve_plaintext_linebreaks(html: str) -> str:
    if not html:
        return ""

    normalized = html.replace("\r\n", "\n").replace("\r", "\n")
    if "\n" not in normalized:
        return normalized

    if _contains_only_inline_tags(normalized):
        return normalized.replace("\n", "<br />")

    return normalized


@register.filter
def agent_message_html(value: str | None) -> str:
    if not value:
        return ""
    if _BASIC_HTML_RE.search(value):
        sanitized = _render_agent_html(value)
        return mark_safe(_preserve_plaintext_linebreaks(sanitized))
    return agent_markdown(value)


def _get_attr_or_key(obj: Any, key: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


@register.filter
def attr(value: Any, key: str) -> Any:
    """Safe attribute/key lookup for templates."""
    return _get_attr_or_key(value, key)


_SKIP_TOOL_NAMES = {
    "send_email",
    "send_web_message",
    "sleep",
    "sleep_until_next_trigger",
    "action",
    "",
    None,
}

_TOOL_CLUSTER_COLLAPSE_THRESHOLD = 5


def _should_skip_tool(tool_call: Any) -> bool:
    name = getattr(tool_call, "tool_name", None)
    if name is None:
        return True
    return name in _SKIP_TOOL_NAMES


@dataclass
class _ToolMeta:
    label: str
    icon_paths: tuple[str, ...]
    icon_bg: str
    icon_color: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "paths": self.icon_paths,
            "icon_bg": self.icon_bg,
            "icon_color": self.icon_color,
        }


_TOOL_META: dict[str, _ToolMeta] = {
    "update_charter": _ToolMeta(
        label="Assignment updated",
        icon_paths=(
            "M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z",
        ),
        icon_bg="bg-indigo-100",
        icon_color="text-indigo-600",
    ),
    "update_schedule": _ToolMeta(
        label="Schedule updated",
        icon_paths=(
            "M8 7V3m8 4V3m-9 8h10m-12 8h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z",
        ),
        icon_bg="bg-sky-100",
        icon_color="text-sky-600",
    ),
    "sqlite_batch": _ToolMeta(
        label="Database query",
        icon_paths=(
            "M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4m0 5c0 2.21-3.582 4-8 4s-8-1.79-8-4",
        ),
        icon_bg="bg-emerald-100",
        icon_color="text-emerald-600",
    ),
    "search_tools": _ToolMeta(
        label="Tool discovery",
        icon_paths=(
            "M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z",
        ),
        icon_bg="bg-blue-100",
        icon_color="text-blue-600",
    ),
    "web_search": _ToolMeta(
        label="Web search",
        icon_paths=(
            "M21 12a9 9 0 01-9 9m9-9a9 9 0 00-9-9m9 9H3m9 9v-9m0-9v9",
        ),
        icon_bg="bg-purple-100",
        icon_color="text-purple-600",
    ),
    "search": _ToolMeta(
        label="Web search",
        icon_paths=(
            "M21 12a9 9 0 01-9 9m9-9a9 9 0 00-9-9m9 9H3m9 9v-9m0-9v9",
        ),
        icon_bg="bg-purple-100",
        icon_color="text-purple-600",
    ),
    "read_file": _ToolMeta(
        label="File access",
        icon_paths=(
            "M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z",
        ),
        icon_bg="bg-orange-100",
        icon_color="text-orange-600",
    ),
    "file_read": _ToolMeta(
        label="File access",
        icon_paths=(
            "M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z",
        ),
        icon_bg="bg-orange-100",
        icon_color="text-orange-600",
    ),
    "write_file": _ToolMeta(
        label="File update",
        icon_paths=(
            "M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z",
        ),
        icon_bg="bg-green-100",
        icon_color="text-green-600",
    ),
    "file_write": _ToolMeta(
        label="File update",
        icon_paths=(
            "M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z",
        ),
        icon_bg="bg-green-100",
        icon_color="text-green-600",
    ),
    "api_call": _ToolMeta(
        label="API request",
        icon_paths=(
            "M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z",
        ),
        icon_bg="bg-cyan-100",
        icon_color="text-cyan-600",
    ),
    "http_request": _ToolMeta(
        label="API request",
        icon_paths=(
            "M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z",
        ),
        icon_bg="bg-cyan-100",
        icon_color="text-cyan-600",
    ),
    "spawn_web_task": _ToolMeta(
        label="Browser task",
        icon_paths=(
            "M4 5h16M4 9h16M8 13h8m-8 4h5m-9 2h12a2 2 0 002-2V5a2 2 0 00-2-2H4a2 2 0 00-2 2v12a2 2 0 002 2z",
        ),
        icon_bg="bg-violet-100",
        icon_color="text-violet-600",
    ),
    "think": _ToolMeta(
        label="Analysis",
        icon_paths=(
            "M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z",
        ),
        icon_bg="bg-yellow-100",
        icon_color="text-yellow-600",
    ),
    "reasoning": _ToolMeta(
        label="Analysis",
        icon_paths=(
            "M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z",
        ),
        icon_bg="bg-yellow-100",
        icon_color="text-yellow-600",
    ),
}

_DEFAULT_TOOL_META = _ToolMeta(
    label="Agent action",
    icon_paths=("M13 10V3L4 14h7v7l9-11h-7z",),
    icon_bg="bg-slate-100",
    icon_color="text-slate-500",
)


def _resolve_tool_meta(tool_call: Any) -> _ToolMeta:
    name = getattr(tool_call, "tool_name", "") or ""
    return _TOOL_META.get(str(name).lower(), _DEFAULT_TOOL_META)


@register.filter
def tool_metadata(tool_call: Any) -> dict[str, Any]:
    return _resolve_tool_meta(tool_call).as_dict()


@register.filter
def group_timeline_events(events: Iterable[Any]) -> list[dict[str, Any]]:
    if events is None:
        return []

    grouped: list[dict[str, Any]] = []
    step_buffer: list[dict[str, Any]] = []
    latest_timestamp = None
    earliest_timestamp = None
    latest_cursor = None
    label_tracker: dict[str, dict[str, Any]] = {}

    def flush() -> None:
        nonlocal step_buffer, latest_timestamp, earliest_timestamp, latest_cursor, label_tracker
        if not step_buffer:
            return
        entry_count = len(step_buffer)
        grouped.append(
            {
                "type": "steps",
                "entries": step_buffer,
                "latest_timestamp": latest_timestamp,
                "earliest_timestamp": earliest_timestamp,
                "cursor": latest_cursor,
                "labels": list(label_tracker.values()),
                "entry_count": entry_count,
                "collapsible": entry_count >= _TOOL_CLUSTER_COLLAPSE_THRESHOLD,
                "collapse_threshold": _TOOL_CLUSTER_COLLAPSE_THRESHOLD,
            }
        )
        step_buffer = []
        latest_timestamp = None
        earliest_timestamp = None
        latest_cursor = None
        label_tracker = {}

    for event in events:
        message = getattr(event, "message", None)
        step = getattr(event, "step", None)
        tool_call = getattr(step, "tool_call", None) if step else None

        if step and tool_call and not _should_skip_tool(tool_call):
            meta = _resolve_tool_meta(tool_call)
            entry = {
                "event": event,
                "step": step,
                "tool": tool_call,
                "meta": meta.as_dict(),
                "show_sql": getattr(tool_call, "tool_name", "").lower() == "sqlite_batch",
            }
            latest_cursor = getattr(event, "cursor", None)
            step_buffer.append(entry)
            latest_timestamp = getattr(event, "timestamp", None)
            if earliest_timestamp is None:
                earliest_timestamp = getattr(event, "timestamp", None)
            else:
                current_ts = getattr(event, "timestamp", None)
                if current_ts and earliest_timestamp and current_ts < earliest_timestamp:
                    earliest_timestamp = current_ts
            if meta.label not in label_tracker:
                label_tracker[meta.label] = meta.as_dict()
            continue

        flush()

        if message:
            grouped.append({"type": "message", "event": event, "message": message})

    flush()
    return grouped
