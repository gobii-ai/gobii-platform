import hashlib
import logging
import re
from collections.abc import Iterable
from urllib.parse import urlsplit

from django.conf import settings
from django.db import DatabaseError

from api.models import PersistentAgentLinkReference

logger = logging.getLogger(__name__)

_HTTP_URL_RE = re.compile(r'''https?://[^\s<>"'`\[\]\\]+''', re.IGNORECASE)
_REFERENCE_RE = re.compile(r"\$\[link:([^\]]*)\]", re.IGNORECASE)
_REFERENCE_PREFIX_RE = re.compile(r"\$\[link:", re.IGNORECASE)
_PUBLIC_ID_PATTERN = r"L[0123456789ABCDEFGHJKMNPQRSTVWXYZ]{16}"
_PUBLIC_ID_RE = re.compile(_PUBLIC_ID_PATTERN, re.IGNORECASE)
_NAKED_DESTINATION_RE = re.compile(rf"(?:\]\(\s*|href\s*=\s*['\"]\s*)({_PUBLIC_ID_PATTERN})(?=\s*(?:\)|['\"]))", re.IGNORECASE)
_RENDERED_PATH_RE = re.compile(rf"/({_PUBLIC_ID_PATTERN})(?:/[^/]*)?/?$", re.IGNORECASE)
_TRAILING_PUNCTUATION = ".,;:!?"
_EMBEDDED_FIELDS = {"create_csv": "csv_text", "create_pdf": "html", "send_agent_message": "message", "send_chat_message": "body", "send_discord_message": "message", "send_email": "mobile_first_html", "send_sms": "body"}
_DOCUMENT_MIME_TYPES = {"application/json", "application/ld+json", "application/xml", "application/yaml", "text/html", "text/markdown", "text/plain", "text/xml", "text/yaml"}
_STRICT_TOOLS = set(_EMBEDDED_FIELDS) | {"apply_patch", "create_chart", "create_custom_tool", "create_file", "create_image", "create_video", "search_tools", "send_webhook_event", "sqlite_batch", "update_charter", "update_plan", "update_schedule"}


class LinkReferenceResolutionError(ValueError):
    pass


def _split_url_suffix(raw_url: str) -> tuple[str, str]:
    url = raw_url.rstrip(_TRAILING_PUNCTUATION)
    for opening, closing in (("(", ")"), ("[", "]"), ("{", "}")):
        while url.endswith(closing) and url.count(closing) > url.count(opening):
            url = url[:-1]
    suffix = raw_url[len(url):]
    url = re.sub(r"&amp;", "&", url, flags=re.IGNORECASE)
    return url, suffix


def extract_http_urls(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_split_url_suffix(match.group())[0] for match in _HTTP_URL_RE.finditer(text or "")))


def is_source_bearing_tool(tool_name: str) -> bool:
    return tool_name in {"http_request", "spawn_web_task_result"} or tool_name.startswith("mcp_")


def _reference_map(
    agent, urls: Iterable[str], *, create: bool, source_kind: str = "", source_object_id: str = ""
) -> dict[str, str]:
    urls_by_hash = {hashlib.sha256(url.encode()).hexdigest(): url for url in urls}
    if not urls_by_hash:
        return {}
    references = list(PersistentAgentLinkReference.objects.filter(agent=agent, url_hash__in=urls_by_hash))
    if create:
        found = {reference.url_hash for reference in references}
        PersistentAgentLinkReference.objects.bulk_create(
            [
                PersistentAgentLinkReference(agent=agent, url=url, url_hash=url_hash, source_kind=source_kind,
                                             source_object_id=str(source_object_id or ""))
                for url_hash, url in urls_by_hash.items()
                if url_hash not in found
            ],
            ignore_conflicts=True,
        )
        references = list(PersistentAgentLinkReference.objects.filter(agent=agent, url_hash__in=urls_by_hash))
    return {ref.url: f"$[link:{ref.public_id}]" for ref in references if urls_by_hash.get(ref.url_hash) == ref.url}


def rewrite_prompt_urls(
    text: str, agent, *, create: bool, source_kind: str = "", source_object_id: str = ""
) -> str:
    urls = extract_http_urls(text)
    if not urls:
        return text
    try:
        references = _reference_map(agent, urls, create=create, source_kind=source_kind, source_object_id=source_object_id)
    except DatabaseError:
        logger.warning("Failed to load link references for agent %s", getattr(agent, "id", None), exc_info=True)
        return text

    def replace(match: re.Match) -> str:
        url, suffix = _split_url_suffix(match.group())
        return references.get(url, url) + suffix

    return _HTTP_URL_RE.sub(replace, text)


def register_prompt_urls(text: str, agent, *, source_kind: str, source_object_id: str = "") -> None:
    try:
        _reference_map(agent, extract_http_urls(text), create=True, source_kind=source_kind, source_object_id=source_object_id)
    except DatabaseError:
        logger.warning("Failed to register link references for agent %s", getattr(agent, "id", None), exc_info=True)


def _rendered_reference_id(url: str) -> str | None:
    parsed, public_site = urlsplit(url), urlsplit(settings.PUBLIC_SITE_URL)
    match = _RENDERED_PATH_RE.search(parsed.path)
    if (parsed.scheme.lower() not in {"http", "https"} or parsed.netloc.lower() != public_site.netloc.lower()
            or not match or parsed.query or parsed.fragment):
        return None
    return match.group(1).upper()


