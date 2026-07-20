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
_LINK_REFERENCE_RE = re.compile(r"\$\[link:([^\]]*)\]", re.IGNORECASE)
_LINK_REFERENCE_PREFIX_RE = re.compile(r"\$\[link:", re.IGNORECASE)
_PUBLIC_ID_RE = re.compile(r"L[0123456789ABCDEFGHJKMNPQRSTVWXYZ]{16}", re.IGNORECASE)
_NAKED_REFERENCE_DESTINATION_RE = re.compile(
    r"(?:\]\(\s*|href\s*=\s*['\"]\s*)(L[0123456789ABCDEFGHJKMNPQRSTVWXYZ]{16})"
    r"(?=\s*(?:\)|['\"]))",
    re.IGNORECASE,
)
_TRAILING_PUNCTUATION = ".,;:!?"
_RENDERED_REFERENCE_PATH_RE = re.compile(
    r"/(L[0123456789ABCDEFGHJKMNPQRSTVWXYZ]{16})/?$",
    re.IGNORECASE,
)


class LinkReferenceResolutionError(ValueError):
    pass


def _split_url_suffix(raw_url: str) -> tuple[str, str]:
    trimmed = raw_url.rstrip(_TRAILING_PUNCTUATION)
    for opening, closing in (("(", ")"), ("[", "]"), ("{", "}")):
        while trimmed.endswith(closing) and trimmed.count(closing) > trimmed.count(opening):
            trimmed = trimmed[:-1]
    return re.sub(r"&amp;", "&", trimmed, flags=re.IGNORECASE), raw_url[len(trimmed):]


def _trim_url(raw_url: str) -> str:
    return _split_url_suffix(raw_url)[0]


def extract_http_urls(text: str) -> tuple[str, ...]:
    seen: set[str] = set()
    urls: list[str] = []
    for match in _HTTP_URL_RE.finditer(text or ""):
        url = _trim_url(match.group(0))
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    return tuple(urls)


def is_source_bearing_tool(tool_name: str) -> bool:
    return (
        tool_name == "http_request"
        or tool_name == "spawn_web_task_result"
        or tool_name.startswith("mcp_")
    )


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _reference_map(
    agent,
    urls: Iterable[str],
    *,
    create: bool,
    source_kind: str = "",
    source_object_id: str = "",
) -> dict[str, str]:
    urls_by_hash = {_url_hash(url): url for url in urls}
    if not urls_by_hash:
        return {}

    references = list(
        PersistentAgentLinkReference.objects.filter(
            agent=agent,
            url_hash__in=urls_by_hash,
        )
    )
    found_hashes = {reference.url_hash for reference in references}
    if create:
        PersistentAgentLinkReference.objects.bulk_create(
            [
                PersistentAgentLinkReference(
                    agent=agent,
                    url=url,
                    url_hash=url_hash,
                    source_kind=source_kind,
                    source_object_id=str(source_object_id or ""),
                )
                for url_hash, url in urls_by_hash.items()
                if url_hash not in found_hashes
            ],
            ignore_conflicts=True,
        )
        references = list(
            PersistentAgentLinkReference.objects.filter(
                agent=agent,
                url_hash__in=urls_by_hash,
            )
        )

    return {
        reference.url: f"$[link:{reference.public_id}]"
        for reference in references
        if urls_by_hash.get(reference.url_hash) == reference.url
    }


def rewrite_prompt_urls(
    text: str,
    agent,
    *,
    create: bool,
    source_kind: str = "",
    source_object_id: str = "",
) -> str:
    """Replace visible URLs with durable references, degrading to raw URLs on DB failure."""
    urls = extract_http_urls(text)
    if not urls:
        return text
    try:
        references = _reference_map(
            agent,
            urls,
            create=create,
            source_kind=source_kind,
            source_object_id=source_object_id,
        )
    except DatabaseError:
        logger.warning("Failed to load link references for agent %s", getattr(agent, "id", None), exc_info=True)
        return text

    def replace(match: re.Match) -> str:
        raw_url = match.group(0)
        url, suffix = _split_url_suffix(raw_url)
        return f"{references.get(url, url)}{suffix}"

    return _HTTP_URL_RE.sub(replace, text)


