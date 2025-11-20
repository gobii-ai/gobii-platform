"""
HTTP request tool for persistent agents.

This module provides HTTP request functionality for persistent agents,
including tool definition and execution logic.
"""

import json
import logging
import re
from typing import Dict, Any

import requests
from requests.exceptions import RequestException

from django.conf import settings

from ...models import PersistentAgent, PersistentAgentSecret
from ...proxy_selection import select_proxy_for_persistent_agent

logger = logging.getLogger(__name__)


def get_http_request_tool() -> Dict[str, Any]:
    """Return the http_request tool definition for the LLM."""
    return {
        "type": "function",
        "function": {
            "name": "http_request",
            "description": (
                "Perform a fast and efficient HTTP request to fetch raw structured data (JSON, XML, CSV) or interact with APIs. "
                "This is the PREFERRED tool for programmatic data retrieval from known endpoints. "
                "Do NOT use this when the task is to read or verify what appears on a webpage; use `spawn_web_task` for user-visible pages even if they are simple HTML. "
                "The URL, headers, and body can include secret placeholders using the unique pattern <<<my_api_key>>>. These placeholders will be replaced with the corresponding secret values at execution time. The response is truncated to 30KB and binary bodies are omitted. You may need to look up API docs using the search_web tool."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "method": {"type": "string", "description": "HTTP method e.g. GET, POST."},
                    "url": {"type": "string", "description": "Full URL to request."},
                    "headers": {"type": "object", "description": "Optional HTTP headers to include in the request."},
                    "body": {"type": "string", "description": "Optional request body (for POST/PUT)."},
                    "range": {"type": "string", "description": "Optional Range header value, e.g. 'bytes=0-1023'."}
                },
                "required": ["method", "url"],
            },
        },
    }


