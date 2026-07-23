"""Channel-specific validation for outbound message content."""

import re

from markdown_it import MarkdownIt


_MARKDOWN_PARSER = MarkdownIt("commonmark")
_RAW_HTML_TOKEN_TYPES = frozenset({"html_block", "html_inline"})
_HTML_TAG_NAMES = frozenset(
    """
    a abbr acronym address applet area article aside audio b base basefont bdi bdo bgsound big blockquote body br
    button canvas caption center cite code col colgroup data datalist dd del details dfn dialog dir div dl dt em
    embed fieldset figcaption figure font footer form frame frameset h1 h2 h3 h4 h5 h6 head header hgroup hr html
    i iframe img input ins kbd keygen label legend li link main map mark marquee math menu menuitem meta meter nav
    nobr noembed noframes noscript object ol optgroup option output p param picture plaintext pre progress q rb rp
    rt rtc ruby s samp script search section select slot small source span strike strong style sub summary sup svg
    table tbody td template textarea tfoot th thead time title tr track tt u ul var video wbr xmp
    circle clippath defs ellipse foreignobject g line lineargradient mask path polygon polyline radialgradient rect
    stop symbol text tspan use
    """.split()
)
_VOID_HTML_TAG_NAMES = frozenset(
    {
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
)
_ALWAYS_HTML_TAG_NAMES = frozenset(
    {"iframe", "noembed", "noscript", "plaintext", "script", "style", "textarea", "title", "xmp"}
)
_HTML_TAG_RE = re.compile(
    r"<\s*(?P<closing>/)?\s*(?P<name>[A-Za-z][A-Za-z0-9-]*)\b(?P<attributes>[^>]*)>",
    re.DOTALL,
)
_SPECIAL_HTML_RE = re.compile(r"<\s*(?:!--|!DOCTYPE\b|!\[CDATA\[|\?)", re.IGNORECASE)


def _raw_html_tokens(value: str | None) -> list[tuple[str, str]]:
    pending = list(_MARKDOWN_PARSER.parse(value or ""))
    raw_tokens: list[tuple[str, str]] = []
    while pending:
        token = pending.pop()
        if token.type in _RAW_HTML_TOKEN_TYPES:
            raw_tokens.append((token.type, token.content))
        pending.extend(token.children or ())
    return raw_tokens


def contains_raw_html(value: str | None) -> bool:
    """Return whether content contains HTML intended for rendering."""

    inline_fragments: list[str] = []
    for token_type, content in _raw_html_tokens(value):
        if _SPECIAL_HTML_RE.search(content):
            return True
        if token_type == "html_block":
            if any(match.group("name").lower() in _HTML_TAG_NAMES for match in _HTML_TAG_RE.finditer(content)):
                return True
        else:
            inline_fragments.append(content)

    for match in _HTML_TAG_RE.finditer("\n".join(inline_fragments)):
        raw_name = match.group("name")
        name = raw_name.lower()
        if name not in _HTML_TAG_NAMES:
            continue
        attributes = match.group("attributes")
        if (
            match.group("closing")
            or (raw_name == name and name in _VOID_HTML_TAG_NAMES)
            or (raw_name == name and name in _ALWAYS_HTML_TAG_NAMES)
            or "=" in attributes
            or attributes.rstrip().endswith("/")
        ):
            return True
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
