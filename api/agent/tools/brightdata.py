"""Native Bright Data tools."""

import json
import logging
import re
import time
from typing import Any, Callable, Optional
from urllib.parse import quote, urlparse

import requests
from django.conf import settings
from markdown_it import MarkdownIt
from markdown_it.token import Token
from requests import Response
from requests.exceptions import RequestException, Timeout


logger = logging.getLogger(__name__)

BRIGHTDATA_API_URL = "https://api.brightdata.com/request"
BRIGHTDATA_DATASET_SCRAPE_URL = "https://api.brightdata.com/datasets/v3/scrape"
BRIGHTDATA_DATASET_PROGRESS_URL = "https://api.brightdata.com/datasets/v3/progress"
BRIGHTDATA_DATASET_SNAPSHOT_URL = "https://api.brightdata.com/datasets/v3/snapshot"
BRIGHTDATA_LINKEDIN_PERSON_PROFILE_DATASET_ID = "gd_l1viktl72bvl7bjuj0"
BRIGHTDATA_SEARCH_ENGINE_TOOL_NAME = "mcp_brightdata_search_engine"
BRIGHTDATA_SCRAPE_AS_MARKDOWN_TOOL_NAME = "mcp_brightdata_scrape_as_markdown"
BRIGHTDATA_LINKEDIN_PERSON_PROFILE_TOOL_NAME = "mcp_brightdata_web_data_linkedin_person_profile"

_SEARCH_ENGINES = {"google", "bing", "yandex"}
_MARKDOWN_PARSER = MarkdownIt("commonmark")
_DATASET_POLL_INTERVAL_SECONDS = 1.0
_LINKEDIN_PERSON_FIELDS_TO_STRIP = {
    "description_html",
    "company_logo_url",
    "institute_logo_url",
    "banner_image",
    "default_avatar",
    "image_url",
    "image",
    "img",
    "people_also_viewed",
}


def _error(message: str, *, retryable: bool = False, **details: Any) -> dict[str, Any]:
    return {"status": "error", "message": message, "retryable": retryable, **details}


def get_brightdata_search_engine_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": BRIGHTDATA_SEARCH_ENGINE_TOOL_NAME,
            "description": (
                "Scrape search results from Google, Bing or Yandex. Returns SERP results in JSON or Markdown "
                "(URL, title, description). Ideal for gathering current information, news, and detailed search "
                "results."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "engine": {
                        "type": "string",
                        "enum": ["google", "bing", "yandex"],
                        "default": "google",
                    },
                    "cursor": {
                        "type": "string",
                        "description": "Pagination cursor for next page",
                    },
                    "geo_location": {
                        "type": "string",
                        "minLength": 2,
                        "maxLength": 2,
                        "description": '2-letter country code for geo-targeted results (e.g., "us", "uk")',
                    },
                },
                "required": ["query"],
            },
        },
    }


def _single_url_tool(name: str, description: str) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string", "format": "uri"}},
                "required": ["url"],
            },
        },
    }


def get_brightdata_scrape_as_markdown_tool() -> dict[str, Any]:
    return _single_url_tool(
        BRIGHTDATA_SCRAPE_AS_MARKDOWN_TOOL_NAME,
        "Scrape a single webpage URL with advanced options for content extraction and get back the results in "
        "Markdown. This tool can unlock webpages that use bot detection or CAPTCHA. NOT for data files (.csv, "
        ".json, .xml, .txt, /api/) — use http_request instead.",
    )


def get_brightdata_linkedin_person_profile_tool() -> dict[str, Any]:
    return _single_url_tool(
        BRIGHTDATA_LINKEDIN_PERSON_PROFILE_TOOL_NAME,
        "Quickly read structured LinkedIn people profile data. This can be a cache lookup, so it can be more "
        "reliable than scraping.",
    )


