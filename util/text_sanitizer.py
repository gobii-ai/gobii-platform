"""Utilities for cleaning outbound message content."""

import unicodedata

__all__ = ["strip_control_chars"]


_ALLOWABLE_CONTROL_CHARS = {"\n", "\r", "\t"}
_CONTROL_CHAR_SUBSTITUTIONS = {"\u0019": "'"}


def strip_control_chars(value: str | None) -> str:
    """Remove disallowed control characters from outbound message bodies."""
    if not isinstance(value, str):
        return ""
    text = value.translate(str.maketrans(_CONTROL_CHAR_SUBSTITUTIONS))
    return "".join(
        ch for ch in text
        if (unicodedata.category(ch)[0] != "C") or ch in _ALLOWABLE_CONTROL_CHARS
    )

