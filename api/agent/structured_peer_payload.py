import json
from typing import Any, Mapping


STRUCTURED_PEER_PAYLOAD_KEY = "structured_payload"
STRUCTURED_PEER_PAYLOAD_MAX_BYTES = 64 * 1024

StructuredPeerPayload = dict[str, Any] | list[Any]


def canonicalize_structured_peer_payload(payload: StructuredPeerPayload) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def validate_structured_peer_payload(value: Any) -> StructuredPeerPayload | None:
    if value is None:
        return None
    if not isinstance(value, (dict, list)):
        raise ValueError("structured_payload must be a JSON object or array.")

    try:
        serialized = canonicalize_structured_peer_payload(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("structured_payload must contain valid JSON values.") from exc

    if len(serialized.encode("utf-8")) > STRUCTURED_PEER_PAYLOAD_MAX_BYTES:
        raise ValueError(
            "structured_payload exceeds the 64 KB limit. "
            "Use an attached file for larger datasets."
        )
    return value


def structured_peer_payload_has_content(payload: StructuredPeerPayload | None) -> bool:
    return bool(payload)


def get_structured_peer_payload(raw_payload: Any) -> StructuredPeerPayload | None:
    if not isinstance(raw_payload, Mapping):
        return None
    payload = raw_payload.get(STRUCTURED_PEER_PAYLOAD_KEY)
    if isinstance(payload, (dict, list)):
        return payload
    return None


def format_structured_peer_payload(payload: StructuredPeerPayload) -> str:
    return json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True)
