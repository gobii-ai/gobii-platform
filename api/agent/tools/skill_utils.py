"""Shared helpers for skill metadata."""

from typing import Any


def normalize_skill_tool_ids(raw_tools: Any) -> tuple[str, ...]:
    """Return unique, trimmed canonical tool IDs in original order."""
    if not isinstance(raw_tools, list):
        return ()

    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_tools:
        if not isinstance(item, str):
            continue
        tool_id = item.strip()
        if not tool_id or tool_id in seen:
            continue
        seen.add(tool_id)
        normalized.append(tool_id)
    return tuple(normalized)