def _configuration_error(*, require_web_unlocker_zone: bool = True) -> Optional[dict[str, Any]]:
    missing = []
    if not settings.BRIGHT_DATA_TOKEN:
        missing.append("BRIGHT_DATA_TOKEN")
    if require_web_unlocker_zone and not settings.BRIGHT_DATA_WEB_UNLOCKER_ZONE:
        missing.append("BRIGHT_DATA_WEB_UNLOCKER_ZONE")
    if not missing:
        return None
    return _error(f"Bright Data is not configured. Set {', '.join(missing)} before using this tool.")


def _request_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.BRIGHT_DATA_TOKEN}",
        "Content-Type": "application/json",
        "User-Agent": "gobii-platform/brightdata-native",
    }


def _response_error(response: Response) -> dict[str, Any]:
    detail = (response.text or "").strip()
    if len(detail) > 500:
        detail = f"{detail[:500]}..."
    message = f"Bright Data API returned HTTP {response.status_code}."
    if detail:
        message = f"{message} {detail}"
    return _error(
        message,
        status_code=response.status_code,
        retryable=response.status_code == 429 or response.status_code >= 500,
    )


def _request_brightdata(
    method: str,
    url: str,
    *,
    params: Optional[dict[str, Any]] = None,
    payload: Any = None,
) -> tuple[Optional[Response], Optional[dict[str, Any]]]:
    request = requests.post if method == "POST" else requests.get
    request_kwargs = {
        "headers": _request_headers(),
        "timeout": settings.BRIGHT_DATA_REQUEST_TIMEOUT_SECONDS,
    }
    if params is not None:
        request_kwargs["params"] = params
    if payload is not None:
        request_kwargs["json"] = payload

    try:
        response = request(url, **request_kwargs)
    except Timeout:
        return None, _error("Bright Data API request timed out.", retryable=True)
    except RequestException as exc:
        logger.warning("Bright Data API request failed: %s", exc)
        return None, _error("Bright Data API request failed before receiving a response.", retryable=True)

    if response.status_code >= 400:
        return None, _response_error(response)
    return response, None


def _post_brightdata(payload: dict[str, Any]) -> tuple[Optional[str], Optional[dict[str, Any]]]:
    response, request_error = _request_brightdata("POST", BRIGHTDATA_API_URL, payload=payload)
    if request_error:
        return None, request_error
    return response.text, None


def _parse_cursor(cursor: Any) -> tuple[Optional[int], Optional[dict[str, Any]]]:
    if cursor in (None, ""):
        return 0, None
    if not isinstance(cursor, str):
        return None, _error("Bright Data search cursor must be a string page number.")
    match = re.match(r"^\s*(\d+)", cursor)
    if not match:
        return None, _error("Bright Data search cursor must begin with a non-negative page number.")
    return int(match.group(1)), None


def _search_url(
    engine: str,
    query: str,
    cursor: Any = None,
    geo_location: Optional[str] = None,
) -> tuple[Optional[str], Optional[dict[str, Any]]]:
    page, cursor_error = _parse_cursor(cursor)
    if cursor_error:
        return None, cursor_error

    encoded_query = quote(query, safe="-_.!~*'()")
    if engine == "yandex":
        return f"https://yandex.com/search/?text={encoded_query}&p={page}", None
    if engine == "bing":
        return f"https://www.bing.com/search?q={encoded_query}&first={(page * 10) + 1}", None

    location = f"&gl={geo_location}" if geo_location else ""
    return f"https://www.google.com/search?q={encoded_query}&start={page * 10}{location}", None


def _clean_google_search_response(
    response_text: str,
) -> tuple[Optional[str], Optional[dict[str, Any]]]:
    try:
        payload = json.loads(response_text)
    except json.JSONDecodeError:
        snippet = response_text.strip()[:300]
        detail = (
            f" Response snippet: {snippet}..."
            if len(response_text.strip()) > 300
            else f" Response snippet: {snippet}"
        )
        if not snippet:
            detail = ""
        return None, _error(f"Unexpected non-JSON response from Bright Data for search_engine.{detail}")

    organic = payload.get("organic") if isinstance(payload, dict) else None
    cleaned = []
    for entry in organic if isinstance(organic, list) else []:
        if not isinstance(entry, dict):
            continue
        link = entry.get("link", "")
        title = entry.get("title", "")
        description = entry.get("description", "")
        link = link.strip() if isinstance(link, str) else ""
        title = title.strip() if isinstance(title, str) else ""
        description = description.strip() if isinstance(description, str) else ""
        if link and title:
            cleaned.append({"link": link, "title": title, "description": description})
    return json.dumps({"organic": cleaned}, ensure_ascii=False, indent=2), None


