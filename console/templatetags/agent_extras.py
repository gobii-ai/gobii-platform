from __future__ import annotations

import json

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
