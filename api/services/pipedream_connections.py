"""Pipedream Connect account helpers scoped to persistent agents."""

import logging
from dataclasses import dataclass
from typing import Iterable

import requests
from django.conf import settings

from api.agent.tools.mcp_manager import get_mcp_manager
from api.models import PersistentAgent
from api.pipedream_app_utils import normalize_app_slug

logger = logging.getLogger(__name__)

PIPEDREAM_CONNECT_API_BASE = "https://api.pipedream.com/v1"


class PipedreamConnectionError(RuntimeError):
    """Raised when Pipedream Connect account operations fail."""


@dataclass(frozen=True)
class PipedreamConnectedAccount:
    id: str
    app_slug: str
    external_user_id: str = ""


def _pipedream_headers() -> dict[str, str]:
    token = get_mcp_manager()._get_pipedream_access_token() or ""
    if not token:
        raise PipedreamConnectionError(
            "Pipedream access token unavailable; set PIPEDREAM_CLIENT_ID/PIPEDREAM_CLIENT_SECRET and try again."
        )
    return {
        "Authorization": f"Bearer {token}",
        "x-pd-environment": settings.PIPEDREAM_ENVIRONMENT,
    }


def _raise_for_status(response: requests.Response, *, action: str) -> None:
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        response_text = (response.text or "")[:1000]
        message = f"Pipedream {action} failed with HTTP {response.status_code}."
        if response_text:
            message = f"{message} Response: {response_text}"
        raise PipedreamConnectionError(message) from exc


def _account_app_slug(account: dict) -> str:
    app = account.get("app")
    if isinstance(app, dict):
        for key in ("name_slug", "slug"):
            slug = normalize_app_slug(app.get(key))
            if slug:
                return slug
    return normalize_app_slug(account.get("app_slug") or account.get("app"))


def _account_external_user_id(account: dict) -> str:
    for key in ("external_user_id", "externalUserId", "user_id", "userId"):
        value = str(account.get(key) or "").strip()
        if value:
            return value

    for nested_key in ("user", "external_user", "externalUser"):
        nested = account.get(nested_key)
        if not isinstance(nested, dict):
            continue
        for key in ("external_user_id", "externalUserId", "id", "user_id", "userId"):
            value = str(nested.get(key) or "").strip()
            if value:
                return value

    return ""


def _is_active_account(account: dict) -> bool:
    return account.get("dead") is not True and account.get("healthy") is not False


def _list_pipedream_connected_accounts(
    *,
    params: dict[str, str],
    normalized_app: str = "",
    page_size: int = 100,
    max_pages: int = 20,
) -> list[PipedreamConnectedAccount]:
    headers = _pipedream_headers()
    accounts: list[PipedreamConnectedAccount] = []
    cursor = ""
    pages = 0
    while pages < max_pages:
        pages += 1
        request_params = dict(params)
        request_params["limit"] = str(page_size)
        if cursor:
            request_params["after"] = cursor

        try:
            response = requests.get(
                f"{PIPEDREAM_CONNECT_API_BASE}/connect/{settings.PIPEDREAM_PROJECT_ID}/accounts",
                params=request_params,
                headers=headers,
                timeout=20,
            )
        except requests.RequestException as exc:
            raise PipedreamConnectionError("Failed to query Pipedream connected accounts.") from exc
        _raise_for_status(response, action="account lookup")
        try:
            payload = response.json() or {}
        except ValueError as exc:
            raise PipedreamConnectionError("Pipedream account lookup returned invalid JSON.") from exc
        items = payload.get("data") or []
        if not isinstance(items, list):
            raise PipedreamConnectionError("Pipedream account lookup returned an invalid response.")

        for item in items:
            if not isinstance(item, dict) or not _is_active_account(item):
                continue
            item_app_slug = _account_app_slug(item)
            if normalized_app and item_app_slug != normalized_app:
                continue
            account_id = str(item.get("id") or "").strip()
            if account_id and item_app_slug:
                accounts.append(
                    PipedreamConnectedAccount(
                        id=account_id,
                        app_slug=item_app_slug,
                        external_user_id=_account_external_user_id(item),
                    )
                )

        page_info = payload.get("page_info") or {}
        next_cursor = page_info.get("end_cursor") if isinstance(page_info, dict) else None
        if not items or not next_cursor or next_cursor == cursor:
            break
        cursor = str(next_cursor)

    return accounts


def list_pipedream_connected_accounts(
    agent: PersistentAgent,
    *,
    app_slug: str | None = None,
    page_size: int = 100,
    max_pages: int = 20,
) -> list[PipedreamConnectedAccount]:
    """Return active Pipedream Connect accounts for the agent, optionally narrowed to one app."""
    normalized_app = normalize_app_slug(app_slug)
    params: dict[str, str] = {
        "external_user_id": str(agent.id),
    }
    if normalized_app:
        params["app"] = normalized_app

    return _list_pipedream_connected_accounts(
        params=params,
        normalized_app=normalized_app,
        page_size=page_size,
        max_pages=max_pages,
    )


def list_pipedream_connected_accounts_for_app(
    app_slug: str,
    *,
    page_size: int = 100,
    max_pages: int = 20,
) -> list[PipedreamConnectedAccount]:
    """Return active Pipedream Connect accounts for one app across external users."""
    normalized_app = normalize_app_slug(app_slug)
    if not normalized_app:
        return []

    return _list_pipedream_connected_accounts(
        params={"app": normalized_app},
        normalized_app=normalized_app,
        page_size=page_size,
        max_pages=max_pages,
    )


def group_pipedream_connected_accounts_by_app(
    accounts: Iterable[PipedreamConnectedAccount],
) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for account in accounts:
        grouped.setdefault(account.app_slug, []).append(account.id)
    return grouped


def delete_pipedream_connected_accounts(account_ids: Iterable[str]) -> int:
    """Delete Pipedream Connect accounts by id. Missing accounts are treated as already deleted."""
    headers = _pipedream_headers()
    deleted_count = 0
    for raw_account_id in account_ids:
        account_id = str(raw_account_id or "").strip()
        if not account_id:
            continue
        try:
            response = requests.delete(
                f"{PIPEDREAM_CONNECT_API_BASE}/connect/{settings.PIPEDREAM_PROJECT_ID}/accounts/{account_id}",
                headers=headers,
                timeout=20,
            )
        except requests.RequestException as exc:
            raise PipedreamConnectionError("Failed to delete Pipedream connected account.") from exc
        if response.status_code not in (204, 404):
            _raise_for_status(response, action="account deletion")
        deleted_count += 1
    return deleted_count
