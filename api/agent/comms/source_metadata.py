from collections.abc import Mapping


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
