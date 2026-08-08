"""Validation and presentation helpers for Discord embeds."""

import json
import re
from typing import Any, Mapping
from urllib.parse import urlparse

from api.agent.comms.outbound_content_policy import contains_raw_html


DISCORD_MAX_EMBEDS = 10
DISCORD_MAX_EMBED_FIELDS = 25
DISCORD_EMBED_TITLE_MAX_LENGTH = 256
DISCORD_EMBED_DESCRIPTION_MAX_LENGTH = 4096
DISCORD_EMBED_FIELD_NAME_MAX_LENGTH = 256
DISCORD_EMBED_FIELD_VALUE_MAX_LENGTH = 1024
DISCORD_EMBED_TOTAL_TEXT_MAX_LENGTH = 6000

_HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_SIMPLE_EMBED_KEYS = frozenset({"title", "description", "url", "color", "fields"})
_SIMPLE_EMBED_FIELD_KEYS = frozenset({"name", "value", "inline"})


def discord_embed_tool_schema() -> dict[str, Any]:
    return {
        "type": "array",
        "maxItems": DISCORD_MAX_EMBEDS,
        "description": (
            "Optional Discord simple-card embeds. Each card needs a title, description, or field. "
            "Use #RRGGBB colors. Discord allows at most 10 cards, 25 fields per card, and 6000 "
            "combined text characters across all cards."
        ),
        "items": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "title": {"type": "string", "maxLength": DISCORD_EMBED_TITLE_MAX_LENGTH},
                "description": {
                    "type": "string",
                    "maxLength": DISCORD_EMBED_DESCRIPTION_MAX_LENGTH,
                    "description": "Discord-compatible Markdown description; raw HTML is rejected.",
                },
                "url": {
                    "type": "string",
                    "description": "Optional absolute http(s) URL opened from the embed title.",
                },
                "color": {
                    "type": "string",
                    "pattern": r"^#[0-9A-Fa-f]{6}$",
                    "description": "Optional six-digit hex color such as #5865F2.",
                },
                "fields": {
                    "type": "array",
                    "maxItems": DISCORD_MAX_EMBED_FIELDS,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "name": {
                                "type": "string",
                                "maxLength": DISCORD_EMBED_FIELD_NAME_MAX_LENGTH,
                            },
                            "value": {
                                "type": "string",
                                "maxLength": DISCORD_EMBED_FIELD_VALUE_MAX_LENGTH,
                                "description": "Discord-compatible Markdown; raw HTML is rejected.",
                            },
                            "inline": {"type": "boolean"},
                        },
                        "required": ["name", "value"],
                    },
                },
            },
        },
    }


def _text(value: object) -> str:
    return str(value or "").strip()


def _optional_string(source: Mapping[str, Any], key: str, label: str) -> str:
    value = source.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string.")
    return value.strip()


def _validate_length(value: str, limit: int, label: str) -> None:
    if len(value) > limit:
        raise ValueError(f"{label} must be {limit} characters or fewer.")


