"""Email content rendering utilities.

This module provides conversion of an agent-authored email body into
two synchronized representations:

- An HTML snippet intended to be wrapped by the app's mobile-first
  email template (no outer <html>/<body> tags expected here)
- A plaintext alternative derived from the same content

Detection rules:
1) If common HTML tags or Markdown patterns are present, render via
   python-markdown (HTML passthrough enabled) and repair any inline
   Markdown that appears inside HTML blocks.
2) Otherwise, treat as plaintext, HTML-escape, and preserve paragraph
   structure with <p> and <br>.
"""

from typing import Tuple
import html
import logging
import re

from inscriptis import get_text
from inscriptis.model.config import ParserConfig
from inscriptis.css_profiles import CSS_PROFILES
import markdown


# Inline styles for email-safe HTML (email clients strip most CSS)
TABLE_STYLE = "border-collapse: collapse; width: 100%; margin: 16px 0; font-size: 14px;"
TH_STYLE = "padding: 10px 12px; text-align: left; background: #f8fafc; border-bottom: 2px solid #e2e8f0; font-weight: 600; color: #1e293b;"
TD_STYLE = "padding: 10px 12px; text-align: left; border-bottom: 1px solid #e2e8f0; color: #334155;"

MARKDOWN_EXTENSIONS = ["extra", "sane_lists", "smarty", "nl2br"]

HTML_TAG_PATTERN = r"</?(?:p|br|hr|img|div|span|a|ul|ol|li|h[1-6]|strong|em|b|i|code|pre|blockquote|table|thead|tbody|tr|th|td)\b[^>]*>"

INLINE_MARKDOWN_PATTERNS = [
    re.compile(r"\*\*.+?\*\*"),
    re.compile(r"__.+?__"),
    re.compile(r"`{1,3}.+?`{1,3}"),
    re.compile(r"\[[^\]]+\]\([^)]+\)"),
    re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)"),
    re.compile(r"(?<!_)_(?!_)(.+?)(?<!_)_(?!_)"),
]
BLOCK_MARKDOWN_PATTERNS = [
    re.compile(r"^\s{0,3}#", re.MULTILINE),
    re.compile(r"^\s*[-*+] ", re.MULTILINE),
    re.compile(r"^\s*\d+\. ", re.MULTILINE),
    re.compile(r"^\s*>", re.MULTILINE),
    re.compile(r"^\s*[-*_]{3,}\s*$", re.MULTILINE),
    re.compile(r"^\s*\|.*\|", re.MULTILINE),
]

TAG_SPLIT_RE = re.compile(r"(<[^>]+>)")
TAG_NAME_RE = re.compile(r"^</?\s*([a-zA-Z0-9]+)")
HR_RE = re.compile(r"<hr\s*/?>", re.IGNORECASE)

VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}
INLINE_CONTEXT_TAGS = {
    "a",
    "b",
    "code",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "i",
    "li",
    "p",
    "span",
    "strong",
    "td",
    "th",
}
SKIP_MARKDOWN_TAGS = {"pre", "code"}
INLINE_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
INLINE_CODE_RE = re.compile(r"`([^`]+)`")
INLINE_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
INLINE_BOLD_UNDER_RE = re.compile(r"__(.+?)__")
INLINE_ITALIC_STAR_RE = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
INLINE_ITALIC_UNDER_RE = re.compile(r"(?<!_)_(?!_)(.+?)(?<!_)_(?!_)")


def _add_table_styles(html_content: str) -> str:
    """Add inline styles to table elements for email compatibility."""
    # Style tables
    html_content = re.sub(
        r'<table(?![^>]*style=)([^>]*)>',
        f'<table style="{TABLE_STYLE}"\\1>',
        html_content,
        flags=re.IGNORECASE
    )
    # Style th elements
    html_content = re.sub(
        r'<th(?![^>]*style=)([^>]*)>',
        f'<th style="{TH_STYLE}"\\1>',
        html_content,
        flags=re.IGNORECASE
    )
    # Style td elements
    html_content = re.sub(
        r'<td(?![^>]*style=)([^>]*)>',
        f'<td style="{TD_STYLE}"\\1>',
        html_content,
        flags=re.IGNORECASE
    )
    return html_content


logger = logging.getLogger(__name__)


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _contains_markdown(text: str) -> bool:
    return any(pattern.search(text) for pattern in INLINE_MARKDOWN_PATTERNS + BLOCK_MARKDOWN_PATTERNS)


def _contains_block_markdown(text: str) -> bool:
    return any(pattern.search(text) for pattern in BLOCK_MARKDOWN_PATTERNS)


def _contains_inline_markdown(text: str) -> bool:
    return any(pattern.search(text) for pattern in INLINE_MARKDOWN_PATTERNS)


def _render_plaintext_html(text: str) -> str:
    normalized = _normalize_newlines(text)
    escaped = html.escape(normalized)
    paragraphs = [p for p in re.split(r"\n{2,}", escaped) if p.strip()]
    rendered = []
    for para in paragraphs:
        rendered.append(f"<p>{para.replace('\n', '<br />')}</p>")
    return "".join(rendered)


def _render_markdown_html(text: str) -> str:
    normalized = _normalize_newlines(text)
    return markdown.markdown(normalized, extensions=MARKDOWN_EXTENSIONS)


def _render_inline_link(match: re.Match[str]) -> str:
    label = html.escape(match.group(1))
    href = html.escape(match.group(2), quote=True)
    return f"<a href='{href}'>{label}</a>"