def _execute_with_zone_fallback(
    payload: dict[str, Any],
    operation: str,
    process_response: Callable[[str], tuple[Optional[str], Optional[dict[str, Any]]]],
) -> tuple[Optional[str], Optional[dict[str, Any]]]:
    config_error = _configuration_error()
    if config_error:
        return None, config_error

    primary_zone = settings.BRIGHT_DATA_WEB_UNLOCKER_ZONE
    fallback_zone = settings.BRIGHT_DATA_WEB_UNLOCKER_ZONE_FALLBACK.strip()
    zones = [primary_zone]
    if fallback_zone and fallback_zone != primary_zone:
        zones.append(fallback_zone)

    last_error = None
    for attempt, zone in enumerate(zones):
        response_text, request_error = _post_brightdata({**payload, "zone": zone})
        if request_error:
            last_error = request_error
        else:
            result, processing_error = process_response(response_text or "")
            if not processing_error:
                if attempt:
                    logger.info("Bright Data %s succeeded with the fallback zone", operation)
                return result, None
            last_error = processing_error

        if attempt == 0 and len(zones) > 1:
            logger.warning("Bright Data %s failed on the primary zone; retrying with the fallback zone", operation)

    if len(zones) == 1:
        return None, last_error

    combined_error = dict(last_error or {})
    combined_error.setdefault("status", "error")
    combined_error["message"] = (
        f"Bright Data {operation} failed on both primary and fallback zones. "
        f"Last failure: {combined_error.get('message', 'unknown')}"
    )
    return None, combined_error


def _non_empty_search_response(response_text: str) -> tuple[Optional[str], Optional[dict[str, Any]]]:
    if response_text.strip():
        return response_text, None
    return None, _error("Bright Data returned an empty search_engine result.")


def _processed_scrape_response(response_text: str) -> tuple[Optional[str], Optional[dict[str, Any]]]:
    if response_text.strip():
        return _strip_markdown(response_text), None
    return None, _error("Bright Data returned an empty scrape_as_markdown result.")


def _parse_json_response(
    response: Response,
    operation: str,
) -> tuple[Optional[Any], Optional[dict[str, Any]]]:
    response_text = response.text or ""
    if not response_text.strip():
        return None, _error(f"Bright Data returned an empty {operation} response.")
    try:
        return json.loads(response_text), None
    except json.JSONDecodeError:
        return None, _error(f"Bright Data returned malformed JSON for {operation}.")


def _clean_linkedin_person_profile(node: Any) -> Any:
    if isinstance(node, list):
        return [_clean_linkedin_person_profile(value) for value in node if value is not None]
    if isinstance(node, dict):
        return {
            key: _clean_linkedin_person_profile(value)
            for key, value in node.items()
            if value is not None
            and key not in _LINKEDIN_PERSON_FIELDS_TO_STRIP
            and not key.endswith("_html")
            and not key.endswith("_img")
        }
    return node


def _linkedin_person_profile_result(payload: Any) -> tuple[Optional[str], Optional[dict[str, Any]]]:
    if not isinstance(payload, (dict, list)):
        return None, _error("Bright Data returned an unexpected LinkedIn person profile response.")
    cleaned = _clean_linkedin_person_profile(payload)
    return json.dumps(cleaned, ensure_ascii=False, separators=(",", ":")), None


