"""Compatibility ingestion for existing Pipedream Discord trigger subscriptions."""

import hashlib
import hmac
import json
import secrets
import time
from typing import Iterable, Mapping

from django.utils import timezone

from api.agent.comms.adapters import ParsedMessage
from api.agent.comms.message_service import ingest_inbound_message
from api.models import CommsChannel, DeliveryStatus, PersistentAgent, PersistentAgentConversation, PersistentAgentMessage, PersistentAgentPipedreamTriggerSubscription
from api.services.discord_messages import (
    create_discord_outbound_message as _create_discord_outbound_message,
    discord_agent_address as _discord_agent_address,
    discord_channel_address as _discord_channel_address,
    discord_channel_source_label as _discord_channel_source_label,
    discord_conversation_address as _discord_conversation_address,
    ensure_discord_agent_endpoint as _ensure_discord_agent_endpoint,
    ensure_discord_conversation_participants as _ensure_discord_conversation_participants,
    find_recent_discord_outbound,
    get_or_create_discord_conversation as _get_or_create_discord_conversation,
    schedule_discord_inbound_processing,
)

DISCORD_APP_SLUG = "discord"
DISCORD_MESSAGE_EVENT_TYPE = "message.created"
SIGNATURE_TOLERANCE_SECONDS = 300
DISCORD_ATTACHMENT_URL_KEYS = ("url", "downloadUrl", "download_url", "proxyURL", "proxyUrl", "proxy_url", "media_url")
DISCORD_ATTACHMENT_FILENAME_KEYS = ("filename", "fileName", "name")
DISCORD_ATTACHMENT_CONTENT_TYPE_KEYS = ("contentType", "content_type", "mimeType", "mime_type")


class PipedreamTriggerSignatureError(ValueError):
    """Raised when a Pipedream trigger delivery signature is invalid."""


