"""Utilities for cleaning outbound message content."""

import re
import unicodedata

__all__ = ["strip_control_chars", "strip_markdown_for_sms", "normalize_whitespace"]


_ALLOWABLE_CONTROL_CHARS = {"\n", "\r", "\t"}
_SEQUENCE_SUBSTITUTIONS = (
    ("\x00b9", "'"),
    ("\x00B9", "'"),
    ("\u00019", "'"),  # occasional malformed apostrophe sequence
)
_CONTROL_CHAR_SUBSTITUTIONS = {
    "\u0013": "-",  # device control 3 sometimes used in lieu of a dash
    "\u0014": "-",  # device control 4 shows up where an em dash was intended
    "\u0019": "'",  # substitute apostrophe-like control character
}
_CONTROL_HEX_SEQUENCE_RE = re.compile(r"([\u0000-\u0001])([0-9a-fA-F]{2})")
_TRANSLATION_TABLE = str.maketrans(_CONTROL_CHAR_SUBSTITUTIONS)

def _decode_control_hex(match: re.Match[str]) -> str:
    high = ord(match.group(1))
    low = int(match.group(2), 16)
    return chr((high << 8) | low)

def strip_control_chars(value: str | None) -> str:
    """Remove disallowed control characters from outbound message bodies."""
    if not isinstance(value, str):
        return ""
    text = value
    for needle, replacement in _SEQUENCE_SUBSTITUTIONS:
        text = text.replace(needle, replacement)

    text = _CONTROL_HEX_SEQUENCE_RE.sub(_decode_control_hex, text)
    text = text.translate(_TRANSLATION_TABLE)
    return "".join(
        ch for ch in text
        if (unicodedata.category(ch)[0] != "C") or ch in _ALLOWABLE_CONTROL_CHARS
    )


# Patterns for markdown stripping in SMS
_MARKDOWN_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")  # **bold**
_MARKDOWN_ITALIC_STAR_RE = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")  # *italic*
_MARKDOWN_BOLD_UNDER_RE = re.compile(r"__(.+?)__")  # __bold__
_MARKDOWN_ITALIC_UNDER_RE = re.compile(r"(?<!_)_(?!_)(.+?)(?<!_)_(?!_)")  # _italic_
_MARKDOWN_CODE_RE = re.compile(r"`([^`]+)`")  # `code`
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")  # [text](url)
_MARKDOWN_HEADER_RE = re.compile(r"^#{1,6}\s*", re.MULTILINE)  # # Header


def strip_markdown_for_sms(value: str | None) -> str:
    """
    Strip markdown formatting from SMS message bodies.

    Converts markdown to plain text:
    - **bold** or __bold__ → bold
    - *italic* or _italic_ → italic
    - `code` → code
    - [text](url) → text (url)
    - # Header → Header
    """
    if not isinstance(value, str):
        return ""

    text = value

    # Order matters: bold before italic to avoid partial matches
    text = _MARKDOWN_BOLD_RE.sub(r"\1", text)
    text = _MARKDOWN_BOLD_UNDER_RE.sub(r"\1", text)
    text = _MARKDOWN_ITALIC_STAR_RE.sub(r"\1", text)
    text = _MARKDOWN_ITALIC_UNDER_RE.sub(r"\1", text)
    text = _MARKDOWN_CODE_RE.sub(r"\1", text)
    text = _MARKDOWN_LINK_RE.sub(r"\1 (\2)", text)
    text = _MARKDOWN_HEADER_RE.sub("", text)

    return text


# Pattern for excessive newlines
_EXCESSIVE_NEWLINES_RE = re.compile(r"\n{3,}")


def normalize_whitespace(value: str | None) -> str:
    """
    Normalize whitespace in message bodies.

    - Collapses 3+ consecutive newlines to 2 (preserves paragraph breaks)
    - Strips trailing whitespace from each line
    """
    if not isinstance(value, str):
        return ""

    # Collapse excessive newlines (3+ → 2)
    text = _EXCESSIVE_NEWLINES_RE.sub("\n\n", value)

    # Strip trailing whitespace from each line
    lines = [line.rstrip() for line in text.split("\n")]
    return "\n".join(lines)
