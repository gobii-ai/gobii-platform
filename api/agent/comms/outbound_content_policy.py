"""Channel-specific validation for outbound message content."""

from markdown_it import MarkdownIt


_MARKDOWN_PARSER = MarkdownIt("commonmark")
_RAW_HTML_TOKEN_TYPES = frozenset({"html_block", "html_inline"})


def contains_raw_html(value: str | None) -> bool:
    """Return whether CommonMark would treat any content as renderable HTML."""

    pending = list(_MARKDOWN_PARSER.parse(value or ""))
    while pending:
        token = pending.pop()
        if token.type in _RAW_HTML_TOKEN_TYPES:
            return True
        pending.extend(token.children or ())
    return False


def markdown_only_error(value: str | None, *, surface: str) -> dict[str, object] | None:
    """Build the standard tool error when a Markdown-only surface receives HTML."""

    if not contains_raw_html(value):
        return None
    return {
        "status": "error",
        "error_type": "unsupported_markup",
        "retryable": True,
        "message": (
            f"{surface} supports Markdown, not raw HTML. Replace HTML formatting with Markdown and retry; "
            "use code formatting to show HTML literally."
        ),
    }