def _poll_linkedin_person_profile_snapshot(
    snapshot_id: str,
) -> tuple[Optional[str], Optional[dict[str, Any]]]:
    escaped_snapshot_id = quote(snapshot_id, safe="")
    deadline = time.monotonic() + max(0.0, settings.BRIGHT_DATA_DATASET_POLL_TIMEOUT_SECONDS)

    while True:
        progress_response, progress_error = _request_brightdata(
            "GET",
            f"{BRIGHTDATA_DATASET_PROGRESS_URL}/{escaped_snapshot_id}",
        )
        if progress_error:
            if not progress_error.get("retryable"):
                return None, progress_error
        else:
            progress, parse_error = _parse_json_response(progress_response, "dataset progress")
            if parse_error:
                return None, parse_error
            if not isinstance(progress, dict):
                return None, _error("Bright Data returned an unexpected dataset progress response.")

            status = progress.get("status")
            if status == "failed":
                detail = progress.get("error_message") or progress.get("message") or "unknown failure"
                return None, _error(f"Bright Data LinkedIn person profile snapshot failed: {detail}")
            if status == "ready":
                snapshot_response, snapshot_error = _request_brightdata(
                    "GET",
                    f"{BRIGHTDATA_DATASET_SNAPSHOT_URL}/{escaped_snapshot_id}",
                    params={"format": "json"},
                )
                if snapshot_error:
                    if snapshot_error.get("status_code") != 409 and not snapshot_error.get("retryable"):
                        return None, snapshot_error
                elif snapshot_response.status_code != 202:
                    snapshot, snapshot_parse_error = _parse_json_response(
                        snapshot_response,
                        "LinkedIn person profile snapshot",
                    )
                    if snapshot_parse_error:
                        return None, snapshot_parse_error
                    return _linkedin_person_profile_result(snapshot)
            elif status not in {"starting", "running"}:
                return None, _error(f"Bright Data returned unknown dataset progress status: {status!r}.")

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None, _error("Bright Data LinkedIn person profile snapshot timed out.", retryable=True)
        time.sleep(min(_DATASET_POLL_INTERVAL_SECONDS, remaining))


def execute_brightdata_search_engine(_agent: Any, params: dict[str, Any]) -> dict[str, Any]:
    query = params.get("query") if isinstance(params, dict) else None
    if not isinstance(query, str) or not query.strip():
        return _error("Bright Data search requires a non-empty query.")

    engine = params.get("engine") or "google"
    if not isinstance(engine, str) or engine not in _SEARCH_ENGINES:
        return _error("Bright Data search engine must be google, bing, or yandex.")

    geo_location = params.get("geo_location")
    if geo_location is not None and (
        not isinstance(geo_location, str)
        or len(geo_location) != 2
        or not geo_location.isalpha()
    ):
        return _error("Bright Data geo_location must be a two-letter country code.")

    target_url, url_error = _search_url(engine, query, params.get("cursor"), geo_location)
    if url_error:
        return url_error

    is_google = engine == "google"
    if is_google:
        result, search_error = _execute_with_zone_fallback(
            {
                "url": target_url,
                "format": "raw",
                "data_format": "parsed_light",
            },
            "Google search",
            _clean_google_search_response,
        )
    else:
        result, search_error = _execute_with_zone_fallback(
            {
                "url": target_url,
                "format": "raw",
                "data_format": "markdown",
            },
            f"{engine.title()} search",
            _non_empty_search_response,
        )
    if search_error:
        return search_error
    return {"status": "success", "result": result}


def _render_inline_tokens(tokens: list[Token]) -> str:
    output: list[str] = []
    link_stack: list[tuple[str, str]] = []
    for token in tokens:
        if token.type == "text":
            output.append(token.content)
        elif token.type in {"softbreak", "hardbreak"}:
            output.append("\n")
        elif token.type == "code_inline":
            markup = token.markup or "`"
            output.append(f"{markup}{token.content}{markup}")
        elif token.type == "link_open":
            href = token.attrGet("href") or ""
            title = token.attrGet("title") or ""
            link_stack.append((href, title))
            output.append("[")
        elif token.type == "link_close":
            href, title = link_stack.pop() if link_stack else ("", "")
            title_suffix = f' "{title}"' if title else ""
            output.append(f"]({href}{title_suffix})")
        elif token.type == "image":
            output.append(token.content)
    return "".join(output)


