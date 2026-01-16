import json
from typing import Any


def decode_embedded_json_strings(node: Any, *, max_bytes: int = 500_000, depth: int = 0, max_depth: int = 4) -> Any:
    """Recursively decode JSON-looking strings into structured objects.

    Used to turn stringified arrays/objects (e.g., Bright Data `result` fields)
    back into dicts/lists so downstream JSON queries work as expected.
    """
    if depth > max_depth:
        return node

    if isinstance(node, str):
        if len(node) > max_bytes:
            return node
        text = node.lstrip()
        if text.startswith("{") or text.startswith("["):
            try:
                parsed = json.loads(node)
            except json.JSONDecodeError:
                return node
            if isinstance(parsed, (dict, list)):
                return decode_embedded_json_strings(
                    parsed, max_bytes=max_bytes, depth=depth + 1, max_depth=max_depth
                )
        return node

    if isinstance(node, list):
        return [
            decode_embedded_json_strings(item, max_bytes=max_bytes, depth=depth + 1, max_depth=max_depth)
            for item in node
        ]

    if isinstance(node, dict):
        return {
            key: decode_embedded_json_strings(value, max_bytes=max_bytes, depth=depth + 1, max_depth=max_depth)
            for key, value in node.items()
        }

    return node
