"""Utilities for cleaning outbound message content."""

import re
import unicodedata

__all__ = ["strip_control_chars"]


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


def strip_control_chars(value: str | None) -> str:
    """Remove disallowed control characters from outbound message bodies."""
    if not isinstance(value, str):
        return ""
    text = value
    for needle, replacement in _SEQUENCE_SUBSTITUTIONS:
        text = text.replace(needle, replacement)

    def _decode_control_hex(match: re.Match[str]) -> str:
        high = ord(match.group(1))
        low = int(match.group(2), 16)
        return chr((high << 8) | low)

    text = _CONTROL_HEX_SEQUENCE_RE.sub(_decode_control_hex, text)
    text = text.translate(_TRANSLATION_TABLE)
    return "".join(
        ch for ch in text
        if (unicodedata.category(ch)[0] != "C") or ch in _ALLOWABLE_CONTROL_CHARS
    )