def _strip_markdown(markdown: str) -> str:
    output: list[str] = []
    for token in _MARKDOWN_PARSER.parse(markdown):
        if token.type == "inline":
            output.append(_render_inline_tokens(token.children or []))
        elif token.type in {"heading_close", "paragraph_close", "list_item_close", "blockquote_close"}:
            output.append("\n\n")
        elif token.type == "fence":
            markup = token.markup or "```"
            info = token.info.strip()
            output.append(f"{markup}{info}\n{token.content}{markup}\n\n")
        elif token.type == "code_block":
            output.append(f"```\n{token.content}```\n\n")
    return re.sub(r"\n{3,}", "\n\n", "".join(output)).rstrip() + "\n"


def _parse_http_url(value: Any) -> Any:
    if not isinstance(value, str):
        return None
    try:
        parsed = urlparse(value)
    except ValueError:
        return None
    return parsed if parsed.scheme in {"http", "https"} and parsed.netloc else None


def _validate_scrape_url(value: Any) -> Optional[dict[str, Any]]:
    parsed = _parse_http_url(value)
    if not parsed:
        return _error("Bright Data scrape requires a valid HTTP or HTTPS URL.")
    if parsed.path.lower().endswith(".pdf"):
        return _error(
            "PDF scraping is not supported for Bright Data snapshots. Use spawn_web_task to read PDFs instead."
        )
    return None


def _validate_linkedin_person_profile_url(value: Any) -> Optional[dict[str, Any]]:
    parsed = _parse_http_url(value)
    if not parsed:
        return _error("Bright Data LinkedIn person profile requires a valid HTTP or HTTPS URL.")
    domain = parsed.hostname.lower() if parsed.hostname else ""
    if domain != "linkedin.com" and not domain.endswith(".linkedin.com"):
        return _error("Bright Data LinkedIn person profile requires a valid LinkedIn URL.")
    return None


def execute_brightdata_scrape_as_markdown(_agent: Any, params: dict[str, Any]) -> dict[str, Any]:
    url = params.get("url") if isinstance(params, dict) else None
    validation_error = _validate_scrape_url(url)
    if validation_error:
        return validation_error

    result, scrape_error = _execute_with_zone_fallback(
        {
            "url": url,
            "format": "raw",
            "data_format": "markdown",
        },
        "markdown scrape",
        _processed_scrape_response,
    )
    if scrape_error:
        return scrape_error
    return {"status": "success", "result": result}


def execute_brightdata_linkedin_person_profile(_agent: Any, params: dict[str, Any]) -> dict[str, Any]:
    url = params.get("url") if isinstance(params, dict) else None
    validation_error = _validate_linkedin_person_profile_url(url)
    if validation_error:
        return validation_error

    config_error = _configuration_error(require_web_unlocker_zone=False)
    if config_error:
        return config_error

    response, request_error = _request_brightdata(
        "POST",
        BRIGHTDATA_DATASET_SCRAPE_URL,
        params={
            "dataset_id": BRIGHTDATA_LINKEDIN_PERSON_PROFILE_DATASET_ID,
            "format": "json",
            "include_errors": True,
        },
        payload={"input": [{"url": url}]},
    )
    if request_error:
        return request_error

    payload, parse_error = _parse_json_response(response, "LinkedIn person profile")
    if parse_error:
        return parse_error

    has_snapshot_id = isinstance(payload, dict) and "snapshot_id" in payload
    snapshot_id = payload.get("snapshot_id") if has_snapshot_id else None
    if response.status_code == 202 and not has_snapshot_id:
        return _error("Bright Data accepted the LinkedIn person profile request without returning a snapshot ID.")
    if has_snapshot_id:
        if not isinstance(snapshot_id, str) or not snapshot_id.strip():
            return _error("Bright Data returned an invalid LinkedIn person profile snapshot ID.")
        result, snapshot_error = _poll_linkedin_person_profile_snapshot(snapshot_id)
        if snapshot_error:
            return snapshot_error
        return {"status": "success", "result": result}

    result, result_error = _linkedin_person_profile_result(payload)
    if result_error:
        return result_error
    return {"status": "success", "result": result}
