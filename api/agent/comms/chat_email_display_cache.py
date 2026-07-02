import hashlib
from typing import Any, Mapping

from bleach.css_sanitizer import CSSSanitizer
from bleach.sanitizer import ALLOWED_ATTRIBUTES as BLEACH_ALLOWED_ATTRIBUTES_BASE
from bleach.sanitizer import ALLOWED_PROTOCOLS as BLEACH_ALLOWED_PROTOCOLS_BASE
from bleach.sanitizer import ALLOWED_TAGS as BLEACH_ALLOWED_TAGS_BASE
from bleach.sanitizer import Cleaner

from api.agent.comms.email_content import convert_body_to_html_and_plaintext

CHAT_BODY_HTML_CACHE_KEY = "chat_body_html_v1"
CHAT_BODY_HTML_SOURCE_HASH_KEY = "chat_body_html_v1_source_sha256"
CHAT_BODY_HTML_CACHE_VERSION = "chat_body_html_v1"

EMAIL_STYLE_TAGS = {
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
    "td",
    "th",
    "tr",
    "ul",
}
EMAIL_ALLOWED_CSS_PROPERTIES = [
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
]


def _build_html_cleaner(*, allow_cid: bool = False) -> Cleaner:
    allowed_tags = set(BLEACH_ALLOWED_TAGS_BASE).union(
        {
            "p",
            "br",
            "div",
            "span",
            "img",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "ul",
            "ol",
            "li",
            "pre",
            "table",
            "thead",
            "tbody",
            "tfoot",
            "tr",
            "th",
            "td",
            "caption",
        }
    )

    allowed_attributes = dict(BLEACH_ALLOWED_ATTRIBUTES_BASE)
    anchor_attrs = set(allowed_attributes.get("a", ())).union({"href", "title", "target", "rel"})
    allowed_attributes["a"] = sorted(anchor_attrs)
    allowed_attributes.setdefault("span", [])
    allowed_attributes["img"] = ["src", "alt", "width", "height"]
    allowed_attributes["th"] = ["colspan", "rowspan", "scope", "headers"]
    allowed_attributes["td"] = ["colspan", "rowspan", "headers"]
    for tag in EMAIL_STYLE_TAGS:
        allowed_attributes[tag] = sorted(set(allowed_attributes.get(tag, ())).union({"style"}))

    allowed_protocols = set(BLEACH_ALLOWED_PROTOCOLS_BASE).union({"mailto", "tel"})
    if allow_cid:
        allowed_protocols.add("cid")

    return Cleaner(
        tags=sorted(allowed_tags),
        attributes=allowed_attributes,
        protocols=allowed_protocols,
        css_sanitizer=CSSSanitizer(allowed_css_properties=EMAIL_ALLOWED_CSS_PROPERTIES),
        strip=True,
    )


HTML_CLEANER = _build_html_cleaner()
HTML_CID_CLEANER = _build_html_cleaner(allow_cid=True)


def normalize_explicit_email_html(explicit_html: Any) -> str | None:
    if not isinstance(explicit_html, str):
        return None
    normalized = explicit_html.strip()
    return normalized or None


def chat_body_html_source_hash(body: str | None, explicit_html: str | None = None) -> str:
    hasher = hashlib.sha256()
    for part in (CHAT_BODY_HTML_CACHE_VERSION, body or "", explicit_html or ""):
        hasher.update(part.encode("utf-8"))
        hasher.update(b"\x00")
    return hasher.hexdigest()


def sanitize_chat_email_html(html: str | None, *, allow_cid: bool = False) -> str:
    if not html:
        return ""
    cleaner = HTML_CID_CLEANER if allow_cid else HTML_CLEANER
    return cleaner.clean(html)


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
        except (RuntimeError, ValueError, OSError):
            html_snippet = body or ""
    return sanitize_chat_email_html(html_snippet, allow_cid=allow_cid)


def build_chat_body_html_cache_payload(
    body: str | None,
    *,
    explicit_html: str | None = None,
    rendered_html: str | None = None,
) -> dict[str, str]:
    return {
        CHAT_BODY_HTML_CACHE_KEY: render_chat_email_body_html(
            body,
            explicit_html=explicit_html,
            rendered_html=rendered_html,
            allow_cid=True,
        ),
        CHAT_BODY_HTML_SOURCE_HASH_KEY: chat_body_html_source_hash(body, explicit_html),
    }


def merge_chat_body_html_cache(
    raw_payload: Mapping[str, Any] | None,
    body: str | None,
    *,
    explicit_html: str | None = None,
    rendered_html: str | None = None,
) -> dict[str, Any]:
    payload = dict(raw_payload or {})
    payload.update(
        build_chat_body_html_cache_payload(
            body,
            explicit_html=explicit_html,
            rendered_html=rendered_html,
        )
    )
    return payload


def get_cached_chat_body_html(
    raw_payload: Mapping[str, Any] | None,
    body: str | None,
    *,
    explicit_html: str | None = None,
) -> str | None:
    if not isinstance(raw_payload, Mapping):
        return None
    cached_html = raw_payload.get(CHAT_BODY_HTML_CACHE_KEY)
    cached_hash = raw_payload.get(CHAT_BODY_HTML_SOURCE_HASH_KEY)
    if not isinstance(cached_html, str) or not isinstance(cached_hash, str):
        return None
    if cached_hash != chat_body_html_source_hash(body, explicit_html):
        return None
    return cached_html
