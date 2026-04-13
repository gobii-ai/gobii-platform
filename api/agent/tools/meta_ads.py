"""Read-only Meta Ads system tool."""

import hashlib
import hmac
import json
import logging
from typing import Any, Optional

import requests
from django.contrib.sites.models import Site
from django.urls import reverse
from requests import Response
from requests.exceptions import RequestException

from api.agent.system_skills.registry import get_system_skill_definition
from api.models import PersistentAgent
from api.services.system_skill_profiles import resolve_system_skill_profile_for_agent


logger = logging.getLogger(__name__)

SYSTEM_SKILL_KEY = "meta_ads_platform"
GRAPH_BASE_URL = "https://graph.facebook.com"
REQUEST_TIMEOUT_SECONDS = 30
RATE_LIMIT_HEADER_NAMES = {
    "x-ad-account-usage",
    "x-app-usage",
    "x-business-use-case",
    "x-business-use-case-usage",
    "x-fb-ads-insights-throttle",
    "x-fb-trace-id",
}

DEFAULT_ACCOUNT_FIELDS = [
    "id",
    "account_id",
    "name",
    "account_status",
    "currency",
    "timezone_name",
]
DEFAULT_CAMPAIGN_FIELDS = [
    "id",
    "name",
    "status",
    "effective_status",
    "objective",
    "daily_budget",
    "lifetime_budget",
    "start_time",
    "stop_time",
]
DEFAULT_INSIGHTS_FIELDS = [
    "account_id",
    "account_name",
    "campaign_id",
    "campaign_name",
    "impressions",
    "reach",
    "spend",
    "clicks",
    "date_start",
    "date_stop",
]


def get_meta_ads_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "meta_ads",
            "description": (
                "Read Meta Ads account, campaign, and insights data for a configured Meta Ads profile. "
                "Use profile_key when you need a non-default credential profile."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": ["doctor", "accounts", "campaigns", "insights"],
                        "description": "Meta Ads action to perform.",
                    },
                    "profile_key": {
                        "type": "string",
                        "description": "Optional named Meta Ads profile to use.",
                    },
                    "account_id": {
                        "type": "string",
                        "description": "Optional ad account override, for example act_1234567890.",
                    },
                    "business_id": {
                        "type": "string",
                        "description": "Optional business ID override for listing owned ad accounts.",
                    },
                    "fields": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional Graph fields override.",
                    },
                    "page_size": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 500,
                        "description": "Page size for paged list requests.",
                    },
                    "fetch_all": {
                        "type": "boolean",
                        "description": "When true, keep following Graph pagination cursors.",
                    },
                    "level": {
                        "type": "string",
                        "enum": ["account", "campaign", "adset", "ad"],
                        "description": "Insights aggregation level.",
                    },
                    "date_preset": {
                        "type": "string",
                        "description": "Meta date preset for insights when since/until are omitted.",
                    },
                    "since": {
                        "type": "string",
                        "description": "Insights start date in YYYY-MM-DD format.",
                    },
                    "until": {
                        "type": "string",
                        "description": "Insights end date in YYYY-MM-DD format.",
                    },
                    "breakdowns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional insights breakdowns.",
                    },
                },
                "required": ["operation"],
            },
        },
    }


def _normalize_api_version(value: Optional[str]) -> str:
    version = str(value or "v25.0").strip()
    if not version.startswith("v"):
        version = f"v{version}"
    return version


def _normalize_account_id(account_id: str) -> str:
    value = str(account_id or "").strip()
    if not value:
        return value
    return value if value.startswith("act_") else f"act_{value}"


def _string_list(value: Any, *, default: list[str]) -> list[str]:
    if value is None:
        return list(default)
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return list(default)


def _extract_rate_limit_headers(response: Response) -> dict[str, str]:
    return {
        key.lower(): value
        for key, value in response.headers.items()
        if key.lower() in RATE_LIMIT_HEADER_NAMES
    }


def _build_setup_url(skill_key: str) -> str:
    relative_url = reverse("console-system-skill-profiles", args=[skill_key])
    try:
        current_site = Site.objects.get_current()
    except Exception:
        return relative_url
    return f"https://{current_site.domain}{relative_url}"


