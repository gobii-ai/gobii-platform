from typing import Any, Mapping

from bs4 import BeautifulSoup

from bleach.css_sanitizer import CSSSanitizer
from bleach.linkifier import Linker
from bleach.sanitizer import ALLOWED_ATTRIBUTES, ALLOWED_PROTOCOLS, ALLOWED_TAGS, Cleaner

from api.agent.comms.email_content import convert_body_to_html_and_plaintext

CHAT_BODY_HTML_CACHE_KEY = "chat_body_html_v1"

EMAIL_TAGS = (
    "caption",
    "div",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "li",
    "ol",
    "p",
    "span",
    "strong",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "ul",
)
STYLED_EMAIL_TAGS = tuple(tag for tag in EMAIL_TAGS if tag not in {"br", "img", "pre", "tbody", "tfoot", "thead"})
EMAIL_CSS_PROPERTIES = (
    "background",
    "border-bottom",
    "border-left",
    "border-radius",
    "color",
    "display",
    "flex-direction",
    "font-size",
    "gap",
    "line-height",
    "margin",
    "margin-bottom",
    "margin-top",
    "padding",
    "padding-bottom",
)


def _build_html_cleaner(*, allow_cid: bool = False) -> Cleaner:
    attributes = dict(ALLOWED_ATTRIBUTES)
    attributes["a"] = sorted(set(attributes.get("a", ())).union({"href", "rel", "target", "title"}))
    attributes["img"] = ["alt", "height", "src", "width"]
    attributes["td"] = ["colspan", "headers", "rowspan"]
    attributes["th"] = ["colspan", "headers", "rowspan", "scope"]
    for tag in STYLED_EMAIL_TAGS:
        attributes[tag] = sorted(set(attributes.get(tag, ())).union({"style"}))

    protocols = set(ALLOWED_PROTOCOLS).union({"mailto", "tel"})
    if allow_cid:
        protocols.add("cid")

    return Cleaner(
        tags=sorted(set(ALLOWED_TAGS).union(EMAIL_TAGS, {"br", "img", "pre"})),
        attributes=attributes,
        protocols=protocols,
        css_sanitizer=CSSSanitizer(allowed_css_properties=EMAIL_CSS_PROPERTIES),
        strip=True,
    )


HTML_CLEANER = _build_html_cleaner()
HTML_CID_CLEANER = _build_html_cleaner(allow_cid=True)

# Code shows text exactly as written, so an address quoted as a sample stays text.
LINKIFY_SKIP_TAGS = {"code", "pre"}


def normalize_explicit_email_html(explicit_html: Any) -> str | None:
    if not isinstance(explicit_html, str):
        return None
    return explicit_html.strip() or None


def _drop_non_content_elements(html: str) -> str:
    # Bleach's strip=True removes disallowed tags but keeps their text, so an email's
    # <style>/<title> contents survive as walls of CSS prose in the timeline (#504).
    # These elements' text is never content; remove them wholesale before cleaning.
    if not any(marker in html.lower() for marker in ("<style", "<title", "<script", "<head")):
        return html
    soup = BeautifulSoup(html, "html.parser")
    for element in soup.find_all(["style", "title", "script", "head"]):
        element.decompose()
    return str(soup)


def sanitize_chat_email_html(html: str | None, *, allow_cid: bool = False) -> str:
    if not html:
        return ""
    cleaned = (HTML_CID_CLEANER if allow_cid else HTML_CLEANER).clean(_drop_non_content_elements(html))
    # Markdown bodies are autolinked on the client by remark-gfm, but HTML reaches the timeline
    # already rendered and bypasses that entirely, leaving addresses readable but not actionable.
    # Linkifying here rather than at a call site covers explicit agent HTML, cached bodies, and
    # cid-rewritten forwards alike, since all of them come through this function.
    #
    # A Linker is built per call because it wraps a stateful html5lib parser; sharing one across
    # concurrently served requests would interleave parses.
    return Linker(
        callbacks=[],
        skip_tags=LINKIFY_SKIP_TAGS,
        parse_email=True,
    ).linkify(cleaned)


def render_chat_email_body_html(
    body: str | None,
    *,
    explicit_html: str | None = None,
    rendered_html: str | None = None,
    allow_cid: bool = False,
) -> str:
    html_snippet = rendered_html or explicit_html or ""
    if not html_snippet:
        try:
            html_snippet, _ = convert_body_to_html_and_plaintext(body or "", emit_logs=False)
        except (RuntimeError, ValueError, OSError, TypeError, AttributeError, LookupError):
            html_snippet = body or ""
    return sanitize_chat_email_html(html_snippet, allow_cid=allow_cid)


def merge_chat_body_html_cache(
    raw_payload: Mapping[str, Any] | None,
    body: str | None,
    *,
    explicit_html: str | None = None,
    rendered_html: str | None = None,
) -> dict[str, Any]:
    payload = dict(raw_payload or {})
    payload[CHAT_BODY_HTML_CACHE_KEY] = render_chat_email_body_html(
        body,
        explicit_html=explicit_html,
        rendered_html=rendered_html,
        allow_cid=True,
    )
    return payload


def get_cached_chat_body_html(raw_payload: Mapping[str, Any] | None) -> str | None:
    if not isinstance(raw_payload, Mapping):
        return None
    cached_html = raw_payload.get(CHAT_BODY_HTML_CACHE_KEY)
    return cached_html if isinstance(cached_html, str) else None
