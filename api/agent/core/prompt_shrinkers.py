import json
import logging
import re
from typing import List


logger = logging.getLogger(__name__)

CODE_FENCE_RE = re.compile(r"^```")
TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")
BRACKET_TIMESTAMP_RE = re.compile(r"\[\d{4}-\d{2}-\d{2}T")
UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
URL_RE = re.compile(r"https?://\S+")
HEADER_RE = re.compile(r"^#{1,6}\s")
BULLET_RE = re.compile(r"^(?:[-*]|\d+\.)\s")
SQL_KEYWORDS_RE = re.compile(
    r"\b(select|insert|update|delete|from|where|join|create|table|drop|alter|group by|order by)\b",
    re.IGNORECASE,
)

PROTECTED_TOKEN_RE = re.compile(
    r"__tool_results|__agent_config|__kanban_cards|"
    r"\$\[[^\]]+\]|"
    r"result_id|result_json|result_text|analysis_json|"
    r"task_id|query|prompt|sql|to_address|to_number|subject|method|status|error|"
    r"sqlite_batch|json_extract|json_each|csv_parse|csv_headers|"
    r"will_continue_work|CONTINUE_WORK_SIGNAL",
    re.IGNORECASE,
)

KEY_LINE_RE = re.compile(
    r"\"?(url|query|prompt|sql|body|subject|to_address|to_number|method|status|error|message|task_id)\"?\s*[:=]",
    re.IGNORECASE,
)

HEAD_LINES = 4
TAIL_LINES = 4
TRUNCATION_TEMPLATE = "[{count} LINES TRUNCATED]"
MIN_RATIO = 0.01
MAX_JSON_PRETTY_BYTES = 200_000


def structured_shrinker(text: str, k: float) -> str:
    """Shrink text deterministically while preserving structure and key tokens."""
    if not text:
        return text
    text = _maybe_pretty_json(text)
    k = max(MIN_RATIO, min(k, 1.0))
    if k >= 0.99:
        return text

    raw_lines = text.splitlines()
    if not raw_lines:
        return text

    total_bytes = len(text.encode("utf-8"))
    target_bytes = max(1, int(total_bytes * k))
    if target_bytes >= total_bytes:
        return text

    blocks = _build_blocks(raw_lines)
    line_to_block = _build_line_map(blocks, len(raw_lines))

    required_blocks = _required_block_indexes(blocks, line_to_block, len(raw_lines))
    keep_blocks = set(required_blocks)

    required_bytes = sum(blocks[idx]["bytes"] for idx in keep_blocks)
    if required_bytes >= total_bytes:
        return text
    budget_left = max(0, target_bytes - required_bytes)

    candidates = [
        idx for idx in range(len(blocks)) if idx not in keep_blocks
    ]
    candidates.sort(
        key=lambda idx: (-blocks[idx]["score"], blocks[idx]["bytes"])
    )

    for idx in candidates:
        block_bytes = blocks[idx]["bytes"]
        if block_bytes <= budget_left:
            keep_blocks.add(idx)
            budget_left -= block_bytes

    output_lines: List[str] = []
    dropped_lines = 0
    total_dropped = 0
    for idx, block in enumerate(blocks):
        if idx in keep_blocks:
            if dropped_lines:
                output_lines.append(TRUNCATION_TEMPLATE.format(count=dropped_lines))
                total_dropped += dropped_lines
                dropped_lines = 0
            output_lines.extend(block["lines"])
        else:
            dropped_lines += len(block["lines"])

    if dropped_lines:
        output_lines.append(TRUNCATION_TEMPLATE.format(count=dropped_lines))
        total_dropped += dropped_lines

    output = "\n".join(output_lines)
    if total_dropped:
        logger.debug(
            "structured_shrinker dropped %s lines (kept %s/%s)",
            total_dropped,
            len(raw_lines) - total_dropped,
            len(raw_lines),
        )
    return output


