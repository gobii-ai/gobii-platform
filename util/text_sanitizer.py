"""Utilities for cleaning outbound message content."""

import re
import unicodedata

__all__ = [
    "strip_control_chars",
    "strip_markdown_for_sms",
    "normalize_whitespace",
    "decode_unicode_escapes",
    "strip_llm_artifacts",
    "strip_redundant_blockquote_quotes",
    "normalize_llm_output",
]


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
_TABLE_SEPARATOR_RE = re.compile(
    r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$"
)


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


def _normalize_markdown_tables(value: str) -> str:
    """Collapse blank lines inside markdown tables to keep rows contiguous."""
    if not value:
        return value

    lines = value.split("\n")
    normalized: list[str] = []
    in_code_block = False
    in_table = False
    i = 0

    def is_table_separator(line: str) -> bool:
        return bool(_TABLE_SEPARATOR_RE.match(line))

    def is_table_row(line: str) -> bool:
        stripped = line.strip()
        if not stripped:
            return False
        return "|" in stripped

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("```"):
            in_code_block = not in_code_block
            normalized.append(line)
            i += 1
            continue

        if in_code_block:
            normalized.append(line)
            i += 1
            continue

        if not in_table:
            if is_table_row(line):
                j = i + 1
                while j < len(lines) and not lines[j].strip():
                    j += 1
                if j < len(lines) and is_table_separator(lines[j]):
                    normalized.append(line)
                    normalized.append(lines[j])
                    in_table = True
                    i = j + 1
                    continue
            normalized.append(line)
            i += 1
            continue

        if not stripped:
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines) and is_table_row(lines[j]):
                i = j
                continue
            in_table = False
            normalized.append(line)
            i += 1
            continue

        if is_table_row(line):
            normalized.append(line)
            i += 1
            continue

        in_table = False
        normalized.append(line)
        i += 1

    return "\n".join(normalized)


# Pattern for JSON-style unicode escape sequences (\uXXXX)
_UNICODE_ESCAPE_RE = re.compile(r"\\u([0-9a-fA-F]{4})")
# Pattern for Python-style unicode escape sequences (\UXXXXXXXX)
_UNICODE_ESCAPE_LONG_RE = re.compile(r"\\U([0-9a-fA-F]{8})")
# Pattern for hex escape sequences (\xNN)
_HEX_ESCAPE_RE = re.compile(r"\\x([0-9a-fA-F]{2})")

# Common string escape sequences that LLMs might output literally
# Mapping from escaped sequence to its actual character
_COMMON_ESCAPES_MAP = {
    "\\\\": "\\",   # Escaped backslash
    "\\n": "\n",    # Newline
    "\\r": "\r",    # Carriage return
    "\\t": "\t",    # Tab
    '\\"': '"',     # Escaped double quote
    "\\'": "'",     # Escaped single quote
}

# Single regex pattern matching all common escapes (order by length desc in alternation)
# This ensures we match \\  before \n when we have \\n in the text
_COMMON_ESCAPES_RE = re.compile(r"\\\\|\\n|\\r|\\t|\\\"|\\'")


def _decode_long_escape(match: re.Match[str]) -> str:
    """Decode a single \\UXXXXXXXX escape sequence to its character."""
    try:
        code_point = int(match.group(1), 16)
        return chr(code_point)
    except (ValueError, OverflowError):
        # Return original if invalid
        return match.group(0)


def _decode_hex_escape(match: re.Match[str]) -> str:
    """Decode a single \\xNN escape sequence to its character."""
    try:
        code_point = int(match.group(1), 16)
        return chr(code_point)
    except (ValueError, OverflowError):
        return match.group(0)


