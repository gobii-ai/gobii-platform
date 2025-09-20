"""Utilities for rendering safe Markdown snippets for the console UI."""
from __future__ import annotations

import markdown
from django.utils.safestring import SafeString, mark_safe

_safe_markdown = markdown.Markdown(
    extensions=["extra", "sane_lists", "nl2br"],
    output_format="html5",
)

# Disable raw HTML processing so user-provided content cannot inject markup.
for pattern in ("html", "entity", "html_inline"):
    try:
        _safe_markdown.inlinePatterns.deregister(pattern)
    except (KeyError, ValueError):
        continue

for preprocessor in ("html_block", "raw_html"):
    try:
        _safe_markdown.preprocessors.deregister(preprocessor)
    except (KeyError, ValueError):
        continue


def render_agent_markdown(value: str) -> SafeString:
    """Convert Markdown to sanitized HTML suitable for embedding in the console."""
    html = _safe_markdown.reset().convert(value)
    return mark_safe(html)
