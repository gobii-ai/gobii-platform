import re
from typing import Optional


DEFAULT_HEAD_WEIGHT = 0.45
DEFAULT_TAIL_WEIGHT = 0.35
DEFAULT_TRIM_RATIO = 0.15
DEFAULT_MIN_LINE_LEN = 20
DEFAULT_SEPARATOR = "\n\n[...]\n\n"


def barbell_focus(
    text: str,
    target_bytes: int,
    *,
    head_weight: float = DEFAULT_HEAD_WEIGHT,
    tail_weight: float = DEFAULT_TAIL_WEIGHT,
    trim_ratio: float = DEFAULT_TRIM_RATIO,
    min_line_len: int = DEFAULT_MIN_LINE_LEN,
    separator: str = DEFAULT_SEPARATOR,
) -> Optional[str]:
    """Focus a long document by trimming junk and sampling head/middle/tail."""
    if not text or target_bytes <= 0:
        return None
    stripped = text.strip()
    if not stripped:
        return None

    lines = stripped.split("\n")
    if not lines:
        return None

    def is_junk(line: str) -> bool:
        l = line.strip()
        if not l:
            return True
        if len(l) < min_line_len:
            return True
        if l.count("|") > 3 or l.count("·") > 2:
            return True
        if l.startswith(("©", "Copyright", "Privacy", "Terms")):
            return True
        if re.search(r"\b(Home|About|Contact|Login|Sign Up|Menu|Search)\b", l):
            return True
        return False

    trim_limit = int(len(lines) * trim_ratio)
    start = 0
    while start < trim_limit and is_junk(lines[start]):
        start += 1

    end = len(lines)
    while end > len(lines) - trim_limit and is_junk(lines[end - 1]):
        end -= 1

    content = "\n".join(lines[start:end]).strip()
    if not content:
        content = stripped

    content_bytes = content.encode("utf-8")
    if len(content_bytes) <= target_bytes:
        return content

    separator_bytes = separator.encode("utf-8")
    overhead = len(separator_bytes) * 2
    if target_bytes <= overhead + 10:
        return _truncate_bytes(content, target_bytes)

    available = target_bytes - overhead
    if available <= 0:
        return _truncate_bytes(content, target_bytes)

    head_bytes = int(available * head_weight)
    tail_bytes = int(available * tail_weight)
    mid_bytes = max(available - head_bytes - tail_bytes, 0)

    if head_bytes <= 0 or tail_bytes <= 0:
        return _truncate_bytes(content, target_bytes)

    head = content_bytes[:head_bytes].decode("utf-8", errors="ignore")
    tail = content_bytes[-tail_bytes:].decode("utf-8", errors="ignore")

    segments = [head]
    if mid_bytes > 0:
        mid_start = max((len(content_bytes) // 2) - (mid_bytes // 2), 0)
        middle = content_bytes[mid_start:mid_start + mid_bytes].decode("utf-8", errors="ignore")
        if middle:
            segments.append(middle)
    if tail:
        segments.append(tail)

    focused = separator.join(segments)
    return _truncate_bytes(focused, target_bytes)


def _truncate_bytes(text: str, max_bytes: int) -> str:
    if max_bytes <= 0:
        return ""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")
