"""
HTTP request tool for persistent agents.

This module provides HTTP request functionality for persistent agents,
including tool definition and execution logic.
"""

import json
import logging
import mimetypes
import os
import re
import urllib.parse
from typing import Dict, Any

import requests
from requests.exceptions import RequestException

from django.conf import settings

from ...models import PersistentAgent, PersistentAgentSecret
from ...proxy_selection import select_proxy_for_persistent_agent
from ..files.filespace_service import DOWNLOADS_DIR_NAME, write_bytes_to_dir

logger = logging.getLogger(__name__)
RESPONSE_MAX_BYTES = 5 * 1024 * 1024
PREVIEW_MAX_BYTES = RESPONSE_MAX_BYTES
DOWNLOAD_CHUNK_SIZE = 64 * 1024


class _ResponseBodyResult:
    def __init__(
        self,
        content_bytes: bytes,
        preview_bytes: bytes,
        total_bytes: int,
        truncated: bool,
        over_limit: bool,
    ) -> None:
        self.content_bytes = content_bytes
        self.preview_bytes = preview_bytes
        self.total_bytes = total_bytes
        self.truncated = truncated
        self.over_limit = over_limit


def _coerce_optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() == "true"
    return None


def _decode_filename_star(value: str) -> str:
    value = value.strip().strip('"')
    charset, sep, encoded = value.partition("''")
    if sep:
        if not charset:
            return urllib.parse.unquote(encoded)
        try:
            return urllib.parse.unquote(encoded, encoding=charset)
        except (LookupError, UnicodeDecodeError):
            return urllib.parse.unquote(encoded)
    return urllib.parse.unquote(value)


def _extract_filename_from_disposition(content_disposition: str | None) -> str | None:
    if not content_disposition:
        return None
    filename = None
    filename_star = None
    for part in content_disposition.split(";"):
        part = part.strip()
        lower = part.lower()
        if lower.startswith("filename*="):
            value = part.split("=", 1)[1].strip().strip('"')
            filename_star = _decode_filename_star(value)
        elif lower.startswith("filename="):
            filename = part.split("=", 1)[1].strip().strip('"')
    return filename_star or filename


def _resolve_download_name(url: str, content_disposition: str | None) -> str | None:
    filename = _extract_filename_from_disposition(content_disposition)
    if filename:
        return filename
    try:
        path = urllib.parse.urlparse(url).path
    except ValueError:
        return None
    base = os.path.basename(path)
    return base or None