def _render_inline_markdown(text: str) -> str:
    normalized = _normalize_newlines(text)
    code_spans: list[str] = []

    def _stash_code(match: re.Match[str]) -> str:
        code_spans.append(match.group(1))
        return f"@@CODE{len(code_spans) - 1}@@"

    rendered = INLINE_CODE_RE.sub(_stash_code, normalized)
    rendered = INLINE_LINK_RE.sub(_render_inline_link, rendered)
    rendered = INLINE_BOLD_RE.sub(r"<strong>\1</strong>", rendered)
    rendered = INLINE_BOLD_UNDER_RE.sub(r"<strong>\1</strong>", rendered)
    rendered = INLINE_ITALIC_STAR_RE.sub(r"<em>\1</em>", rendered)
    rendered = INLINE_ITALIC_UNDER_RE.sub(r"<em>\1</em>", rendered)

    for idx, code in enumerate(code_spans):
        escaped = html.escape(code)
        rendered = rendered.replace(f"@@CODE{idx}@@", f"<code>{escaped}</code>")

    return rendered.replace("\n", "<br />")


def _extract_tag_name(tag: str) -> str:
    match = TAG_NAME_RE.match(tag)
    if not match:
        return ""
    return match.group(1).lower()


def _is_void_tag(tag_name: str, raw_tag: str) -> bool:
    if tag_name in VOID_TAGS:
        return True
    return raw_tag.endswith("/>")


def _stack_contains_any(stack: list[str], tags: set[str]) -> bool:
    return any(tag in tags for tag in stack)


def _apply_inline_markdown_in_html(html_content: str) -> str:
    if not html_content:
        return html_content

    parts = TAG_SPLIT_RE.split(html_content)
    stack: list[str] = []
    rendered: list[str] = []

    for part in parts:
        if not part:
            continue
        if part.startswith("<") and part.endswith(">"):
            tag_name = _extract_tag_name(part)
            if tag_name:
                if part.startswith("</"):
                    if stack and stack[-1] == tag_name:
                        stack.pop()
                    elif tag_name in stack:
                        last_index = len(stack) - 1 - stack[::-1].index(tag_name)
                        stack = stack[:last_index]
                elif not _is_void_tag(tag_name, part):
                    stack.append(tag_name)
            rendered.append(part)
            continue

        if not part.strip():
            rendered.append(part)
            continue

        if _stack_contains_any(stack, SKIP_MARKDOWN_TAGS):
            rendered.append(part)
            continue

        has_inline = _contains_inline_markdown(part)
        has_block = _contains_block_markdown(part)
        in_inline_context = _stack_contains_any(stack, INLINE_CONTEXT_TAGS)

        if has_inline or has_block:
            if in_inline_context:
                rendered.append(_render_inline_markdown(part))
            else:
                rendered.append(_render_markdown_html(part))
            continue

        if "\n" in part:
            rendered.append(part.replace("\n", "<br />"))
        else:
            rendered.append(part)

    return "".join(rendered)


def _replace_horizontal_rules(html_content: str) -> str:
    return HR_RE.sub("<br /><br />", html_content)


def convert_body_to_html_and_plaintext(body: str) -> Tuple[str, str]:
    """Return (html_snippet, plaintext) derived from ``body``.

    The html_snippet is suitable for inclusion inside the application's
    email template (no outer <html>/<body> wrappers).
    """
    # Configure inscriptis to preserve URLs in plaintext conversion with strict CSS
    strict_css = CSS_PROFILES["strict"].copy()
    config = ParserConfig(css=strict_css, display_links=True, display_anchors=True)

    normalized_body = _normalize_newlines(body or "")
    # Basic observability
    body_length = len(normalized_body)
    body_preview = normalized_body[:200] + ("..." if body_length > 200 else "")
    logger.info(
        "Email content conversion starting. Input body length: %d characters. Preview: %r",
        body_length,
        body_preview,
    )

    html_match = re.search(HTML_TAG_PATTERN, normalized_body, re.IGNORECASE)
    has_html = bool(html_match)
    has_markdown = _contains_markdown(normalized_body)

    if has_html and has_markdown:
        mode = "mixed"
    elif has_html:
        mode = "html"
    elif has_markdown:
        mode = "markdown"
    else:
        mode = "plaintext"

    if html_match:
        logger.info(
            "HTML detection: found tag pattern %r at position %d",
            html_match.group(0),
            html_match.start(),
        )
    logger.info(
        "Content detection summary: mode=%s html=%s markdown=%s",
        mode,
        has_html,
        has_markdown,
    )

    if has_html or has_markdown:
        html_snippet = _render_markdown_html(normalized_body)
        repaired = _apply_inline_markdown_in_html(html_snippet)
        html_snippet = _replace_horizontal_rules(repaired)
        html_snippet = _add_table_styles(html_snippet)
        plaintext = get_text(html_snippet, config).strip()
        logger.info(
            "Rich content processing complete. HTML length: %d, plaintext length: %d.",
            len(html_snippet),
            len(plaintext),
        )
        return html_snippet, plaintext

    html_snippet = _render_plaintext_html(normalized_body)
    plaintext = normalized_body.strip()
    logger.info(
        "Plaintext processing complete. HTML-escaped length: %d.",
        len(html_snippet),
    )
    return html_snippet, plaintext
