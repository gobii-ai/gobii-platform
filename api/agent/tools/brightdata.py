"""Native Bright Data search and webpage scraping tools."""

import json
import logging
import re
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
BRIGHTDATA_SEARCH_ENGINE_TOOL_NAME = "mcp_brightdata_search_engine"
BRIGHTDATA_SCRAPE_AS_MARKDOWN_TOOL_NAME = "mcp_brightdata_scrape_as_markdown"

_SEARCH_ENGINES = {"google", "bing", "yandex"}
_MARKDOWN_PARSER = MarkdownIt("commonmark")


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


def get_brightdata_scrape_as_markdown_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": BRIGHTDATA_SCRAPE_AS_MARKDOWN_TOOL_NAME,
            "description": (
                "Scrape a single webpage URL with advanced options for content extraction and get back the results "
                "in Markdown. This tool can unlock webpages that use bot detection or CAPTCHA. NOT for data files "
                "(.csv, .json, .xml, .txt, /api/) — use http_request instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "format": "uri"},
                },
                "required": ["url"],
            },
        },
    }


def _configuration_error() -> Optional[dict[str, Any]]:
    missing = []
    if not settings.BRIGHT_DATA_TOKEN:
        missing.append("BRIGHT_DATA_TOKEN")
    if not settings.BRIGHT_DATA_WEB_UNLOCKER_ZONE:
        missing.append("BRIGHT_DATA_WEB_UNLOCKER_ZONE")
    if not missing:
        return None
    return {
        "status": "error",
        "message": f"Bright Data is not configured. Set {', '.join(missing)} before using this tool.",
        "retryable": False,
    }


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
    return {
        "status": "error",
        "message": message,
        "status_code": response.status_code,
        "retryable": response.status_code == 429 or response.status_code >= 500,
    }


def _post_brightdata(payload: dict[str, Any]) -> tuple[Optional[str], Optional[dict[str, Any]]]:
    try:
        response = requests.post(
            BRIGHTDATA_API_URL,
            json=payload,
            headers=_request_headers(),
            timeout=settings.BRIGHT_DATA_REQUEST_TIMEOUT_SECONDS,
        )
    except Timeout:
        return None, {
            "status": "error",
            "message": "Bright Data API request timed out.",
            "retryable": True,
        }
    except RequestException as exc:
        logger.warning("Bright Data API request failed: %s", exc)
        return None, {
            "status": "error",
            "message": "Bright Data API request failed before receiving a response.",
            "retryable": True,
        }

    if response.status_code >= 400:
        return None, _response_error(response)
    return response.text, None


def _parse_cursor(cursor: Any) -> tuple[Optional[int], Optional[dict[str, Any]]]:
    if cursor in (None, ""):
        return 0, None
    if not isinstance(cursor, str):
        return None, {
            "status": "error",
            "message": "Bright Data search cursor must be a string page number.",
            "retryable": False,
        }
    match = re.match(r"^\s*([+-]?\d+)", cursor)
    if not match:
        return None, {
            "status": "error",
            "message": "Bright Data search cursor must begin with a page number.",
            "retryable": False,
        }
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
        return None, {
            "status": "error",
            "message": f"Unexpected non-JSON response from Bright Data for search_engine.{detail}",
            "retryable": False,
        }

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
    return None, {
        "status": "error",
        "message": "Bright Data returned an empty search_engine result.",
        "retryable": False,
    }


def _processed_scrape_response(response_text: str) -> tuple[Optional[str], Optional[dict[str, Any]]]:
    if response_text.strip():
        return _strip_markdown(response_text), None
    return None, {
        "status": "error",
        "message": "Bright Data returned an empty scrape_as_markdown result.",
        "retryable": False,
    }


def execute_brightdata_search_engine(_agent: Any, params: dict[str, Any]) -> dict[str, Any]:
    query = params.get("query") if isinstance(params, dict) else None
    if not isinstance(query, str) or not query.strip():
        return {
            "status": "error",
            "message": "Bright Data search requires a non-empty query.",
            "retryable": False,
        }

    engine = params.get("engine") or "google"
    if not isinstance(engine, str) or engine not in _SEARCH_ENGINES:
        return {
            "status": "error",
            "message": "Bright Data search engine must be google, bing, or yandex.",
            "retryable": False,
        }

    geo_location = params.get("geo_location")
    if geo_location is not None and (
        not isinstance(geo_location, str)
        or len(geo_location) != 2
        or not geo_location.isalpha()
    ):
        return {
            "status": "error",
            "message": "Bright Data geo_location must be a two-letter country code.",
            "retryable": False,
        }

    target_url, url_error = _search_url(engine, query, params.get("cursor"), geo_location)
    if url_error:
        return url_error

    is_google = engine == "google"
    if is_google:
        result, search_error = _execute_with_zone_fallback(
            {
                "url": f"{target_url}&brd_json=1",
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


def _validate_scrape_url(value: Any) -> Optional[dict[str, Any]]:
    if not isinstance(value, str):
        return {
            "status": "error",
            "message": "Bright Data scrape requires a valid HTTP or HTTPS URL.",
            "retryable": False,
        }
    try:
        parsed = urlparse(value)
    except ValueError:
        parsed = None
    if not parsed or parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return {
            "status": "error",
            "message": "Bright Data scrape requires a valid HTTP or HTTPS URL.",
            "retryable": False,
        }
    if parsed.path.lower().endswith(".pdf"):
        return {
            "status": "error",
            "message": (
                "PDF scraping is not supported for Bright Data snapshots. "
                "Use spawn_web_task to read PDFs instead."
            ),
            "retryable": False,
        }
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