def execute_http_request(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    """Perform a generic HTTP request with safety guards.

    Supports any HTTP method (GET, POST, PUT, DELETE, etc.). The agent can also
    supply custom headers and an optional request body. To limit prompt size and
    avoid leaking binary data, we:

    1. Cap the response body to 30 KB (first bytes only).
    2. Detect non-textual content via the Content-Type header (anything not
       starting with ``text/`` or common JSON / XML / JavaScript MIME types).
       Binary responses are replaced with a placeholder string indicating the
       size and content-type.
    3. Allow ranged requests by accepting a ``range`` parameter which, if
       provided, is mapped to the ``Range`` HTTP header (e.g. "bytes=0-1023").
    4. Uses a proxy server when one is configured. In proprietary mode a proxy
       is required; community mode falls back to a direct request if none is
       available.
    """
    method = (params.get("method") or "GET").upper()
    url = params.get("url")
    if not url:
        return {"status": "error", "message": "Missing required parameter: url"}

    # Log original request details (before secret substitution)
    logger.info(
        "Agent %s executing HTTP request: %s %s",
        agent.id, method, url
    )

    # Select proxy server - enforced in proprietary mode, optional in community
    proxy_required = getattr(settings, "GOBII_PROPRIETARY_MODE", False)
    proxy_server = None
    try:
        proxy_server = select_proxy_for_persistent_agent(
            agent,
            allow_no_proxy_in_debug=False,  # Proprietary mode requires proxies
        )
    except RuntimeError as e:
        if proxy_required:
            return {"status": "error", "message": f"No proxy server available: {e}"}
        logger.warning(
            "Agent %s proceeding without proxy (community mode): %s",
            agent.id,
            e,
        )

    if proxy_required and not proxy_server:
        return {"status": "error", "message": "No proxy server available"}

    proxies = None
    if proxy_server:
        proxy_url = proxy_server.proxy_url
        proxies = {
            "http": proxy_url,
            "https": proxy_url,
        }
    else:
        logger.info(
            "Agent %s executing HTTP request without proxy (community mode).",
            agent.id,
        )

    headers = params.get("headers") or {}
    # Normalise header keys to str in case they come in as other types
    headers = {str(k): str(v) for k, v in headers.items()}

    rng = params.get("range")
    if rng:
        headers["Range"] = str(rng)

    body = params.get("body")  # Optional – may be None

    # ---------------- Secret placeholder substitution ---------------- #
    # Build a mapping of secret_key -> decrypted value for this agent (exclude requested secrets)
    secret_map = {
        s.key: s.get_value()
        for s in PersistentAgentSecret.objects.filter(agent=agent, requested=False)
    }

    UNIQUE_PATTERN_RE = re.compile(r"<<<\s*([A-Za-z0-9_]+)\s*>>>")
    
    # Track which placeholders we find for logging
    found_placeholders = set()

    def _replace_placeholders(obj):
        """Recursively replace <<<secret_key>>> placeholders in strings and collections."""
        if isinstance(obj, str):
            def _repl(match):
                key = match.group(1)
                found_placeholders.add(key)
                return secret_map.get(key, match.group(0))

            new_val = UNIQUE_PATTERN_RE.sub(_repl, obj)
            # If the whole string exactly matches a secret key, replace it outright (edge case)
            if new_val in secret_map:
                found_placeholders.add(new_val)
                return secret_map[new_val]
            return new_val
        elif isinstance(obj, dict):
            return {k: _replace_placeholders(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [_replace_placeholders(v) for v in obj]
        else:
            return obj

    # Store original values for logging (before replacement)
    original_headers = headers.copy() if headers else {}
    original_body = body

    # Log headers and body BEFORE substitution (contains only placeholders, never real secrets)
    if original_headers:
        header_preview = str(original_headers)
        if len(header_preview) > 500:
            header_preview = header_preview[:500] + f"... [TRUNCATED, total {len(header_preview)} chars]"
        logger.debug("Agent %s HTTP request headers (pre-sub): %s", agent.id, header_preview)

    if original_body is not None:
        body_preview = str(original_body)
        if len(body_preview) > 500:
            body_preview = body_preview[:500] + f"... [TRUNCATED, total {len(body_preview)} chars]"
        logger.debug("Agent %s HTTP request body (pre-sub): %s", agent.id, body_preview)

    url = _replace_placeholders(url)
    headers = {k: _replace_placeholders(v) for k, v in headers.items()}
    body = _replace_placeholders(body)

    # Log secret placeholder usage (without actual values)
    if found_placeholders:
        logger.info(
            "Agent %s HTTP request used secret placeholders: %s",
            agent.id, ", ".join(sorted(found_placeholders))
        )
    
    # Log sanitized headers (mask values that might contain secrets)
    sanitized_headers = {}
    for k, v in original_headers.items():
        # Check if this header likely contains a secret (common auth headers)
        if any(auth_header in k.lower() for auth_header in ['authorization', 'api-key', 'x-api-key', 'token', 'bearer']):
            sanitized_headers[k] = "[REDACTED]"
        elif len(str(v)) > 100:  # Long values might be tokens
            sanitized_headers[k] = f"[TRUNCATED {len(str(v))} chars]"
        else:
            sanitized_headers[k] = str(v)
    
    if sanitized_headers:
        logger.debug(
            "Agent %s HTTP request headers: %s",
            agent.id, sanitized_headers
        )

    # Log body info (truncated and sanitized)
    if original_body:
        body_str = str(original_body)
        # Check for potential secrets in body
        if any(pattern in body_str.lower() for pattern in ['password', 'token', 'key', 'secret', 'auth']):
            body_info = f"[BODY CONTAINS POTENTIAL SECRETS - {len(body_str)} chars]"
        else:
            body_info = body_str[:200]  # Truncate to 200 chars
            if len(body_str) > 200:
                body_info += f"... [TRUNCATED, total {len(body_str)} chars]"
        logger.debug(
            "Agent %s HTTP request body: %s",
            agent.id, body_info
        )

    # If body is still a dict or list, JSON-encode it for transmission
    if isinstance(body, (dict, list)):
        body = json.dumps(body)

    # Safety: timeouts to avoid hanging
    timeout = 15  # seconds

    request_kwargs = {
        "headers": headers,
        "data": body,
        "stream": True,
        "timeout": timeout,
    }

    if proxies:
        request_kwargs["proxies"] = proxies

    try:
        # Stream to avoid downloading huge bodies – we'll manually truncate
        resp = requests.request(
            method,
            url,
            **request_kwargs,
        )
    except RequestException as e:
        return {"status": "error", "message": f"HTTP request failed: {e}"}

    # Read up to 30 KB
    max_bytes = 30 * 1024  # 30 KB
    content_chunks = []
    bytes_read = 0
    try:
        for chunk in resp.iter_content(chunk_size=1024):
            if not chunk:
                break
            if bytes_read + len(chunk) > max_bytes:
                chunk = chunk[: max_bytes - bytes_read]
            content_chunks.append(chunk)
            bytes_read += len(chunk)
            if bytes_read >= max_bytes:
                break
    finally:
        resp.close()

    content_bytes = b"".join(content_chunks)
    truncated = (
        bytes_read >= max_bytes or (
            resp.headers.get("Content-Length") and int(resp.headers["Content-Length"]) > bytes_read
        )
    )

    # Determine if we should treat content as text
    content_type = (resp.headers.get("Content-Type") or "").lower()
    is_textual = (
        content_type.startswith("text/")
        or "json" in content_type
        or "javascript" in content_type
        or "xml" in content_type
    )

    if is_textual:
        try:
            content_str = content_bytes.decode("utf-8", errors="replace")
        except Exception:
            content_str = content_bytes.decode(errors="replace")
        if truncated:
            content_str += "\n\n[Content truncated to 30KB]"
    else:
        size_hint = resp.headers.get("Content-Length", f"{bytes_read}+")
        content_str = f"[Binary content omitted – {content_type or 'unknown type'}, length ≈ {size_hint} bytes]"

    # Log response details
    response_size = len(content_str) if isinstance(content_str, str) else len(str(content_str))
    logger.info(
        "Agent %s HTTP response: %s %s - Status: %d, Size: %d chars%s",
        agent.id, method, url, resp.status_code, response_size,
        " (truncated)" if truncated else ""
    )
    
    return {
        "status": "ok",
        "status_code": resp.status_code,
        "headers": dict(resp.headers),
        "content": content_str,
        "proxy_used": str(proxy_server) if proxy_server else None,
    }