def register_prompt_urls(
    text: str,
    agent,
    *,
    source_kind: str,
    source_object_id: str = "",
) -> None:
    """Register trusted source URLs without changing the inspectable source text."""
    urls = extract_http_urls(text)
    if not urls:
        return
    try:
        _reference_map(
            agent,
            urls,
            create=True,
            source_kind=source_kind,
            source_object_id=source_object_id,
        )
    except DatabaseError:
        logger.warning(
            "Failed to register link references for agent %s",
            getattr(agent, "id", None),
            exc_info=True,
        )


def _rendered_reference_id(url: str) -> str | None:
    parsed = urlsplit(url)
    public_site = urlsplit(settings.PUBLIC_SITE_URL)
    if parsed.scheme.lower() not in {"http", "https"} or parsed.netloc.lower() != public_site.netloc.lower():
        return None
    match = _RENDERED_REFERENCE_PATH_RE.search(parsed.path)
    if not match or parsed.query or parsed.fragment:
        return None
    return match.group(1).upper()


def resolve_link_references(text: str, agent) -> str:
    naked_match = _NAKED_REFERENCE_DESTINATION_RE.search(text or "")
    if naked_match:
        public_id = naked_match.group(1).upper()
        raise LinkReferenceResolutionError(
            f"A link reference is malformed. Use $[link:{public_id}] as the complete destination."
        )
    matches = list(_LINK_REFERENCE_RE.finditer(text or ""))
    if len(matches) != len(_LINK_REFERENCE_PREFIX_RE.findall(text or "")):
        raise LinkReferenceResolutionError(
            "A link reference is malformed. Reuse a provided $[link:id] value or omit the link."
        )
    rendered_references = {
        url: reference_id
        for url in extract_http_urls(text)
        if (reference_id := _rendered_reference_id(url)) is not None
    }
    if not matches and not rendered_references:
        return text

    reference_ids: set[str] = set()
    for match in matches:
        public_id = match.group(1).upper()
        if not _PUBLIC_ID_RE.fullmatch(public_id):
            raise LinkReferenceResolutionError(
                "A link reference is malformed. Reuse a provided $[link:id] value or omit the link."
            )
        reference_ids.add(public_id)

    lookup_ids = reference_ids | set(rendered_references.values())
    try:
        references = {
            reference.public_id: reference.url
            for reference in PersistentAgentLinkReference.objects.filter(
                agent=agent,
                public_id__in=lookup_ids,
            )
        }
    except DatabaseError as exc:
        raise LinkReferenceResolutionError(
            "Link references are temporarily unavailable. Retry the same message."
        ) from exc

    if not lookup_ids.issubset(references):
        raise LinkReferenceResolutionError(
            "A link reference is unavailable for this agent. Reuse a provided $[link:id] value or omit the link."
        )

    resolved = _LINK_REFERENCE_RE.sub(lambda match: references[match.group(1).upper()], text)
    if rendered_references:
        def replace_rendered(match: re.Match) -> str:
            url, suffix = _split_url_suffix(match.group(0))
            reference_id = rendered_references.get(url)
            return f"{references.get(reference_id, url)}{suffix}"

        resolved = _HTTP_URL_RE.sub(replace_rendered, resolved)
    return resolved


def resolve_link_reference_params(value, agent):
    """Resolve references used as complete tool parameter values, without rewriting authored prose."""
    if isinstance(value, dict):
        return {key: resolve_link_reference_params(item, agent) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_link_reference_params(item, agent) for item in value]
    if not isinstance(value, str):
        return value

    stripped = value.strip()
    if not (
        _LINK_REFERENCE_RE.fullmatch(stripped)
        or _rendered_reference_id(stripped) is not None
    ):
        return value
    resolved = resolve_link_references(stripped, agent)
    return f"{value[:len(value) - len(value.lstrip())]}{resolved}{value[len(value.rstrip()):]}"


def link_reference_error_response(exc: LinkReferenceResolutionError) -> dict[str, object]:
    return {"status": "error", "message": str(exc), "retryable": True}