def _read_response_body(
    resp: requests.Response,
    download_requested: bool,
    max_download_bytes: int | None,
    content_length: int | None,
) -> _ResponseBodyResult:
    total_bytes = 0
    if download_requested:
        download_chunks = []
        preview_chunks = []
        preview_read = 0
        for chunk in resp.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
            if not chunk:
                break
            next_total = total_bytes + len(chunk)
            if max_download_bytes and next_total > max_download_bytes:
                total_bytes = next_total
                return _ResponseBodyResult(
                    content_bytes=b"".join(download_chunks),
                    preview_bytes=b"".join(preview_chunks),
                    total_bytes=total_bytes,
                    truncated=total_bytes > PREVIEW_MAX_BYTES,
                    over_limit=True,
                )
            download_chunks.append(chunk)
            total_bytes = next_total
            if preview_read < PREVIEW_MAX_BYTES:
                take = min(len(chunk), PREVIEW_MAX_BYTES - preview_read)
                preview_chunks.append(chunk[:take])
                preview_read += take
        return _ResponseBodyResult(
            content_bytes=b"".join(download_chunks),
            preview_bytes=b"".join(preview_chunks),
            total_bytes=total_bytes,
            truncated=total_bytes > PREVIEW_MAX_BYTES,
            over_limit=False,
        )

    preview_chunks = []
    content_chunks = []
    bytes_read = 0
    preview_read = 0
    for chunk in resp.iter_content(chunk_size=1024):
        if not chunk:
            break
        if bytes_read >= RESPONSE_MAX_BYTES:
            break
        remaining = RESPONSE_MAX_BYTES - bytes_read
        if len(chunk) > remaining:
            chunk = chunk[:remaining]
        content_chunks.append(chunk)
        bytes_read += len(chunk)
        if preview_read < PREVIEW_MAX_BYTES:
            take = min(len(chunk), PREVIEW_MAX_BYTES - preview_read)
            preview_chunks.append(chunk[:take])
            preview_read += take
        if bytes_read >= RESPONSE_MAX_BYTES:
            break
    truncated = bytes_read >= RESPONSE_MAX_BYTES or (
        content_length is not None and content_length > bytes_read
    )
    return _ResponseBodyResult(
        content_bytes=b"".join(content_chunks),
        preview_bytes=b"".join(preview_chunks),
        total_bytes=bytes_read,
        truncated=truncated,
        over_limit=False,
    )


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
                "The URL, headers, and body can include secret placeholders using the unique pattern <<<my_api_key>>>. These placeholders will be replaced with the corresponding secret values at execution time. The response is truncated to 5MB. Text content is returned even if served with application/octet-stream; only truly binary data (images, etc.) is omitted. You may need to look up API docs using the mcp_brightdata_search_engine tool."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "method": {"type": "string", "description": "HTTP method e.g. GET, POST."},
                    "url": {"type": "string", "description": "Full URL to request."},
                    "headers": {"type": "object", "description": "Optional HTTP headers to include in the request."},
                    "body": {"type": "string", "description": "Optional request body (for POST/PUT)."},
                    "range": {"type": "string", "description": "Optional Range header value, e.g. 'bytes=0-1023'."},
                    "download": {
                        "type": "boolean",
                        "description": "Whether to save the response to the agent filespace (returns path/node_id/filename).",
                    },
                    "will_continue_work": {
                        "type": "boolean",
                        "description": "Set false when no immediate follow-up work is needed; enables auto-sleep.",
                    },
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

    1. Cap the response body to 5 MB (first bytes only).
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

    will_continue_work = _coerce_optional_bool(params.get("will_continue_work"))
    download_requested = _coerce_optional_bool(params.get("download")) is True
    if download_requested and not getattr(settings, "ALLOW_FILE_DOWNLOAD", False):
        return {"status": "error", "message": "File downloads are disabled."}

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

    if download_requested and (resp.status_code < 200 or resp.status_code >= 300):
        resp.close()
        return {
            "status": "error",
            "message": f"Download failed with status {resp.status_code}.",
            "status_code": resp.status_code,
        }

    content_length = resp.headers.get("Content-Length")
    try:
        content_length = int(content_length) if content_length else None
    except (TypeError, ValueError):
        content_length = None

    max_download_bytes = getattr(settings, "MAX_FILE_SIZE", None) if download_requested else None
    if download_requested and max_download_bytes and content_length and content_length > max_download_bytes:
        resp.close()
        return {
            "status": "error",
            "message": (
                f"File exceeds maximum allowed size ({content_length} bytes > "
                f"{max_download_bytes} bytes)."
            ),
        }

    try:
        body_result = _read_response_body(
            resp,
            download_requested=download_requested,
            max_download_bytes=max_download_bytes,
            content_length=content_length,
        )
    finally:
        resp.close()

    if download_requested and body_result.over_limit:
        return {
            "status": "error",
            "message": (
                f"File exceeds maximum allowed size ({body_result.total_bytes} bytes > "
                f"{max_download_bytes} bytes)."
            ),
        }

    content_bytes = body_result.content_bytes
    preview_bytes = body_result.preview_bytes
    total_bytes = body_result.total_bytes
    truncated = body_result.truncated

    # Determine if we should treat content as text
    content_type = (resp.headers.get("Content-Type") or "").lower()
    is_explicitly_textual = (
        content_type.startswith("text/")
        or "json" in content_type
        or "javascript" in content_type
        or "xml" in content_type
        or "csv" in content_type
    )

    decode_bytes = preview_bytes if download_requested else content_bytes

    # For explicitly textual types or unknown types, try to decode as UTF-8.
    # Many servers return application/octet-stream for plain text/CSV files.
    # We attempt decoding and only treat as binary if it fails badly.
    is_binary = False
    if is_explicitly_textual:
        # Trust the content type and decode with replacement for any bad chars
        try:
            content_str = decode_bytes.decode("utf-8", errors="replace")
        except Exception:
            content_str = decode_bytes.decode(errors="replace")
    else:
        # Unknown content type - try to decode and check if it looks like valid text
        try:
            # First try strict decoding to see if it's clean UTF-8
            content_str = decode_bytes.decode("utf-8")
        except UnicodeDecodeError:
            # Not clean UTF-8, try with replacement and check the error ratio
            content_str = decode_bytes.decode("utf-8", errors="replace")
            # Count replacement chars (U+FFFD) - if too many, probably binary
            replacement_count = content_str.count("\ufffd")
            # More than 5% replacement chars suggests actual binary data
            if len(content_str) > 0 and replacement_count / len(content_str) > 0.05:
                is_binary = True
        except Exception:
            # Fallback for any other errors
            content_str = decode_bytes.decode(errors="replace")

    if is_binary:
        size_hint = content_length if content_length is not None else total_bytes
        content_str = (
            f"[Binary content omitted – {content_type or 'unknown type'}, "
            f"length ≈ {size_hint} bytes]"
        )
    else:
        # Parse JSON content so agents can query it directly without double extraction.
        # Try parsing regardless of content-type since some APIs return JSON with wrong headers.
        # Quick check: only attempt parse if content looks like JSON (starts with { or [).
        # IMPORTANT: Parse BEFORE adding truncation message, otherwise json.loads fails.
        stripped = content_str.lstrip()
        if stripped and stripped[0] in "{[":
            try:
                content_str = json.loads(content_str)
            except Exception:
                pass  # Keep as string if parse fails
        # Add truncation notice for string content only (parsed JSON doesn't need it)
        if truncated and isinstance(content_str, str):
            content_str += "\n\n[Content truncated to 5MB]"

    # Log response details
    response_size = len(content_str) if isinstance(content_str, str) else len(str(content_str))
    logger.info(
        "Agent %s HTTP response: %s %s - Status: %d, Size: %d chars%s",
        agent.id, method, url, resp.status_code, response_size,
        " (truncated)" if truncated else ""
    )
    
    response = {
        "status": "ok",
        "status_code": resp.status_code,
        "headers": dict(resp.headers),
        "content": content_str,
        "proxy_used": str(proxy_server) if proxy_server else None,
    }
    if download_requested:
        content_type_header = resp.headers.get("Content-Type") or ""
        mime_type = content_type_header.split(";", 1)[0].strip().lower() or "application/octet-stream"
        download_name = _resolve_download_name(url, resp.headers.get("Content-Disposition"))
        extension = ""
        if download_name:
            _, ext = os.path.splitext(download_name)
            extension = (ext or "").lower()
        if not extension:
            extension = (mimetypes.guess_extension(mime_type) or "").lower()
        download_basename = os.path.basename(download_name or "download") or "download"
        download_path = f"/{DOWNLOADS_DIR_NAME}/{download_basename}"
        download_result = write_bytes_to_dir(
            agent=agent,
            content_bytes=content_bytes,
            path=download_path,
            extension=extension,
            mime_type=mime_type,
        )
        if download_result.get("status") != "ok":
            return download_result
        response.update(
            {
                "path": download_result["path"],
                "node_id": download_result["node_id"],
                "filename": download_result["filename"],
            }
        )
    if will_continue_work is False:
        response["auto_sleep_ok"] = True
    return response
