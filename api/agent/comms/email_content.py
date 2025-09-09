"""Email content rendering utilities.

This module provides conversion of an agent-authored email body into
two synchronized representations:

- An HTML snippet intended to be wrapped by the app's mobile‑first
  email template (no outer <html>/<body> tags expected here)
- A plaintext alternative derived from the same content

Detection rules:
1) If common HTML tags are present, treat content as HTML and derive
   plaintext with inscriptis.
2) Otherwise, if common Markdown patterns are present, render to HTML
   with python-markdown and derive plaintext with inscriptis.
3) Otherwise, treat as plaintext, HTML‑escape and replace newlines
   with <br>, and use the stripped original for the plaintext part.
"""

from __future__ import annotations

from typing import Tuple
import logging
import re
import html

from inscriptis import get_text
from inscriptis.model.config import ParserConfig
from inscriptis.css_profiles import CSS_PROFILES
import markdown


logger = logging.getLogger(__name__)


def convert_body_to_html_and_plaintext(body: str) -> Tuple[str, str]:
    """Return (html_snippet, plaintext) derived from ``body``.

    The html_snippet is suitable for inclusion inside the application's
    email template (no outer <html>/<body> wrappers).
    """
    # Configure inscriptis to preserve URLs in plaintext conversion with strict CSS
    strict_css = CSS_PROFILES["strict"].copy()
    config = ParserConfig(css=strict_css, display_links=True, display_anchors=True)

    # Basic observability
    body_length = len(body or "")
    body_preview = (body or "")[:200] + ("..." if body_length > 200 else "")
    logger.info(
        "Email content conversion starting. Input body length: %d characters. Preview: %r",
        body_length,
        body_preview,
    )

    # Detect HTML
    html_tag_pattern = r"</?(?:p|br|div|span|a|ul|ol|li|h[1-6]|strong|em|b|i|code|pre|blockquote)\b[^>]*>"
    html_match = re.search(html_tag_pattern, body or "", re.IGNORECASE)
    if html_match:
        logger.info(
            "Content type detected: HTML. Found HTML tag pattern: %r at position %d",
            html_match.group(0),
            html_match.start(),
        )
        html_snippet = body or ""
        plaintext = get_text(html_snippet, config).strip()
        logger.info(
            "HTML processing complete. Original HTML length: %d, extracted plaintext length: %d.",
            len(html_snippet),
            len(plaintext),
        )
        return html_snippet, plaintext

    # Detect Markdown
    markdown_patterns = [
        (r"^\s{0,3}#", "heading"),              # Heading '# Title'
        (r"\*\*.+?\*\*", "bold_asterisk"),     # Bold **text**
        (r"__.+?__", "bold_underscore"),       # Bold __text__
        (r"`{1,3}.+?`{1,3}", "code"),          # Inline/fenced code
        (r"\[[^\]]+\]\([^)]+\)", "link"),      # Link [text](url)
        (r"^\s*[-*+] ", "unordered_list"),     # Unordered list
        (r"^\s*\d+\. ", "ordered_list"),      # Ordered list
    ]

    detected = any(re.search(pat, body or "", flags=re.MULTILINE) for pat, _ in markdown_patterns)
    if detected:
        html_snippet = markdown.markdown(body or "", extensions=["extra", "sane_lists", "smarty"])
        plaintext = get_text(html_snippet, config).strip()
        logger.info(
            "Markdown processing complete. Rendered HTML length: %d, plaintext length: %d.",
            len(html_snippet),
            len(plaintext),
        )
        return html_snippet, plaintext

    # Plaintext fallback
    escaped = html.escape(body or "")
    html_snippet = escaped.replace("\n", "<br>")
    plaintext = (body or "").strip()
    logger.info(
        "Plaintext processing complete. HTML-escaped length: %d.",
        len(html_snippet),
    )
    return html_snippet, plaintext

