import logging
import time
import random
from typing import Dict, Generator, List, Optional, Tuple

import requests
from django.conf import settings

from api.agent.tools.mcp_manager import get_mcp_manager


logger = logging.getLogger(__name__)


PD_BASE = "https://api.pipedream.com"


def _get_pd_token() -> Optional[str]:
    try:
        mgr = get_mcp_manager()
        return mgr._get_pipedream_access_token() or None
    except Exception as e:
        logger.error("Pipedream GC: failed to obtain access token: %s", e)
        return None


def _pd_headers() -> Optional[Dict[str, str]]:
    token = _get_pd_token()
    if not token:
        return None
    return {
        "Authorization": f"Bearer {token}",
        "x-pd-environment": getattr(settings, "PIPEDREAM_ENVIRONMENT", "development"),
    }


def _request_with_backoff(method: str, url: str, *, headers: Dict[str, str], params: Dict[str, str] | None = None,
                          timeout: int = 20, max_retries: int = 3) -> requests.Response:
    """Perform a requests call with simple backoff on 429/5xx."""
    attempt = 0
    while True:
        attempt += 1
        try:
            resp = requests.request(method, url, headers=headers, params=params, timeout=timeout)
            # Backoff on 429/5xx
            if resp.status_code in (429, 500, 502, 503, 504) and attempt <= max_retries:
                sleep_s = min(8.0, 0.5 * attempt + random.random())
                time.sleep(sleep_s)
                continue
            return resp
        except requests.RequestException:
            if attempt > max_retries:
                raise
            sleep_s = min(8.0, 0.5 * attempt + random.random())
            time.sleep(sleep_s)


def iter_accounts(page_size: int = 200, max_pages: int = 1000) -> Generator[Dict, None, None]:
    """Yield account records from Pipedream Connect across all pages.

    Tries best-effort pagination using 'after' and 'end_cursor'.
    """
    headers = _pd_headers()
    if not headers:
        logger.warning("Pipedream GC: missing headers/token; cannot iterate accounts")
        return

    project_id = getattr(settings, "PIPEDREAM_PROJECT_ID", "")
    if not project_id:
        logger.warning("Pipedream GC: PIPEDREAM_PROJECT_ID not set; cannot iterate accounts")
        return

    cursor: Optional[str] = None
    pages = 0
    while pages < max_pages:
        pages += 1
        params = {"limit": str(page_size)}
        if cursor:
            params["after"] = cursor
        url = f"{PD_BASE}/v1/connect/{project_id}/accounts"
        resp = _request_with_backoff("GET", url, headers=headers, params=params)
        try:
            resp.raise_for_status()
        except Exception as e:
            logger.error("Pipedream GC: list accounts failed (page=%s): %s", pages, e)
            return

        data = resp.json() or {}
        items: List[Dict] = (data.get("data") or [])
        for item in items:
            yield item

        page_info = data.get("page_info") or {}
        next_cursor = page_info.get("end_cursor")
        if not items or not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor


def delete_external_user(external_user_id: str) -> Tuple[bool, int, str]:
    """Delete an external user; returns (ok, status_code, message).

    Treats 204 and 404 as success (idempotent behavior).
    """
    headers = _pd_headers()
    if not headers:
        return False, 0, "missing token"

    project_id = getattr(settings, "PIPEDREAM_PROJECT_ID", "")
    if not project_id:
        return False, 0, "missing project id"

    url = f"{PD_BASE}/v1/connect/{project_id}/users/{external_user_id}"
    resp = _request_with_backoff("DELETE", url, headers=headers, params=None)
    status = resp.status_code
    if status in (204, 404):
        return True, status, "ok"
    try:
        msg = resp.text
    except Exception:
        msg = ""
    return False, status, msg


def delete_account(account_id: str) -> Tuple[bool, int, str]:
    """Delete a single account; returns (ok, status_code, message).

    Treats 204 and 404 as success.
    """
    headers = _pd_headers()
    if not headers:
        return False, 0, "missing token"

    project_id = getattr(settings, "PIPEDREAM_PROJECT_ID", "")
    if not project_id:
        return False, 0, "missing project id"

    url = f"{PD_BASE}/v1/connect/{project_id}/accounts/{account_id}"
    resp = _request_with_backoff("DELETE", url, headers=headers, params=None)
    status = resp.status_code
    if status in (204, 404):
        return True, status, "ok"
    try:
        msg = resp.text
    except Exception:
        msg = ""
    return False, status, msg


def extract_external_user_id(account: Dict) -> Optional[str]:
    """Best-effort extraction of external_user_id from an account record.

    The official schema may not expose this directly; try common keys.
    """
    if not isinstance(account, dict):
        return None
    # Common patterns
    for key in ("external_user_id", "externalUserId", "user_id", "userId"):
        val = account.get(key)
        if isinstance(val, str) and val:
            return val
    # Sometimes present under a metadata/credentials blob
    creds = account.get("credentials") or {}
    if isinstance(creds, dict):
        for key in ("external_user_id", "externalUserId", "user_id", "userId"):
            val = creds.get(key)
            if isinstance(val, str) and val:
                return val
    return None


def extract_account_id(account: Dict) -> Optional[str]:
    if not isinstance(account, dict):
        return None
    val = account.get("id")
    return str(val) if val else None