def _validate_url(value: str, label: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{label} must be an absolute http(s) URL.")


def _validate_markdown(value: str, label: str) -> None:
    if contains_raw_html(value):
        raise ValueError(
            f"{label} supports Markdown, not raw HTML. Replace HTML formatting with Markdown and retry."
        )


def normalize_discord_embeds(raw_embeds: object) -> list[dict[str, Any]]:
    """Validate the agent-facing simple-card schema and build Discord API payloads."""

    if raw_embeds is None:
        return []
    if not isinstance(raw_embeds, list):
        raise ValueError("embeds must be an array.")
    if len(raw_embeds) > DISCORD_MAX_EMBEDS:
        raise ValueError(f"Discord supports at most {DISCORD_MAX_EMBEDS} embeds per message.")

    normalized: list[dict[str, Any]] = []
    total_text_length = 0
    for embed_index, raw_embed in enumerate(raw_embeds, start=1):
        if not isinstance(raw_embed, Mapping):
            raise ValueError(f"embeds[{embed_index}] must be an object.")
        unknown_keys = sorted(set(raw_embed) - _SIMPLE_EMBED_KEYS)
        if unknown_keys:
            raise ValueError(
                f"embeds[{embed_index}] contains unsupported properties: {', '.join(unknown_keys)}."
            )

        embed: dict[str, Any] = {}
        title = _optional_string(raw_embed, "title", f"embeds[{embed_index}].title")
        description = _optional_string(raw_embed, "description", f"embeds[{embed_index}].description")
        url = _optional_string(raw_embed, "url", f"embeds[{embed_index}].url")
        color = _optional_string(raw_embed, "color", f"embeds[{embed_index}].color")
        raw_fields = raw_embed.get("fields")
        if raw_fields is None:
            raw_fields = []
        if not isinstance(raw_fields, list):
            raise ValueError(f"embeds[{embed_index}].fields must be an array.")
        if len(raw_fields) > DISCORD_MAX_EMBED_FIELDS:
            raise ValueError(
                f"embeds[{embed_index}].fields supports at most {DISCORD_MAX_EMBED_FIELDS} fields."
            )

        if title:
            _validate_length(title, DISCORD_EMBED_TITLE_MAX_LENGTH, f"embeds[{embed_index}].title")
            _validate_markdown(title, f"embeds[{embed_index}].title")
            embed["title"] = title
            total_text_length += len(title)
        if description:
            _validate_length(
                description,
                DISCORD_EMBED_DESCRIPTION_MAX_LENGTH,
                f"embeds[{embed_index}].description",
            )
            _validate_markdown(description, f"embeds[{embed_index}].description")
            embed["description"] = description
            total_text_length += len(description)
        if url:
            _validate_url(url, f"embeds[{embed_index}].url")
            embed["url"] = url
        if color:
            if not _HEX_COLOR_RE.fullmatch(color):
                raise ValueError(f"embeds[{embed_index}].color must use #RRGGBB format.")
            embed["color"] = int(color[1:], 16)

        fields: list[dict[str, Any]] = []
        for field_index, raw_field in enumerate(raw_fields, start=1):
            if not isinstance(raw_field, Mapping):
                raise ValueError(f"embeds[{embed_index}].fields[{field_index}] must be an object.")
            unknown_field_keys = sorted(set(raw_field) - _SIMPLE_EMBED_FIELD_KEYS)
            if unknown_field_keys:
                raise ValueError(
                    f"embeds[{embed_index}].fields[{field_index}] contains unsupported properties: "
                    f"{', '.join(unknown_field_keys)}."
                )
            name = _optional_string(
                raw_field,
                "name",
                f"embeds[{embed_index}].fields[{field_index}].name",
            )
            value = _optional_string(
                raw_field,
                "value",
                f"embeds[{embed_index}].fields[{field_index}].value",
            )
            if not name or not value:
                raise ValueError(f"embeds[{embed_index}].fields[{field_index}] requires name and value.")
            _validate_length(
                name,
                DISCORD_EMBED_FIELD_NAME_MAX_LENGTH,
                f"embeds[{embed_index}].fields[{field_index}].name",
            )
            _validate_length(
                value,
                DISCORD_EMBED_FIELD_VALUE_MAX_LENGTH,
                f"embeds[{embed_index}].fields[{field_index}].value",
            )
            _validate_markdown(name, f"embeds[{embed_index}].fields[{field_index}].name")
            _validate_markdown(value, f"embeds[{embed_index}].fields[{field_index}].value")
            field = {"name": name, "value": value}
            if "inline" in raw_field:
                if not isinstance(raw_field["inline"], bool):
                    raise ValueError(f"embeds[{embed_index}].fields[{field_index}].inline must be boolean.")
                field["inline"] = raw_field["inline"]
            fields.append(field)
            total_text_length += len(name) + len(value)
        if fields:
            embed["fields"] = fields

        if not title and not description and not fields:
            raise ValueError(f"embeds[{embed_index}] requires a title, description, or field.")
        normalized.append(embed)

    if total_text_length > DISCORD_EMBED_TOTAL_TEXT_MAX_LENGTH:
        raise ValueError(
            "Discord embed text must total "
            f"{DISCORD_EMBED_TOTAL_TEXT_MAX_LENGTH} characters or fewer across the message."
        )
    return normalized


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _display_color(value: object) -> str:
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 0xFFFFFF:
        return f"#{value:06X}"
    return _text(value)


def _http_url(value: object) -> str:
    url = _text(value)
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return url


def project_discord_embeds(raw_embeds: object) -> list[dict[str, Any]]:
    """Project provider embeds onto the display-safe fields shared by prompts and Live Chat."""

    if not isinstance(raw_embeds, list):
        return []

    projected: list[dict[str, Any]] = []
    for raw_embed in raw_embeds[:DISCORD_MAX_EMBEDS]:
        if not isinstance(raw_embed, Mapping):
            continue

        embed: dict[str, Any] = {}
        for key in ("title", "description"):
            value = _text(raw_embed.get(key))
            if value:
                embed[key] = value

        url = _http_url(raw_embed.get("url"))
        if url:
            embed["url"] = url
        color = _display_color(raw_embed.get("color"))
        if _HEX_COLOR_RE.fullmatch(color):
            embed["color"] = color.upper()

        for source_key, target_key in (("author", "author"), ("footer", "footer"), ("provider", "provider")):
            source = _mapping(raw_embed.get(source_key))
            nested: dict[str, Any] = {}
            text_key = "text" if source_key == "footer" else "name"
            text_value = _text(source.get(text_key))
            if text_value:
                nested[text_key] = text_value
            source_url = _http_url(source.get("url"))
            if source_url:
                nested["url"] = source_url
            icon_url = _http_url(source.get("icon_url"))
            if icon_url:
                nested["iconUrl"] = icon_url
            if nested:
                embed[target_key] = nested

        fields = []
        raw_fields = raw_embed.get("fields")
        if isinstance(raw_fields, list):
            for raw_field in raw_fields[:DISCORD_MAX_EMBED_FIELDS]:
                if not isinstance(raw_field, Mapping):
                    continue
                name = _text(raw_field.get("name"))
                value = _text(raw_field.get("value"))
                if name and value:
                    fields.append({
                        "name": name,
                        "value": value,
                        "inline": raw_field.get("inline") is True,
                    })
        if fields:
            embed["fields"] = fields

        for source_key, target_key in (
            ("image", "imageUrl"),
            ("thumbnail", "thumbnailUrl"),
            ("video", "videoUrl"),
        ):
            media_url = _http_url(_mapping(raw_embed.get(source_key)).get("url"))
            if media_url:
                embed[target_key] = media_url

        if embed:
            projected.append(embed)
    return projected


def format_discord_embeds(raw_embeds: object) -> str:
    """Serialize meaningful received embed fields for agent context."""

    embeds = project_discord_embeds(raw_embeds)
    return json.dumps(embeds, ensure_ascii=False, separators=(",", ":")) if embeds else ""


def discord_embed_signature_projection(raw_embeds: object) -> list[dict[str, Any]]:
    """Project sent and received embeds onto fields stable across Discord serialization."""

    if not isinstance(raw_embeds, list):
        return []
    rich_embeds = [
        embed
        for embed in raw_embeds
        if isinstance(embed, Mapping) and _text(embed.get("type")).casefold() in {"", "rich"}
    ]
    projected = []
    for embed in project_discord_embeds(rich_embeds):
        signature = {key: embed[key] for key in ("title", "description", "url", "fields") if key in embed}
        if "color" in embed:
            signature["color"] = int(embed["color"][1:], 16)
        if signature:
            projected.append(signature)
    return projected