def _maybe_pretty_json(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return text
    if "```" in stripped:
        return text
    if len(stripped.encode("utf-8")) > MAX_JSON_PRETTY_BYTES:
        return text
    if not (
        (stripped.startswith("{") and stripped.endswith("}"))
        or (stripped.startswith("[") and stripped.endswith("]"))
    ):
        return text
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return _heuristic_json_newlines(stripped, text)
    return json.dumps(payload, indent=2, ensure_ascii=True)


def _heuristic_json_newlines(stripped: str, original: str) -> str:
    formatted = stripped
    formatted = re.sub(r"\},\s*\{", "},\n{", formatted)
    formatted = re.sub(r"\],\s*\[", "],\n[", formatted)
    formatted = re.sub(r"\],\s*\{", "],\n{", formatted)
    formatted = re.sub(r"\},\s*\[", "},\n[", formatted)
    formatted = re.sub(r",(?=\s*\"[^\"]{1,80}\"\s*:)", ",\n", formatted)
    if formatted == stripped:
        return original
    return formatted


def _build_blocks(lines: List[str]) -> List[dict]:
    blocks: List[dict] = []
    current: List[str] = []
    in_fence = False
    start_index = 0

    for idx, line in enumerate(lines):
        is_fence = CODE_FENCE_RE.match(line.strip()) is not None
        if in_fence:
            current.append(line)
            if is_fence:
                blocks.append(_finalize_block(current, start_index, idx, True))
                current = []
                in_fence = False
            continue

        if is_fence:
            in_fence = True
            start_index = idx
            current = [line]
            continue

        blocks.append(_finalize_block([line], idx, idx, False))

    if current:
        blocks.append(_finalize_block(current, start_index, start_index + len(current) - 1, in_fence))

    return blocks


def _finalize_block(lines: List[str], start: int, end: int, is_fence: bool) -> dict:
    scores = [_line_score(line) for line in lines]
    has_protected = any(_is_protected_line(line) for line in lines)
    block_score = max(scores) if scores else 0

    if is_fence and block_score < 30:
        block_score = 30

    block_bytes = _estimate_block_bytes(lines)
    return {
        "lines": list(lines),
        "start": start,
        "end": end,
        "score": block_score,
        "bytes": block_bytes,
        "protected": has_protected,
    }


def _estimate_block_bytes(lines: List[str]) -> int:
    if not lines:
        return 0
    byte_total = sum(len(line.encode("utf-8")) for line in lines)
    byte_total += max(0, len(lines) - 1)
    return byte_total


def _build_line_map(blocks: List[dict], total_lines: int) -> List[int]:
    line_map = [-1] * total_lines
    for idx, block in enumerate(blocks):
        for line_idx in range(block["start"], block["end"] + 1):
            if 0 <= line_idx < total_lines:
                line_map[line_idx] = idx
    return line_map


def _required_block_indexes(blocks: List[dict], line_to_block: List[int], total_lines: int) -> List[int]:
    required: List[int] = []
    head = range(min(HEAD_LINES, total_lines))
    tail = range(max(0, total_lines - TAIL_LINES), total_lines)

    for line_idx in list(head) + list(tail):
        block_idx = line_to_block[line_idx]
        if block_idx >= 0:
            required.append(block_idx)

    for idx, block in enumerate(blocks):
        if block.get("protected"):
            required.append(idx)

    return sorted(set(required))


def _line_score(line: str) -> int:
    stripped = line.strip()
    if not stripped:
        return 0
    if _is_protected_line(line):
        return 100
    if TIMESTAMP_RE.search(line) or BRACKET_TIMESTAMP_RE.search(line):
        return 80
    if HEADER_RE.match(stripped):
        return 70
    if BULLET_RE.match(stripped) or stripped.startswith("|"):
        return 50
    if KEY_LINE_RE.search(line):
        return 60
    if SQL_KEYWORDS_RE.search(line):
        return 45
    if URL_RE.search(line) or UUID_RE.search(line):
        return 40
    return 10


def _is_protected_line(line: str) -> bool:
    return bool(PROTECTED_TOKEN_RE.search(line))
