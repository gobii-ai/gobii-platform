"""Shared helpers for email threading metadata and reply-target resolution."""

import re
from typing import Any

from ...models import CommsChannel, PersistentAgentCommsEndpoint, PersistentAgentMessage

_EMAIL_REFERENCE_ID_RE = re.compile(r"<[^>]+>")


def normalize_email_address(address: str | None) -> str:
    normalized = PersistentAgentCommsEndpoint.normalize_address(CommsChannel.EMAIL, address or "")
    return normalized or ""


def get_message_raw_payload(message: PersistentAgentMessage) -> dict[str, Any]:
    return message.raw_payload if isinstance(message.raw_payload, dict) else {}


def get_message_rfc_message_id(message: PersistentAgentMessage) -> str:
    raw_payload = get_message_raw_payload(message)
    candidate_values = [raw_payload.get("message_id")]
    headers = raw_payload.get("headers")
    if isinstance(headers, dict):
        candidate_values.extend([
            headers.get("MessageID"),
            headers.get("Message-ID"),
            headers.get("Message-Id"),
            headers.get("message-id"),
        ])

    for candidate in candidate_values:
        value = str(candidate or "").strip()
        if value:
            return value
    return ""


def get_message_channel(message: PersistentAgentMessage) -> str:
    if message.from_endpoint_id and message.from_endpoint:
        return message.from_endpoint.channel
    if message.conversation_id and message.conversation:
        return message.conversation.channel
    return ""


def get_message_contact_address(message: PersistentAgentMessage) -> str:
    if message.conversation_id and message.conversation and message.conversation.address:
        return normalize_email_address(message.conversation.address)
    if message.is_outbound and message.to_endpoint_id and message.to_endpoint:
        return normalize_email_address(message.to_endpoint.address)
    if not message.is_outbound and message.from_endpoint_id and message.from_endpoint:
        return normalize_email_address(message.from_endpoint.address)
    return ""


def split_email_reference_ids(raw_references: str) -> list[str]:
    value = str(raw_references or "").strip()
    if not value:
        return []

    matches = _EMAIL_REFERENCE_ID_RE.findall(value)
    if matches:
        return matches

    return [part for part in value.split() if part]


def get_message_references(message: PersistentAgentMessage) -> list[str]:
    raw_payload = get_message_raw_payload(message)
    candidate_values = [raw_payload.get("references")]
    headers = raw_payload.get("headers")
    if isinstance(headers, dict):
        candidate_values.extend([
            headers.get("References"),
            headers.get("references"),
        ])

    references: list[str] = []
    seen: set[str] = set()
    for candidate in candidate_values:
        for reference_id in split_email_reference_ids(str(candidate or "")):
            normalized = reference_id.strip()
            if normalized and normalized not in seen:
                references.append(normalized)
                seen.add(normalized)
    return references


def build_reply_headers(parent_message: PersistentAgentMessage | None) -> dict[str, str]:
    if not parent_message:
        return {}

    parent_message_id = get_message_rfc_message_id(parent_message)
    if not parent_message_id:
        return {}

    references = get_message_references(parent_message)
    if parent_message_id not in references:
        references.append(parent_message_id)

    headers = {"In-Reply-To": parent_message_id}
    if references:
        headers["References"] = " ".join(references)
    return headers