def _profile_action_required(
    resolution: dict[str, object],
    *,
    profile_key: Optional[str] = None,
) -> dict[str, Any]:
    definition = get_system_skill_definition(SYSTEM_SKILL_KEY)
    required_fields = [field.key for field in definition.required_profile_fields] if definition else []
    setup_url = _build_setup_url(SYSTEM_SKILL_KEY)
    available_profiles = list(resolution.get("available_profile_keys") or [])

    status = resolution.get("status")
    if status == "profile_not_found":
        result = (
            f"Meta Ads profile '{profile_key}' was not found. "
            f"Available profiles: {', '.join(available_profiles) if available_profiles else 'none'}. "
            f"Manage profiles here: {setup_url}"
        )
    elif status == "multiple_profiles":
        result = (
            "Multiple Meta Ads profiles are configured and no default profile is set. "
            f"Choose one of: {', '.join(available_profiles)}. "
            f"You can set a default profile here: {setup_url}"
        )
    elif status == "incomplete_profile":
        selected_profile = resolution.get("profile")
        selected_profile_key = getattr(selected_profile, "profile_key", profile_key or "default")
        missing_required_keys = list(resolution.get("missing_required_keys") or [])
        result = (
            f"Meta Ads profile '{selected_profile_key}' is missing required values: {', '.join(missing_required_keys)}. "
            f"Complete the profile here: {setup_url}"
        )
    else:
        result = (
            "Meta Ads setup is required before this tool can run. "
            f"Add a Meta Ads profile here: {setup_url}"
        )

    instructions = definition.setup_instructions if definition else ""
    return {
        "status": "action_required",
        "result": result,
        "setup_url": setup_url,
        "skill_key": SYSTEM_SKILL_KEY,
        "required_fields": required_fields,
        "available_profiles": available_profiles,
        "setup_instructions": instructions,
    }


def _graph_error_message(response: Response, payload: Any) -> str:
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            pieces = []
            if error.get("message"):
                pieces.append(str(error["message"]))
            if error.get("code") is not None:
                pieces.append(f"code={error['code']}")
            if error.get("error_subcode") is not None:
                pieces.append(f"subcode={error['error_subcode']}")
            if pieces:
                return "Meta Graph API error: " + " | ".join(pieces)
    return f"Meta Graph API request failed with HTTP {response.status_code}."


