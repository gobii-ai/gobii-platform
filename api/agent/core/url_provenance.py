import html
import json
import re
from collections.abc import Iterable

_HTTP_URL_RE = re.compile(r'''https?://[^\s<>"'`\[\]\\]+''', re.IGNORECASE)
_TRAILING_PUNCTUATION = ".,;:!?"
_SOURCE_BEARING_TOOL_NAMES = frozenset({
    "http_request",
    "spawn_web_task",
    "spawn_web_task_result",
})


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


def _urls_from_tool_result(result: object) -> set[str]:
    if not isinstance(result, str):
        return _urls_from_json_value(result)
    try:
        payload = json.loads(result)
    except (json.JSONDecodeError, TypeError):
        return set(extract_http_urls(result))
    return _urls_from_json_value(payload)


def source_urls_from_tool_result(tool_name: str, result: object) -> frozenset[str]:
    if tool_name not in _SOURCE_BEARING_TOOL_NAMES and not tool_name.startswith("mcp_"):
        return frozenset()
    return frozenset(_urls_from_tool_result(result))


def trusted_urls_from_prompt(
    system_prompt: str,
    user_prompt: str,
    *,
    inbound_urls: Iterable[str] = (),
    source_result_urls: Iterable[str] = (),
) -> frozenset[str]:
    """Trust generated prompt sections plus sourced URLs visible in unified history."""
    opening = "<unified_history>"
    closing = "</unified_history>"
    start = user_prompt.find(opening)
    # Use the outer closing tag even when a message contains prompt-like markup.
    end = user_prompt.rfind(closing) if start >= 0 else -1
    generated_user_prompt = (
        user_prompt[:start] + user_prompt[end + len(closing):]
        if start >= 0 and end >= 0
        else user_prompt
    )
    rendered_urls = set(extract_http_urls(system_prompt))
    rendered_urls.update(extract_http_urls(user_prompt))
    trusted_urls = set(extract_http_urls(system_prompt))
    trusted_urls.update(extract_http_urls(generated_user_prompt))
    trusted_urls.update(rendered_urls.intersection({*inbound_urls, *source_result_urls}))
    return frozenset(trusted_urls)


def build_delivery_url_inventory(
    *,
    trusted_prompt_urls: Iterable[str] = (),
    run_source_urls: Iterable[str] = (),
) -> frozenset[str]:
    """Combine URLs trusted by this prompt render and this processing run."""
    urls = set(trusted_prompt_urls)
    urls.update(run_source_urls)
    return frozenset(urls)
