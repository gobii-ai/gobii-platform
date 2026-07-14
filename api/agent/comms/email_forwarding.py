import re


# Forward markers may be quoted when a user replies to an already-forwarded email.
_QUOTE_PREFIX = r"\s*(?:>\s*)*"

FORWARD_ONLY_MARKERS = [
    r"^" + _QUOTE_PREFIX + r"Begin forwarded message:",
    r"^" + _QUOTE_PREFIX + r"-{2,}\s*Forwarded message\s*-{2,}$",
]
AMBIGUOUS_QUOTE_MARKERS = [
    r"^" + _QUOTE_PREFIX + r"-----Original Message-----$",
    r"^" + _QUOTE_PREFIX + r"-{3,}\s*Original Message\s*-{3,}$",
    r"^" + _QUOTE_PREFIX + r"_{10,}$",
]
FORWARD_ONLY_MARKERS_RE = re.compile(
    "|".join(FORWARD_ONLY_MARKERS),
    re.IGNORECASE | re.MULTILINE,
)
AMBIGUOUS_QUOTE_MARKERS_RE = re.compile(
    "|".join(AMBIGUOUS_QUOTE_MARKERS),
    re.IGNORECASE | re.MULTILINE,
)
SUBJECT_FWD_RE = re.compile(r"^\s*(fwd?|fw|wg|tr|rv)\s*:", re.IGNORECASE)
SUBJECT_REPLY_RE = re.compile(r"^\s*re\s*:", re.IGNORECASE)
FORWARDED_HEADER_LINE_RE = re.compile(
    r"^" + _QUOTE_PREFIX + r"(From|Date|Sent|Subject|To):\s*.+",
    re.IGNORECASE | re.MULTILINE,
)


def _find_header_block_line(lines: list[str]) -> int | None:
    header_matches = []
    for index, line in enumerate(lines):
        match = FORWARDED_HEADER_LINE_RE.match(line)
        if not match:
            continue
        header_type = match.group(1).lower()
        if header_type == "sent":
            header_type = "date"
        header_matches.append((index, header_type))

    left = 0
    for right, (right_line, _) in enumerate(header_matches):
        while right_line - header_matches[left][0] >= 8:
            left += 1
        unique_headers = {
            header_type
            for _, header_type in header_matches[left:right + 1]
        }
        if len(unique_headers) >= 3:
            return header_matches[left][0]
    return None


def has_forwarded_header_block(text: str) -> bool:
    """Return whether text contains a clustered forwarded-email header block."""
    return bool(text) and _find_header_block_line(text.split("\n")) is not None


def is_forward_like(subject: str, body_text: str, attachments: list[dict]) -> bool:
    if any(
        (attachment.get("ContentType", "") or "").lower() == "message/rfc822"
        for attachment in (attachments or [])
    ):
        return True
    if SUBJECT_FWD_RE.search(subject or ""):
        return True
    if FORWARD_ONLY_MARKERS_RE.search(body_text or ""):
        return True

    # Outlook uses these markers for replies too, so the subject must disambiguate them.
    if SUBJECT_REPLY_RE.search(subject or ""):
        return False
    if AMBIGUOUS_QUOTE_MARKERS_RE.search(body_text or ""):
        return True
    return has_forwarded_header_block(body_text)


def _find_header_block_start(text: str) -> int | None:
    if not text:
        return None

    lines = text.split("\n")
    line_starts = []
    position = 0
    for line in lines:
        line_starts.append(position)
        position += len(line) + 1

    header_block_line = _find_header_block_line(lines)
    return line_starts[header_block_line] if header_block_line is not None else None


def extract_forward_sections(body_text: str) -> tuple[str, str]:
    """Split a forward into its sender preamble and forwarded message block."""
    if not body_text:
        return "", ""

    starts = []
    forward_marker = FORWARD_ONLY_MARKERS_RE.search(body_text)
    if forward_marker:
        starts.append(forward_marker.start())
    ambiguous_marker = AMBIGUOUS_QUOTE_MARKERS_RE.search(body_text)
    if ambiguous_marker:
        starts.append(ambiguous_marker.start())
    header_start = _find_header_block_start(body_text)
    if header_start is not None:
        starts.append(header_start)
    if not starts:
        return body_text.strip(), ""

    split_at = min(starts)
    return body_text[:split_at].strip(), body_text[split_at:].strip()
