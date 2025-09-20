from __future__ import annotations

import json
import re
from html import escape
from html.parser import HTMLParser
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


_BASIC_HTML_RE = re.compile(r"<[a-zA-Z][^>]*>")
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
}
_SELF_CLOSING_TAGS = {"br"}
_ALLOWED_ATTRS = {
    "a": {"href", "title"},
    "span": set(),
}
_ALLOWED_PROTOCOLS = {"http", "https", "mailto", "tel", ""}


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
        if allowed:
            for name, value in attrs:
                if name not in allowed or value is None:
                    continue
                if name == "href" and not _is_safe_href(value):
                    continue
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


@register.filter
def agent_message_html(value: str | None) -> str:
    if not value:
        return ""
    if _BASIC_HTML_RE.search(value):
        return mark_safe(_render_agent_html(value))
    return agent_markdown(value)