def _graph_get(profile_values: dict[str, str], path: str, *, params: Optional[dict[str, Any]] = None) -> tuple[Any, dict[str, str]]:
    api_version = _normalize_api_version(profile_values.get("META_API_VERSION"))
    access_token = profile_values["META_SYSTEM_USER_TOKEN"]
    app_secret = profile_values["META_APP_SECRET"]

    request_params = dict(params or {})
    request_params["access_token"] = access_token
    request_params["appsecret_proof"] = hmac.new(
        app_secret.encode("utf-8"),
        access_token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    url = f"{GRAPH_BASE_URL}/{api_version}/{path.lstrip('/')}"
    response = requests.get(url, params=request_params, timeout=REQUEST_TIMEOUT_SECONDS)
    rate_limit_headers = _extract_rate_limit_headers(response)

    try:
        payload = response.json()
    except ValueError:
        payload = {"raw_body": response.text[:1000]}

    if response.status_code >= 400:
        raise ValueError(_graph_error_message(response, payload))

    return payload, rate_limit_headers


def _paginate_graph_get(
    profile_values: dict[str, str],
    path: str,
    *,
    params: Optional[dict[str, Any]] = None,
    fetch_all: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    page_params = dict(params or {})
    rows: list[dict[str, Any]] = []
    last_headers: dict[str, str] = {}
    after_cursor: Optional[str] = None

    while True:
        current_params = dict(page_params)
        if after_cursor:
            current_params["after"] = after_cursor
        payload, last_headers = _graph_get(profile_values, path, params=current_params)
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise ValueError(f"Expected a paged Graph API response, got: {json.dumps(payload)[:500]}")

        rows.extend(payload["data"])
        if not fetch_all:
            break

        paging = payload.get("paging")
        cursors = paging.get("cursors") if isinstance(paging, dict) else None
        after_cursor = cursors.get("after") if isinstance(cursors, dict) else None
        if not after_cursor:
            break

    return rows, last_headers


def _doctor_result(profile_key: str, profile_values: dict[str, str]) -> dict[str, Any]:
    api_version = _normalize_api_version(profile_values.get("META_API_VERSION"))
    default_account_id = _normalize_account_id(profile_values.get("META_AD_ACCOUNT_ID") or "")
    business_id = str(profile_values.get("META_BUSINESS_ID") or "").strip() or None
    result = (
        f"Meta Ads profile '{profile_key}' is configured. "
        f"Default account: {default_account_id or 'not set'}. "
        f"API version: {api_version}."
    )
    return {
        "status": "success",
        "result": result,
        "profile_key": profile_key,
        "api_version": api_version,
        "default_account_id": default_account_id or None,
        "business_id": business_id,
    }


def _require_account_id(account_id: Optional[str], profile_values: dict[str, str]) -> str:
    resolved_account_id = _normalize_account_id(account_id or profile_values.get("META_AD_ACCOUNT_ID") or "")
    if not resolved_account_id:
        raise ValueError("Missing Meta ad account ID. Provide account_id or configure META_AD_ACCOUNT_ID on the profile.")
    return resolved_account_id


def execute_meta_ads(agent: PersistentAgent, params: dict[str, Any]) -> dict[str, Any]:
    operation = str(params.get("operation") or "").strip().lower()
    if operation not in {"doctor", "accounts", "campaigns", "insights"}:
        return {"status": "error", "message": "operation must be one of: doctor, accounts, campaigns, insights"}

    profile_key = str(params.get("profile_key") or "").strip() or None
    resolution = resolve_system_skill_profile_for_agent(agent, SYSTEM_SKILL_KEY, profile_key=profile_key)
    if resolution.get("status") != "ok":
        return _profile_action_required(resolution, profile_key=profile_key)

    profile = resolution["profile"]
    profile_values = resolution["values"]
    selected_profile_key = profile.profile_key

    try:
        if operation == "doctor":
            return _doctor_result(selected_profile_key, profile_values)

        page_size = int(params.get("page_size") or 100)
        page_size = max(1, min(page_size, 500))
        fetch_all = bool(params.get("fetch_all"))

        if operation == "accounts":
            business_id = str(params.get("business_id") or profile_values.get("META_BUSINESS_ID") or "").strip()
            path = f"{business_id}/owned_ad_accounts" if business_id else "me/adaccounts"
            rows, headers = _paginate_graph_get(
                profile_values,
                path,
                params={
                    "fields": ",".join(_string_list(params.get("fields"), default=DEFAULT_ACCOUNT_FIELDS)),
                    "limit": page_size,
                },
                fetch_all=fetch_all,
            )
            return {
                "status": "success",
                "result": f"Fetched {len(rows)} Meta ad account row(s) for profile '{selected_profile_key}'.",
                "operation": operation,
                "profile_key": selected_profile_key,
                "rows": rows,
                "rate_limit_headers": headers,
            }

        account_id = _require_account_id(params.get("account_id"), profile_values)
        if operation == "campaigns":
            rows, headers = _paginate_graph_get(
                profile_values,
                f"{account_id}/campaigns",
                params={
                    "fields": ",".join(_string_list(params.get("fields"), default=DEFAULT_CAMPAIGN_FIELDS)),
                    "limit": page_size,
                },
                fetch_all=fetch_all,
            )
            return {
                "status": "success",
                "result": f"Fetched {len(rows)} Meta campaign row(s) for account '{account_id}'.",
                "operation": operation,
                "profile_key": selected_profile_key,
                "account_id": account_id,
                "rows": rows,
                "rate_limit_headers": headers,
            }

        insights_params: dict[str, Any] = {
            "fields": ",".join(_string_list(params.get("fields"), default=DEFAULT_INSIGHTS_FIELDS)),
            "level": str(params.get("level") or "account"),
            "limit": page_size,
        }
        since = str(params.get("since") or "").strip()
        until = str(params.get("until") or "").strip()
        if since and until:
            insights_params["time_range"] = json.dumps({"since": since, "until": until})
        else:
            insights_params["date_preset"] = str(params.get("date_preset") or "last_7d")

        breakdowns = _string_list(params.get("breakdowns"), default=[])
        if breakdowns:
            insights_params["breakdowns"] = ",".join(breakdowns)

        rows, headers = _paginate_graph_get(
            profile_values,
            f"{account_id}/insights",
            params=insights_params,
            fetch_all=fetch_all,
        )
        return {
            "status": "success",
            "result": f"Fetched {len(rows)} Meta insights row(s) for account '{account_id}'.",
            "operation": operation,
            "profile_key": selected_profile_key,
            "account_id": account_id,
            "rows": rows,
            "rate_limit_headers": headers,
        }
    except (RequestException, ValueError) as exc:
        logger.warning("Meta Ads tool failed for agent %s profile %s: %s", agent.id, selected_profile_key, exc)
        return {"status": "error", "message": str(exc)}
