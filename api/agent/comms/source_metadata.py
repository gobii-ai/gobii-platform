from collections.abc import Mapping
from typing import Any


def get_message_source_metadata(raw_payload: object) -> tuple[str | None, str | None]:
    """Return normalized source metadata for a persisted message payload."""

    if not isinstance(raw_payload, Mapping):
        return None, None

    source_kind = raw_payload.get("source_kind") or raw_payload.get("sourceKind")
    source_label = raw_payload.get("source_label") or raw_payload.get("sourceLabel")

    normalized_kind = str(source_kind).strip().lower() if isinstance(source_kind, str) else None
    normalized_label = str(source_label).strip() if isinstance(source_label, str) else None

    if normalized_kind == "webhook" and not normalized_label:
        webhook_name = raw_payload.get("webhook_name") or raw_payload.get("webhookName")
        if isinstance(webhook_name, str) and webhook_name.strip():
            normalized_label = webhook_name.strip()

    return normalized_kind or None, normalized_label or None


def get_webhook_timeline_metadata(raw_payload: object) -> dict[str, Any] | None:
    """Return timeline metadata for webhook-originated messages."""

    source_kind, _ = get_message_source_metadata(raw_payload)
    if source_kind != "webhook" or not isinstance(raw_payload, Mapping):
        return None

    payload_kind = raw_payload.get("payload_kind")
    if not isinstance(payload_kind, str) or not payload_kind.strip():
        if raw_payload.get("json_payload") is not None:
            payload_kind = "json"
        elif isinstance(raw_payload.get("form_payload"), Mapping) and raw_payload.get("form_payload"):
            payload_kind = "form"
        elif isinstance(raw_payload.get("text_payload"), str) and raw_payload.get("text_payload").strip():
            payload_kind = "text"
        else:
            payload_kind = "empty"
    else:
        payload_kind = payload_kind.strip().lower()

    payload_value: Any = None
    if payload_kind == "json":
        payload_value = raw_payload.get("json_payload")
    elif payload_kind == "form":
        form_payload = raw_payload.get("form_payload")
        payload_value = dict(form_payload) if isinstance(form_payload, Mapping) else None

    content_type = raw_payload.get("content_type")
    method = raw_payload.get("method")
    path = raw_payload.get("path")
    query_params = raw_payload.get("query_params")

    return {
        "contentType": str(content_type).strip() if isinstance(content_type, str) and content_type.strip() else None,
        "method": str(method).strip() if isinstance(method, str) and method.strip() else None,
        "path": str(path).strip() if isinstance(path, str) and path.strip() else None,
        "queryParams": dict(query_params) if isinstance(query_params, Mapping) else {},
        "payloadKind": payload_kind,
        "payload": payload_value,
    }
