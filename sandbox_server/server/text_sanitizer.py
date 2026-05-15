import re

__all__ = ["decode_unicode_escapes"]


_UNICODE_ESCAPE_RE = re.compile(r"\\u([0-9a-fA-F]{4})")
_UNICODE_ESCAPE_LONG_RE = re.compile(r"\\U([0-9a-fA-F]{8})")
_HEX_ESCAPE_RE = re.compile(r"\\x([0-9a-fA-F]{2})")

_COMMON_ESCAPES_MAP = {
    "\\n": "\n",
    "\\r": "\r",
    "\\t": "\t",
    '\\"': '"',
    "\\'": "'",
}
_ESCAPED_BACKSLASH_SENTINEL = "\uE000GOBII_BACKSLASH\uE000"

_COMMON_ESCAPES_RE = re.compile(r"\\n|\\r|\\t|\\\"|\\'")


def _decode_long_escape(match: re.Match[str]) -> str:
    try:
        code_point = int(match.group(1), 16)
        if 0xD800 <= code_point <= 0xDFFF:
            return match.group(0)
        return chr(code_point)
    except (ValueError, OverflowError):
        return match.group(0)


def _decode_hex_escape(match: re.Match[str]) -> str:
    try:
        code_point = int(match.group(1), 16)
        return chr(code_point)
    except (ValueError, OverflowError):
        return match.group(0)


def decode_unicode_escapes(value: str | None) -> str:
    """
    Decode JSON/Python-style escape sequences in text.

    This copy keeps the standalone sandbox package independent from the Django
    app package while preserving the PDF rendering behavior used by agent tools.
    """
    if not isinstance(value, str):
        return ""

    text = value
    text = text.replace("\\\\", _ESCAPED_BACKSLASH_SENTINEL)
    text = _COMMON_ESCAPES_RE.sub(lambda m: _COMMON_ESCAPES_MAP[m.group(0)], text)
    text = _UNICODE_ESCAPE_LONG_RE.sub(_decode_long_escape, text)
    text = _HEX_ESCAPE_RE.sub(_decode_hex_escape, text)

    result = []
    i = 0
    while i < len(text):
        match = _UNICODE_ESCAPE_RE.match(text, i)
        if match:
            code_point = int(match.group(1), 16)
            if 0xD800 <= code_point <= 0xDBFF:
                next_match = _UNICODE_ESCAPE_RE.match(text, match.end())
                if next_match:
                    next_code = int(next_match.group(1), 16)
                    if 0xDC00 <= next_code <= 0xDFFF:
                        combined = 0x10000 + (
                            ((code_point - 0xD800) << 10) | (next_code - 0xDC00)
                        )
                        try:
                            result.append(chr(combined))
                            i = next_match.end()
                            continue
                        except (ValueError, OverflowError):
                            pass
            if 0xD800 <= code_point <= 0xDFFF:
                result.append(match.group(0))
                i = match.end()
                continue

            try:
                result.append(chr(code_point))
            except (ValueError, OverflowError):
                result.append(match.group(0))
            i = match.end()
        else:
            result.append(text[i])
            i += 1

    return "".join(result).replace(_ESCAPED_BACKSLASH_SENTINEL, "\\")