def decode_unicode_escapes(value: str | None) -> str:
    """
    Decode JSON/Python-style escape sequences in text.

    LLMs sometimes output literal escape sequences like \\u2014 instead of the
    actual character (em dash). This function converts those sequences to their
    proper unicode characters.

    Handles:
    - \\uXXXX (4-digit hex, e.g., \\u2014 -> —)
    - \\UXXXXXXXX (8-digit hex, e.g., \\U0001F600 -> emoji)
    - \\xNN (2-digit hex, e.g., \\xA9 -> ©)
    - Surrogate pairs (\\uD83D\\uDE00 -> emoji)
    - Common escapes: \\n, \\r, \\t, \\\\, \\", \\'

    Args:
        value: The text potentially containing escape sequences

    Returns:
        Text with escape sequences decoded to actual characters
    """
    if not isinstance(value, str):
        return ""

    text = value

    # First handle common string escapes in a SINGLE pass using regex
    # This is critical to avoid collisions (e.g., \\name becoming \<newline>ame)
    text = _COMMON_ESCAPES_RE.sub(lambda m: _COMMON_ESCAPES_MAP[m.group(0)], text)

    # Handle 8-digit unicode escapes (less common but more specific)
    text = _UNICODE_ESCAPE_LONG_RE.sub(_decode_long_escape, text)

    # Handle 2-digit hex escapes
    text = _HEX_ESCAPE_RE.sub(_decode_hex_escape, text)

    # Handle 4-digit escapes, including surrogate pairs
    # We need to handle surrogate pairs specially since they come in two parts
    result = []
    i = 0
    while i < len(text):
        match = _UNICODE_ESCAPE_RE.match(text, i)
        if match:
            code_point = int(match.group(1), 16)
            # Check if this is a high surrogate (D800-DBFF)
            if 0xD800 <= code_point <= 0xDBFF:
                # Look for a following low surrogate
                next_match = _UNICODE_ESCAPE_RE.match(text, match.end())
                if next_match:
                    next_code = int(next_match.group(1), 16)
                    # Check if it's a low surrogate (DC00-DFFF)
                    if 0xDC00 <= next_code <= 0xDFFF:
                        # Combine surrogate pair into a single code point
                        combined = 0x10000 + (
                            ((code_point - 0xD800) << 10) | (next_code - 0xDC00)
                        )
                        try:
                            result.append(chr(combined))
                            i = next_match.end()
                            continue
                        except (ValueError, OverflowError):
                            pass
            # Not a surrogate pair or failed to combine, just decode normally
            try:
                result.append(chr(code_point))
            except (ValueError, OverflowError):
                result.append(match.group(0))
            i = match.end()
        else:
            result.append(text[i])
            i += 1

    return "".join(result)


# Patterns for stripping LLM reasoning/tool call artifacts that leak into output
# These patterns match XML-style tags that some LLMs output when confused about tool calls
_LLM_THINKING_TAG_RE = re.compile(r"</?think(?:ing)?>", re.IGNORECASE)
_LLM_ARG_TAGS_RE = re.compile(
    r"<arg_(?:key|value)>[^<]*</arg_(?:key|value)>|<arg_(?:key|value)>[^<]*$",
    re.IGNORECASE,
)
# Match trailing incomplete tool call syntax: </think>, <arg_key>..., etc.
_LLM_TRAILING_ARTIFACTS_RE = re.compile(
    r"</think>.*$|<arg_\w+>.*$|<function_call>.*$|<tool_call>.*$",
    re.IGNORECASE | re.DOTALL,
)


def strip_llm_artifacts(value: str | None) -> str:
    """
    Strip LLM reasoning and tool call artifacts from message content.

    Some LLMs occasionally leak internal reasoning tags or malformed tool call
    syntax into their output. This function removes:
    - <think>...</think> or </think> tags
    - <arg_key>...</arg_key> and <arg_value>...</arg_value> patterns
    - Trailing incomplete tool call fragments

    Args:
        value: Text potentially containing LLM artifacts

    Returns:
        Cleaned text with artifacts removed
    """
    if not isinstance(value, str):
        return ""

    text = value

    # Remove trailing artifacts first (most common case - incomplete tool calls at end)
    text = _LLM_TRAILING_ARTIFACTS_RE.sub("", text)

    # Remove any remaining thinking tags
    text = _LLM_THINKING_TAG_RE.sub("", text)

    # Remove arg key/value tags
    text = _LLM_ARG_TAGS_RE.sub("", text)

    return text.strip()


# Quote characters that might wrap blockquote content redundantly
# Includes straight quotes, smart quotes, and various international quotation marks
_OPENING_QUOTES = {'"', "\u201c", "\u201e", "\u00ab", "\u2039", "\u2018", "'"}
_CLOSING_QUOTES = {'"', "\u201c", "\u201d", "\u00bb", "\u203a", "\u2019", "'"}
_QUOTE_PAIRS = {
    '"': '"',           # straight double
    "\u201c": "\u201d",  # smart double " → "
    "\u201e": "\u201d",  # German/Polish „ → "
    "\u00ab": "\u00bb",  # guillemets « → »
    "\u2039": "\u203a",  # single guillemets ‹ → ›
    "'": "'",           # straight single
    "\u2018": "\u2019",  # smart single ' → '
}