def resolve_link_references(text: str, agent) -> str:
    text = text or ""
    if naked := _NAKED_DESTINATION_RE.search(text):
        public_id = naked.group(1).upper()
        raise LinkReferenceResolutionError(f"A link reference is malformed. Use $[link:{public_id}] as the complete destination.")
    matches = list(_REFERENCE_RE.finditer(text))
    if len(matches) != len(_REFERENCE_PREFIX_RE.findall(text)):
        raise LinkReferenceResolutionError("A link reference is malformed. Reuse a provided $[link:id] value or omit the link.")
    rendered = {url: public_id for url in extract_http_urls(text)
                if (public_id := _rendered_reference_id(url)) is not None}
    if not matches and not rendered:
        return text
    reference_ids = {match.group(1).upper() for match in matches}
    if any(not _PUBLIC_ID_RE.fullmatch(public_id) for public_id in reference_ids):
        raise LinkReferenceResolutionError("A link reference is malformed. Reuse a provided $[link:id] value or omit the link.")
    lookup_ids = reference_ids | set(rendered.values())
    try:
        references = {ref.public_id: ref.url for ref in
                      PersistentAgentLinkReference.objects.filter(agent=agent, public_id__in=lookup_ids)}
    except DatabaseError as exc:
        raise LinkReferenceResolutionError("Link references are temporarily unavailable. Retry the same message.") from exc
    if not lookup_ids.issubset(references):
        missing = ", ".join(sorted(lookup_ids - references.keys()))
        raise LinkReferenceResolutionError(f"Link reference {missing} is unavailable. Recopy that provided token exactly; other references remain usable.")
    resolved = _REFERENCE_RE.sub(lambda match: references[match.group(1).upper()], text)
    if rendered:
        def replace_rendered(match: re.Match) -> str:
            url, suffix = _split_url_suffix(match.group())
            return references.get(rendered.get(url), url) + suffix
        resolved = _HTTP_URL_RE.sub(replace_rendered, resolved)
    return resolved


def resolve_link_references_for_display(value, agent):
    if isinstance(value, dict):
        return {key: resolve_link_references_for_display(item, agent) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_link_references_for_display(item, agent) for item in value]
    if not isinstance(value, str) or not _contains_reference_syntax(value):
        return value
    try:
        return resolve_link_references(value, agent)
    except LinkReferenceResolutionError:
        resolved = re.sub(r"\$\[link:[^\]\s]*(?:\]|$)", "Link unavailable", value, flags=re.IGNORECASE)
        return _HTTP_URL_RE.sub(lambda match: "Link unavailable" if _rendered_reference_id(_split_url_suffix(match.group())[0]) else match.group(), resolved)


def _contains_reference_syntax(value: str) -> bool:
    return bool(_REFERENCE_PREFIX_RE.search(value) or _NAKED_DESTINATION_RE.search(value)
                or any(_rendered_reference_id(url) for url in extract_http_urls(value)))


def _param_path(path: tuple[object, ...]) -> str:
    return "".join(f"[{part}]" if isinstance(part, int) else f"{'.' if index else ''}{part}" for index, part in enumerate(path))


def _embedded_fields(tool_name: str, params) -> set[str]:
    fields = {_EMBEDDED_FIELDS[tool_name]} if tool_name in _EMBEDDED_FIELDS else set()
    if tool_name == "create_file" and str(params.get("mime_type", "")).split(";", 1)[0].strip().lower() in _DOCUMENT_MIME_TYPES:
        fields.add("content")
    return fields


def resolve_link_reference_params(value, agent, *, tool_name: str = "", _path=(), _allowed=None):
    if _allowed is None:
        _allowed = _embedded_fields(tool_name, value) if isinstance(value, dict) else set()
    if isinstance(value, dict):
        return {key: resolve_link_reference_params(item, agent, tool_name=tool_name, _path=(*_path, key), _allowed=_allowed) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_link_reference_params(item, agent, tool_name=tool_name, _path=(*_path, index), _allowed=_allowed) for index, item in enumerate(value)]
    if not isinstance(value, str):
        return value
    if _path and _path[0] in _allowed:
        return value
    stripped = value.strip()
    exact = _REFERENCE_RE.fullmatch(stripped) or _rendered_reference_id(stripped)
    if exact and tool_name not in _STRICT_TOOLS:
        resolved = resolve_link_references(stripped, agent)
        return value[: len(value) - len(value.lstrip())] + resolved + value[len(value.rstrip()):]
    if tool_name and _contains_reference_syntax(value):
        location = f"{tool_name}.{_param_path(_path)}" if tool_name else _param_path(_path) or "this value"
        raise LinkReferenceResolutionError(f"Link references are unsupported in {location}. Use a standalone token only where URL input is supported, move it to supported message/document content, or omit it.")
    return value


def document_mime_supports_link_references(mime_type: str) -> bool:
    return mime_type in _DOCUMENT_MIME_TYPES


def link_reference_error_response(exc: LinkReferenceResolutionError) -> dict[str, object]:
    return {"status": "error", "message": str(exc), "retryable": True}
