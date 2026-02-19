from typing import Any, Optional
from urllib.parse import quote_plus, urlparse

from django.conf import settings
from django.http import HttpRequest
from django.urls import reverse


def _public_site_base_url() -> str:
    raw = (settings.PUBLIC_SITE_URL or "").strip()
    if not raw:
        return "http://localhost:8000"
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    if parsed.path:
        return parsed.path.rstrip("/")
    return "http://localhost:8000"


def bridge_shared_secret() -> str:
    return str(getattr(settings, "MCP_REMOTE_BRIDGE_SHARED_SECRET", "") or "").strip()


def _append_query_param(url: str, key: str, value: str) -> str:
    separator = "&" if "?" in url else "?"
    encoded_key = quote_plus(key)
    encoded_value = quote_plus(value)
    return f"{url}{separator}{encoded_key}={encoded_value}"


def build_mcp_remote_bridge_payload(*, auth_session_id: Optional[str] = None) -> dict[str, Any]:
    if not settings.MCP_REMOTE_BRIDGE_ENABLED:
        return {}

    base_url = _public_site_base_url()
    redirect_url = f"{base_url}{reverse('api-mcp-bridge-callback')}"
    poll_url = f"{base_url}{reverse('api-mcp-bridge-poll')}?session_id={{session_id}}"
    notify_url = f"{base_url}{reverse('api-mcp-bridge-notify')}"

    shared_secret = bridge_shared_secret()
    if shared_secret:
        redirect_url = _append_query_param(redirect_url, "bridge_token", shared_secret)
        poll_url = _append_query_param(poll_url, "bridge_token", shared_secret)
        notify_url = _append_query_param(notify_url, "bridge_token", shared_secret)

    payload: dict[str, Any] = {
        "auth_mode": "bridge",
        "redirect_url": redirect_url,
        "poll_url": poll_url,
        "notify_url": notify_url,
        "poll_interval_seconds": settings.MCP_REMOTE_BRIDGE_POLL_INTERVAL_SECONDS,
        "auth_timeout_seconds": settings.MCP_REMOTE_BRIDGE_AUTH_TIMEOUT_SECONDS,
    }
    if auth_session_id and auth_session_id.strip():
        payload["auth_session_id"] = auth_session_id.strip()
    return payload


def validate_mcp_remote_bridge_request(request: HttpRequest) -> bool:
    expected = bridge_shared_secret()
    if not expected:
        return True

    token_candidates = [
        str(request.GET.get("bridge_token") or "").strip(),
        str(request.POST.get("bridge_token") or "").strip(),
        str(request.headers.get("X-MCP-Bridge-Token") or "").strip(),
        str(request.headers.get("X-Gobii-MCP-Bridge-Token") or "").strip(),
    ]
    return any(candidate and candidate == expected for candidate in token_candidates)
