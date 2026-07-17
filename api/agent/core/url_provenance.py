import html
import json
import re
from collections.abc import Iterable

from api.models import PersistentAgent, PersistentAgentMessage, PersistentAgentToolCall


_HTTP_URL_RE = re.compile(r'''https?://[^\s<>"'`\[\]\\]+''', re.IGNORECASE)
_TRAILING_PUNCTUATION = ".,;:!?"


def _trim_url(raw_url: str) -> str:
    url = html.unescape(raw_url).rstrip(_TRAILING_PUNCTUATION)
    pairs = (("(", ")"), ("[", "]"), ("{", "}"))
    changed = True
    while changed and url:
        changed = False
        for opening, closing in pairs:
            if url.endswith(closing) and url.count(closing) > url.count(opening):
                url = url[:-1]
                changed = True
    return url


def extract_http_urls(text: str) -> tuple[str, ...]:
    seen: set[str] = set()
    urls: list[str] = []
    for match in _HTTP_URL_RE.finditer(text or ""):
        url = _trim_url(match.group(0))
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    return tuple(urls)


def unexpected_delivery_urls(body: str, allowed_urls: Iterable[str]) -> tuple[str, ...]:
    allowed = set(allowed_urls)
    return tuple(
        url
        for url in extract_http_urls(body)
        if url not in allowed
    )


def _urls_from_json_value(value: object) -> set[str]:
    if isinstance(value, str):
        return set(extract_http_urls(value))
    if isinstance(value, dict):
        urls: set[str] = set()
        for child in value.values():
            urls.update(_urls_from_json_value(child))
        return urls
    if isinstance(value, list):
        urls = set()
        for child in value:
            urls.update(_urls_from_json_value(child))
        return urls
    return set()


def _urls_from_tool_result(result: str) -> set[str]:
    try:
        payload = json.loads(result)
    except (json.JSONDecodeError, TypeError):
        return set(extract_http_urls(result))
    return _urls_from_json_value(payload)


def build_delivery_url_inventory(
    agent: PersistentAgent,
    *,
    system_prompt: str = "",
) -> frozenset[str]:
    """Collect literal source URLs without trusting prior agent-authored messages."""
    urls = set(extract_http_urls(system_prompt))
    urls.update(extract_http_urls(agent.charter or ""))

    inbound_messages = (
        PersistentAgentMessage.objects.filter(
            owner_agent=agent,
            is_outbound=False,
            body__icontains="http",
        )
        .values_list("body", flat=True)
        .iterator(chunk_size=200)
    )
    for body in inbound_messages:
        urls.update(extract_http_urls(body))

    tool_results = (
        PersistentAgentToolCall.objects.filter(
            step__agent=agent,
            result__icontains="http",
        )
        # SQLite output is derived from agent-authored SQL. Trusting it would let
        # a model turn URL components into a new URL and then cite its own query.
        .exclude(tool_name="sqlite_batch")
        .values_list("result", flat=True)
        .iterator(chunk_size=100)
    )
    for result in tool_results:
        urls.update(_urls_from_tool_result(result))

    skill_instructions = (
        agent.skills.filter(instructions__icontains="http")
        .values_list("instructions", flat=True)
        .iterator(chunk_size=100)
    )
    for instructions in skill_instructions:
        urls.update(extract_http_urls(instructions))

    return frozenset(urls)