def verify_pipedream_signature(
    subscription: PersistentAgentPipedreamTriggerSubscription,
    signature_header: str,
    raw_body: bytes,
) -> None:
    signing_key = subscription.signing_key
    if not signing_key:
        raise PipedreamTriggerSignatureError("Missing signing key.")
    if not signature_header:
        raise PipedreamTriggerSignatureError("Missing Pipedream signature.")

    parts = {}
    for item in signature_header.split(","):
        key, sep, value = item.partition("=")
        if sep:
            parts[key.strip()] = value.strip()
    timestamp = parts.get("t")
    signature = parts.get("v1")
    if not timestamp or not signature:
        raise PipedreamTriggerSignatureError("Malformed Pipedream signature.")
    try:
        signed_at = int(timestamp)
    except ValueError as exc:
        raise PipedreamTriggerSignatureError("Invalid Pipedream signature timestamp.") from exc
    if abs(int(time.time()) - signed_at) > SIGNATURE_TOLERANCE_SECONDS:
        raise PipedreamTriggerSignatureError("Expired Pipedream signature.")

    signed_payload = timestamp.encode("utf-8") + b"." + raw_body
    expected = hmac.new(signing_key.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    if not secrets.compare_digest(expected, signature):
        raise PipedreamTriggerSignatureError("Invalid Pipedream signature.")


def _coerce_json_body(raw_body: bytes) -> dict[str, object]:
    try:
        parsed = json.loads((raw_body or b"{}").decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid JSON payload.") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Pipedream trigger payload must be a JSON object.")
    return parsed


def _pipedream_event_from_payload(payload: Mapping[str, object]) -> Mapping[str, object]:
    event = payload.get("event")
    if isinstance(event, Mapping):
        return event
    return payload


def _nested_mapping(root: Mapping[str, object], *keys: str) -> Mapping[str, object] | None:
    current: object = root
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current if isinstance(current, Mapping) else None


def _discord_message_event(payload: Mapping[str, object]) -> Mapping[str, object]:
    event = _pipedream_event_from_payload(payload)
    for key in ("message", "payload", "data"):
        nested = event.get(key)
        if isinstance(nested, Mapping) and (
            _event_value(nested, "content", "message", "text", "body")
            or _event_value(nested, "id", "messageId", "message_id")
        ):
            return nested
    return event


def _event_value(event: Mapping[str, object], *keys: str) -> str:
    for key in keys:
        value = event.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _discord_author(event: Mapping[str, object]) -> tuple[str, str]:
    author_candidates = [
        event.get("author"),
        event.get("user"),
        _nested_mapping(event, "member", "user"),
        _nested_mapping(event, "message", "author"),
        _nested_mapping(event, "payload", "author"),
        _nested_mapping(event, "data", "author"),
    ]
    for author in author_candidates:
        if not isinstance(author, Mapping):
            continue
        author_id = _event_value(author, "id", "userId", "user_id")
        username = _event_value(
            author,
            "global_name",
            "globalName",
            "display_name",
            "displayName",
            "nick",
            "username",
            "name",
        )
        if author_id or username:
            return author_id, username

    member = event.get("member")
    if isinstance(member, Mapping):
        member_name = _event_value(member, "nick", "display_name", "displayName")
        user = member.get("user")
        if member_name and isinstance(user, Mapping):
            return _event_value(user, "id", "userId", "user_id"), member_name

    author_name = _event_value(
        event,
        "author",
        "global_name",
        "globalName",
        "username",
        "display_name",
        "displayName",
        "user",
    )
    return _event_value(event, "authorID", "authorId", "author_id", "userID", "userId", "user_id"), author_name


def _find_recent_discord_outbound(
    agent: PersistentAgent,
    *,
    channel_id: str,
    body: str,
) -> PersistentAgentMessage | None:
    return find_recent_discord_outbound(
        agent,
        channel_id=channel_id,
        body=body,
        source="pipedream_tool",
    )


def _discord_message_body(event: Mapping[str, object]) -> str:
    return _event_value(event, "content", "message", "text", "body")


def _event_list(event: Mapping[str, object], key: str) -> list[object]:
    value = event.get(key)
    return value if isinstance(value, list) else []


def _discord_attachment_items(event: Mapping[str, object]) -> list[object]:
    value = event.get("attachments")
    if isinstance(value, list):
        return value
    if isinstance(value, Mapping):
        if _event_value(value, *DISCORD_ATTACHMENT_URL_KEYS):
            return [value]
        return list(value.values())
    return []


def _discord_attachment_downloads(attachments: Iterable[object]) -> list[dict[str, str]]:
    downloads: list[dict[str, str]] = []
    for attachment in attachments:
        if isinstance(attachment, str):
            url = attachment.strip()
            if url.startswith(("http://", "https://")):
                downloads.append({"url": url})
            continue
        if not isinstance(attachment, Mapping):
            continue

        url = _event_value(attachment, *DISCORD_ATTACHMENT_URL_KEYS)
        if not url:
            continue
        item = {"url": url}
        filename = _event_value(attachment, *DISCORD_ATTACHMENT_FILENAME_KEYS)
        if filename:
            item["filename"] = filename
        content_type = _event_value(attachment, *DISCORD_ATTACHMENT_CONTENT_TYPE_KEYS)
        if content_type:
            item["content_type"] = content_type
        downloads.append(item)
    return downloads


def _normalize_discord_event(
    subscription: PersistentAgentPipedreamTriggerSubscription,
    payload: Mapping[str, object],
) -> tuple[ParsedMessage, str]:
    event = _discord_message_event(payload)
    message_id = _event_value(event, "id", "messageId", "message_id")
    channel_id = _event_value(event, "channelID", "channelId", "channel_id") or subscription.platform_channel
    if channel_id != subscription.platform_channel:
        raise ValueError("Discord event channel does not match this subscription.")
    body = _discord_message_body(event)
    attachments = _discord_attachment_items(event)
    embeds = _event_list(event, "embeds")
    if not message_id or (not body and not attachments and not embeds):
        raise ValueError("Discord message event is missing a message id, content, attachments, or embeds.")

    guild_id = _event_value(event, "guildID", "guildId", "guild_id")
    channel_name = _event_value(event, "channelName", "channel_name") or subscription.platform_channel_name
    guild_name = _event_value(event, "guildName", "guild_name")
    author_id, author_name = _discord_author(event)
    platform_channel_address = _discord_channel_address(guild_id, channel_id)
    conversation_address = _discord_conversation_address(subscription.agent_id, guild_id, channel_id)
    source_label_parts = []
    if author_name:
        source_label_parts.append(author_name)
    if channel_name:
        source_label_parts.append(f"#{channel_name.lstrip('#')}")
    source_label = " in ".join(source_label_parts) if source_label_parts else channel_name or channel_id

    normalized_payload = {
        "source": "pipedream_trigger",
        "source_kind": "discord",
        "source_label": source_label,
        "app_slug": subscription.app_slug,
        "event_type": subscription.event_type,
        "subscription_id": str(subscription.id),
        "deployed_trigger_id": subscription.deployed_trigger_id,
        "discord_message_id": message_id,
        "discord_channel_id": channel_id,
        "discord_channel_name": channel_name,
        "discord_guild_id": guild_id,
        "discord_guild_name": guild_name,
        "discord_author_id": author_id,
        "discord_author_name": author_name,
        "discord_attachments": attachments,
        "discord_embeds": embeds,
        "discord_platform_channel_address": platform_channel_address,
        "discord_conversation_address": conversation_address,
        "pipedream_payload": dict(payload),
    }
    parsed = ParsedMessage(
        sender=platform_channel_address,
        recipient=_discord_agent_address(subscription.agent_id),
        subject=None,
        body=body,
        attachments=_discord_attachment_downloads(attachments),
        raw_payload=normalized_payload,
        msg_channel=CommsChannel.DISCORD.value,
        conversation_address=conversation_address,
    )
    display_name = f"#{channel_name.lstrip('#')}" if channel_name else f"Discord {channel_id}"
    return parsed, display_name


def _discord_event_is_bot_authored(raw_payload: Mapping[str, object]) -> bool:
    pipedream_payload = raw_payload.get("pipedream_payload")
    if not isinstance(pipedream_payload, Mapping):
        return False
    metadata = pipedream_payload.get("author_metadata")
    if isinstance(metadata, Mapping) and metadata.get("bot") is True:
        return True
    return bool(_event_value(pipedream_payload, "webhookId", "webhookID", "webhook_id"))


def _merge_discord_echo_into_outbound(
    message: PersistentAgentMessage,
    *,
    parsed: ParsedMessage,
    display_name: str,
) -> PersistentAgentMessage:
    raw_payload = parsed.raw_payload if isinstance(parsed.raw_payload, Mapping) else {}
    channel_id = str(raw_payload.get("discord_channel_id") or "").strip()
    channel_name = str(raw_payload.get("discord_channel_name") or "").strip()
    guild_id = str(raw_payload.get("discord_guild_id") or "").strip()
    platform_channel_address = str(raw_payload.get("discord_platform_channel_address") or "").strip()
    if guild_id and channel_id:
        address = _discord_conversation_address(message.owner_agent_id, guild_id, channel_id)
        if not platform_channel_address:
            platform_channel_address = _discord_channel_address(guild_id, channel_id)
        conversation = _get_or_create_discord_conversation(
            message.owner_agent,
            address=address,
            channel_id=channel_id,
            channel_name=channel_name,
        )
        _ensure_discord_conversation_participants(
            message.owner_agent,
            conversation,
            platform_channel_address=platform_channel_address,
        )
        if message.conversation_id != conversation.id:
            message.conversation = conversation
    next_payload = dict(message.raw_payload or {})
    next_payload.update(
        {
            "discord_message_id": raw_payload.get("discord_message_id", ""),
            "discord_channel_id": raw_payload.get("discord_channel_id", ""),
            "discord_channel_name": raw_payload.get("discord_channel_name", ""),
            "discord_guild_id": raw_payload.get("discord_guild_id", ""),
            "discord_guild_name": raw_payload.get("discord_guild_name", ""),
            "discord_author_id": raw_payload.get("discord_author_id", ""),
            "discord_author_name": raw_payload.get("discord_author_name", ""),
            "discord_attachments": raw_payload.get("discord_attachments", []),
            "discord_platform_channel_address": platform_channel_address,
            "discord_conversation_address": message.conversation.address if message.conversation_id else "",
            "source_label": _discord_channel_source_label(channel_id, channel_name),
            "pipedream_trigger_echo_payload": raw_payload.get("pipedream_payload", {}),
        }
    )
    message.raw_payload = next_payload
    message.latest_status = DeliveryStatus.SENT
    if message.latest_sent_at is None:
        message.latest_sent_at = timezone.now()
    message.save(update_fields=["conversation", "raw_payload", "latest_status", "latest_sent_at"])
    if message.conversation_id and display_name:
        PersistentAgentConversation.objects.filter(id=message.conversation_id).update(display_name=display_name)
    return message


def _upsert_discord_outbound_echo(
    subscription: PersistentAgentPipedreamTriggerSubscription,
    *,
    parsed: ParsedMessage,
    display_name: str,
) -> PersistentAgentMessage | None:
    raw_payload = parsed.raw_payload if isinstance(parsed.raw_payload, Mapping) else {}
    channel_id = str(raw_payload.get("discord_channel_id") or "").strip()
    body = parsed.body or ""
    if not channel_id or not body:
        return None
    recent_outbound = _find_recent_discord_outbound(subscription.agent, channel_id=channel_id, body=body)
    bot_authored = _discord_event_is_bot_authored(raw_payload)
    author_name = str(raw_payload.get("discord_author_name") or "").strip()
    is_named_agent = bool(author_name and author_name == subscription.agent.name)
    if not recent_outbound and not (bot_authored and is_named_agent):
        return None
    if recent_outbound:
        return _merge_discord_echo_into_outbound(recent_outbound, parsed=parsed, display_name=display_name)

    channel_name = str(raw_payload.get("discord_channel_name") or "").strip()
    outbound_payload = {
        "source": "pipedream_tool",
        "source_kind": "discord",
        "app_slug": subscription.app_slug,
        "event_type": subscription.event_type,
        "discord_channel_id": channel_id,
        "discord_channel_name": channel_name,
        "source_label": _discord_channel_source_label(channel_id, channel_name),
        "discord_message_id": raw_payload.get("discord_message_id", ""),
        "discord_guild_id": raw_payload.get("discord_guild_id", ""),
        "discord_guild_name": raw_payload.get("discord_guild_name", ""),
        "discord_author_id": raw_payload.get("discord_author_id", ""),
        "discord_author_name": raw_payload.get("discord_author_name", ""),
        "discord_attachments": raw_payload.get("discord_attachments", []),
        "discord_platform_channel_address": raw_payload.get("discord_platform_channel_address", ""),
        "discord_conversation_address": raw_payload.get("discord_conversation_address", ""),
        "pipedream_trigger_echo_payload": raw_payload.get("pipedream_payload", {}),
    }
    return _create_discord_outbound_message(
        subscription.agent,
        channel_id=channel_id,
        body=body,
        conversation_address=parsed.conversation_address or parsed.sender,
        platform_channel_address=str(raw_payload.get("discord_platform_channel_address") or parsed.sender),
        channel_name=channel_name,
        raw_payload=outbound_payload,
    )


def ingest_trigger_delivery(
    subscription: PersistentAgentPipedreamTriggerSubscription,
    raw_body: bytes,
) -> dict[str, object]:
    if subscription.app_slug != DISCORD_APP_SLUG or subscription.event_type != DISCORD_MESSAGE_EVENT_TYPE:
        raise ValueError("Unsupported Pipedream trigger subscription.")

    payload = _coerce_json_body(raw_body)
    parsed, display_name = _normalize_discord_event(subscription, payload)
    outbound_echo = _upsert_discord_outbound_echo(subscription, parsed=parsed, display_name=display_name)
    if outbound_echo:
        subscription.record_event()
        return {
            "message_id": str(outbound_echo.id),
            "conversation_id": str(outbound_echo.conversation_id) if outbound_echo.conversation_id else "",
            "ignored": True,
            "outbound_echo": True,
        }

    _ensure_discord_agent_endpoint(subscription.agent)
    info = ingest_inbound_message(
        CommsChannel.DISCORD,
        parsed,
        filespace_import_mode="sync",
        trigger_processing=False,
    )
    if info.message.conversation_id and display_name:
        PersistentAgentConversation.objects.filter(id=info.message.conversation_id).update(display_name=display_name)
    debounce_result = schedule_discord_inbound_processing(str(subscription.agent_id))
    subscription.record_event()
    return {
        "message_id": str(info.message.id),
        "conversation_id": str(info.message.conversation_id) if info.message.conversation_id else "",
        "debounced": bool(debounce_result.get("debounced")),
        "debounce_seconds": debounce_result.get("debounce_seconds", 0),
    }
