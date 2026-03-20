from typing import Any


def build_rejected_attachment_metadata(
    *,
    filename: str,
    channel: str,
    limit_bytes: int | None,
    reason_code: str,
    size_bytes: int | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "filename": (filename or "attachment").strip() or "attachment",
        "limit_bytes": int(limit_bytes) if limit_bytes is not None else None,
        "reason_code": reason_code,
        "channel": channel,
    }
    if size_bytes is not None:
        metadata["size_bytes"] = int(size_bytes)
    return metadata
