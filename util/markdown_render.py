"""Utilities for rendering safe Markdown snippets for the console UI."""
from __future__ import annotations

import re
from xml.etree.ElementTree import Element

import markdown
from markdown.extensions import Extension
from markdown.inlinepatterns import SimpleTagInlineProcessor
from markdown.treeprocessors import Treeprocessor
from django.utils.safestring import SafeString, mark_safe

_TASK_PATTERN = re.compile(r"^\s*\[(?P<state>[ xX])\]\s+")


class _StrikeThroughExtension(Extension):
    def extendMarkdown(self, md: markdown.Markdown) -> None:
        pattern = r"(~~)(.+?)\1"
        md.inlinePatterns.register(
            SimpleTagInlineProcessor(pattern, "del"),
            "gobii_strike",
            175,
        )


class _TaskListTreeprocessor(Treeprocessor):
    def run(self, root: Element) -> None:
        for li in root.iter("li"):
            container = li
            text = li.text or ""
            match = _TASK_PATTERN.match(text)

            if not match and len(li) and li[0].tag == "p":
                container = li[0]
                text = container.text or ""
                match = _TASK_PATTERN.match(text)

            if not match:
                continue

            remainder = text[match.end():]
            checked = match.group("state").lower() == "x"
            self._attach_checkbox(container, remainder, checked)

    @staticmethod
    def _attach_checkbox(container: Element, remainder: str, checked: bool) -> None:
        checkbox = Element("input")
        checkbox.set("type", "checkbox")
        checkbox.set("disabled", "disabled")
        if checked:
            checkbox.set("checked", "checked")

        container.insert(0, checkbox)

        cleaned = remainder.lstrip()
        container.text = ""
        checkbox.tail = f" {cleaned}" if cleaned else " "


class _TaskListExtension(Extension):
    def extendMarkdown(self, md: markdown.Markdown) -> None:
        md.treeprocessors.register(_TaskListTreeprocessor(md), "gobii_tasklist", 75)


_safe_markdown = markdown.Markdown(
    extensions=[
        "extra",
        "sane_lists",
        "nl2br",
        _StrikeThroughExtension(),
        _TaskListExtension(),
    ],
    output_format="html5",
)

_LIST_ITEM_RE = re.compile(r"^(?:[*+-]|\d+[.)])\s+")
_FENCE_PATTERN = re.compile(r"^(```|~~~)")

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

def _prepare_markdown_source(value: str) -> str:
    """Normalize agent-authored Markdown before rendering.

    This primarily adds implicit blank lines before list items that follow a
    paragraph without an intervening newline. The OpenAI agents frequently emit
    markdown like ``Paragraph\n1. Item`` without the required blank line, which
    python-markdown otherwise treats as a plain paragraph. We keep the
    normalisation intentionally lightweight so we do not disturb legitimate
    constructs like code fences or preformatted blocks.
    """

    if not value:
        return ""

    clean_value = value.replace("\r\n", "\n").replace("\r", "\n")
    lines = clean_value.split("\n")
    if len(lines) <= 1:
        return clean_value

    normalized: list[str] = []
    previous_blank = True
    previous_was_list = False
    in_fence = False

    for line in lines:
        stripped = line.lstrip()

        if _FENCE_PATTERN.match(stripped):
            normalized.append(line)
            in_fence = not in_fence
            previous_blank = True
            previous_was_list = False
            continue

        if in_fence:
            normalized.append(line)
            continue

        is_blank = stripped == ""
        is_list_item = bool(_LIST_ITEM_RE.match(stripped))

        if is_list_item and not previous_blank and not previous_was_list:
            normalized.append("")
            previous_blank = True
            previous_was_list = False

        normalized.append(line)

        previous_blank = is_blank
        previous_was_list = is_list_item and not is_blank

    return "\n".join(normalized)


def render_agent_markdown(value: str) -> SafeString:
    """Convert Markdown to sanitized HTML suitable for embedding in the console."""
    prepared = _prepare_markdown_source(value)
    html = _safe_markdown.reset().convert(prepared)
    return mark_safe(html)
