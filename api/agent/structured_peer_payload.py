import json
from typing import Any, Mapping


STRUCTURED_PEER_PAYLOAD_KEY = "structured_payload"
STRUCTURED_PEER_PAYLOAD_MAX_BYTES = 64 * 1024
PEER_DM_SOURCE = "agent_peer_dm"

StructuredPeerPayload = dict[str, Any] | list[Any]


def canonicalize_structured_peer_payload(payload: Any) -> str:
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


def get_structured_peer_payload(raw_payload: Any) -> StructuredPeerPayload | None:
    if not isinstance(raw_payload, Mapping) or raw_payload.get("_source") != PEER_DM_SOURCE:
        return None
    payload = raw_payload.get(STRUCTURED_PEER_PAYLOAD_KEY)
    return payload if isinstance(payload, (dict, list)) else None