def strip_redundant_blockquote_quotes(value: str | None) -> str:
    """
    Strip redundant quotation marks from markdown blockquotes.

    When LLMs write blockquotes like:
        > "The problem with sandboxing..."

    The blockquote styling (left border, italic) already indicates it's a quote,
    so the literal "..." marks are redundant. This function removes them.

    Handles:
    - Single-line blockquotes: > "text"
    - Multi-line blockquotes where first line starts and last line ends with quotes
    - Various quote styles: "...", "...", «...», '...', etc.

    Args:
        value: Markdown text potentially containing blockquotes with redundant quotes

    Returns:
        Text with redundant quotes stripped from blockquotes
    """
    if not isinstance(value, str):
        return ""

    lines = value.split("\n")
    result = []
    i = 0

    while i < len(lines):
        line = lines[i]

        # Check if this is a blockquote line
        if line.lstrip().startswith(">"):
            # Collect all consecutive blockquote lines
            blockquote_lines = []
            while i < len(lines) and lines[i].lstrip().startswith(">"):
                blockquote_lines.append(lines[i])
                i += 1

            # Process the blockquote block
            processed = _process_blockquote_block(blockquote_lines)
            result.extend(processed)
        else:
            result.append(line)
            i += 1

    return "\n".join(result)


def _process_blockquote_block(lines: list[str]) -> list[str]:
    """Process a block of consecutive blockquote lines, stripping redundant quotes."""
    if not lines:
        return lines

    # Extract the content after the > marker for each line
    # Preserve the original prefix (spaces + > + space)
    parsed = []
    for line in lines:
        # Find where the > is and extract prefix + content
        stripped = line.lstrip()
        prefix_spaces = line[:len(line) - len(stripped)]
        if stripped.startswith(">"):
            # Handle "> text" or ">text"
            after_marker = stripped[1:]
            if after_marker.startswith(" "):
                prefix = prefix_spaces + "> "
                content = after_marker[1:]
            else:
                prefix = prefix_spaces + ">"
                content = after_marker
        else:
            # Shouldn't happen, but handle gracefully
            prefix = ""
            content = line
        parsed.append((prefix, content))

    # Check if first line content starts with an opening quote
    first_content = parsed[0][1].lstrip()
    if not first_content:
        return lines  # Empty blockquote, leave as-is

    first_char = first_content[0]
    if first_char not in _OPENING_QUOTES:
        return lines  # Doesn't start with a quote, leave as-is

    # Find the expected closing quote
    expected_close = _QUOTE_PAIRS.get(first_char, first_char)

    # Check if last line content ends with the closing quote
    last_content = parsed[-1][1].rstrip()
    if not last_content or last_content[-1] != expected_close:
        return lines  # Doesn't end with matching quote, leave as-is

    # Strip the quotes
    if len(parsed) == 1:
        # Single line blockquote
        prefix, content = parsed[0]
        content = content.strip()
        if len(content) >= 2 and content[0] == first_char and content[-1] == expected_close:
            content = content[1:-1].strip()
        return [prefix + content]
    else:
        # Multi-line blockquote
        result = []
        for idx, (prefix, content) in enumerate(parsed):
            if idx == 0:
                # Strip opening quote from first line
                content = content.lstrip()
                if content and content[0] == first_char:
                    content = content[1:].lstrip()
            if idx == len(parsed) - 1:
                # Strip closing quote from last line
                content = content.rstrip()
                if content and content[-1] == expected_close:
                    content = content[:-1].rstrip()
            result.append(prefix + content)
        return result


def normalize_llm_output(value: str | None) -> str:
    """
    Comprehensive normalization of LLM output for display.

    This is the primary function to call when processing raw LLM output
    before rendering. It applies all necessary transformations in the
    correct order to produce clean, displayable text.

    Processing steps:
    1. Strip LLM artifacts (reasoning tags, malformed tool calls)
    2. Decode unicode/string escape sequences (\\u2014 -> —, \\n -> newline)
    3. Strip control characters (keeps \\n, \\r, \\t)
    4. Normalize whitespace (collapse excessive newlines, strip trailing spaces)
    5. Strip redundant quotes from blockquotes (> "text" -> > text)

    Args:
        value: Raw LLM output text

    Returns:
        Normalized text ready for display or further processing
    """
    if not isinstance(value, str):
        return ""

    text = value

    # Step 1: Strip LLM artifacts first (before they can be misinterpreted as HTML)
    text = strip_llm_artifacts(text)

    # Step 2: Decode escape sequences (before control char stripping)
    text = decode_unicode_escapes(text)

    # Step 3: Strip control characters (preserves \n, \r, \t)
    text = strip_control_chars(text)

    # Step 4: Normalize whitespace
    text = normalize_whitespace(text)

    # Step 5: Keep markdown tables contiguous (no blank lines between rows)
    text = _normalize_markdown_tables(text)

    # Step 6: Strip redundant quotes from blockquotes
    text = strip_redundant_blockquote_quotes(text)

    return text
