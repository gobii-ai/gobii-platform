import time
from typing import Any, Optional


def normalize_timeout(value: Any, *, default: int, maximum: Optional[int] = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    if parsed <= 0:
        parsed = default
    if maximum is not None:
        return min(parsed, maximum)
    return parsed


def monotonic_elapsed_ms(started_at: float) -> int:
    return int(round((time.monotonic() - started_at) * 1000))
