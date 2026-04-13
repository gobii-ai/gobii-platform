import datetime as dt
import logging
from ipaddress import ip_address
from typing import Any
from urllib.parse import quote

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from requests import RequestException

from api.models import (
    UserFingerprintVisit,
    UserFingerprintVisitFetchStatusChoices,
    UserIdentitySignalTypeChoices,
)


logger = logging.getLogger(__name__)


class FingerprintConfigurationError(RuntimeError):
    """Raised when the local Fingerprint server-side integration is unavailable."""


class FingerprintRetryableError(RuntimeError):
    """Raised when Fingerprint data may become available after a retry."""


class FingerprintTerminalError(RuntimeError):
    """Raised when a Fingerprint fetch failed in a non-retryable way."""


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def _clean_string(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if not normalized:
            return None
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
    return None


def _coerce_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _timestamp_ms_to_datetime(value: Any) -> dt.datetime | None:
    timestamp_ms = _coerce_float(value)
    if timestamp_ms is None:
        return None
    return dt.datetime.fromtimestamp(timestamp_ms / 1000.0, tz=dt.timezone.utc)


def _extract_ip_info_entry(payload: dict[str, Any]) -> dict[str, Any]:
    ip_info = _mapping(payload.get("ip_info"))
    if not ip_info:
        return {}

    ip_value = _clean_string(payload.get("ip_address"))
    if ip_value:
        try:
            version = ip_address(ip_value).version
        except ValueError:
            version = None
        if version is not None:
            candidate = _mapping(ip_info.get(f"v{version}"))
            if candidate:
                return candidate

    for key in ("v4", "v6"):
        candidate = _mapping(ip_info.get(key))
        if candidate:
            return candidate
    return {}


def _extract_signal_result(payload: dict[str, Any], key: str) -> bool | None:
    signal_payload = payload.get(key)
    if isinstance(signal_payload, dict):
        return _coerce_bool(signal_payload.get("result"))
    result = _coerce_bool(signal_payload)
    if result is not None:
        return result
    return _coerce_bool(payload.get(f"{key}_result"))


def _extract_signal_confidence(payload: dict[str, Any], key: str) -> str:
    signal_payload = payload.get(key)
    if isinstance(signal_payload, dict):
        confidence = _clean_string(signal_payload.get("confidence"))
        if confidence:
            return confidence
    return _clean_string(payload.get(f"{key}_confidence"))


def _extract_numeric_signal(value: Any) -> float | None:
    if isinstance(value, dict):
        for candidate_key in ("result", "score", "value"):
            candidate = _coerce_float(value.get(candidate_key))
            if candidate is not None:
                return candidate
        return None
    return _coerce_float(value)


def _extract_bot_value(value: Any) -> str:
    if isinstance(value, dict):
        return _clean_string(value.get("result") or value.get("type"))
    return _clean_string(value)


def _extract_proxy_type(payload: dict[str, Any]) -> str:
    proxy_details = _mapping(payload.get("proxy_details"))
    if proxy_details:
        return _clean_string(proxy_details.get("proxy_type"))

    proxy_payload = _mapping(payload.get("proxy"))
    nested_details = _mapping(proxy_payload.get("details"))
    if nested_details:
        return _clean_string(nested_details.get("proxy_type"))

    return _clean_string(payload.get("proxy_type"))


def is_fingerprint_server_api_configured() -> bool:
    return bool(settings.FINGERPRINT_SERVER_API_KEY.strip())


def _fingerprint_processing_stale_after() -> dt.timedelta:
    stale_after_seconds = max(int(settings.FINGERPRINT_SERVER_PROCESSING_STALE_SECONDS), 60)
    return dt.timedelta(seconds=stale_after_seconds)


def _is_processing_visit_stale(visit: UserFingerprintVisit) -> bool:
    if visit.fetch_status != UserFingerprintVisitFetchStatusChoices.PROCESSING:
        return False

    last_attempt_at = visit.last_fetch_attempt_at or visit.updated_at or visit.created_at
    if last_attempt_at is None:
        return True

    return last_attempt_at <= timezone.now() - _fingerprint_processing_stale_after()


def _enqueue_fingerprint_visit_refresh(visit_id: int) -> None:
    from api.tasks.fingerprint_tasks import fetch_user_fingerprint_visit_task

    transaction.on_commit(lambda: fetch_user_fingerprint_visit_task.delay(visit_id))


def stage_user_fingerprint_visit(
    user,
    *,
    source: str,
    signal_values: dict[str, str],
) -> UserFingerprintVisit | None:
    event_id = _clean_string(signal_values.get(UserIdentitySignalTypeChoices.FPJS_REQUEST_ID))
    visitor_id = _clean_string(signal_values.get(UserIdentitySignalTypeChoices.FPJS_VISITOR_ID))
    if not event_id:
        return None

    configured = is_fingerprint_server_api_configured()
    initial_status = (
        UserFingerprintVisitFetchStatusChoices.PENDING
        if configured
        else UserFingerprintVisitFetchStatusChoices.NOT_CONFIGURED
    )
    visit, created = UserFingerprintVisit.objects.get_or_create(
        user=user,
        fingerprint_event_id=event_id,
        defaults={
            "source": source,
            "fingerprint_visitor_id": visitor_id,
            "fetch_status": initial_status,
        },
    )

    updates: dict[str, Any] = {}
    should_enqueue = False

    if source and visit.source != source:
        updates["source"] = source
    if visitor_id and visit.fingerprint_visitor_id != visitor_id:
        updates["fingerprint_visitor_id"] = visitor_id

    if configured:
        if created:
            should_enqueue = True
        elif visit.fetch_status in {
            UserFingerprintVisitFetchStatusChoices.FAILED,
            UserFingerprintVisitFetchStatusChoices.NOT_CONFIGURED,
        } or _is_processing_visit_stale(visit):
            updates["fetch_status"] = UserFingerprintVisitFetchStatusChoices.PENDING
            updates["error_message"] = ""
            should_enqueue = True
    elif visit.fetch_status == UserFingerprintVisitFetchStatusChoices.PENDING and visit.fetch_attempt_count == 0:
        updates["fetch_status"] = UserFingerprintVisitFetchStatusChoices.NOT_CONFIGURED

    if updates:
        UserFingerprintVisit.objects.filter(pk=visit.pk).update(**updates)
        for field_name, value in updates.items():
            setattr(visit, field_name, value)

    if should_enqueue:
        _enqueue_fingerprint_visit_refresh(visit.id)

    return visit


def fetch_fingerprint_event_payload(event_id: str) -> dict[str, Any]:
    normalized_event_id = _clean_string(event_id)
    if not normalized_event_id:
        raise FingerprintTerminalError("Fingerprint event id is required.")
    if not is_fingerprint_server_api_configured():
        raise FingerprintConfigurationError("Fingerprint server API key is not configured.")

    url = f"{settings.FINGERPRINT_SERVER_API_URL.rstrip('/')}/v4/events/{quote(normalized_event_id, safe='')}"
    try:
        response = requests.get(
            url,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {settings.FINGERPRINT_SERVER_API_KEY}",
            },
            timeout=settings.FINGERPRINT_SERVER_API_TIMEOUT_SECONDS,
        )
    except RequestException as exc:
        raise FingerprintRetryableError("Failed to reach Fingerprint server API.") from exc

    if response.status_code in {404, 408, 429} or response.status_code >= 500:
        raise FingerprintRetryableError(
            f"Fingerprint server API returned retryable status {response.status_code}."
        )
    if response.status_code >= 400:
        response_preview = _clean_string(response.text)[:200]
        raise FingerprintTerminalError(
            f"Fingerprint server API returned status {response.status_code}: {response_preview}"
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise FingerprintRetryableError("Fingerprint server API returned invalid JSON.") from exc

    if not isinstance(payload, dict):
        raise FingerprintTerminalError("Fingerprint server API returned an unexpected payload shape.")
    return payload


def build_user_fingerprint_visit_updates(
    payload: dict[str, Any],
    *,
    fallback_event_id: str = "",
    fallback_visitor_id: str = "",
) -> dict[str, Any]:
    identification = _mapping(payload.get("identification"))
    confidence = _mapping(identification.get("confidence"))
    browser_details = _mapping(payload.get("browser_details"))
    ip_blocklist = _mapping(payload.get("ip_blocklist"))
    ip_info_entry = _extract_ip_info_entry(payload)
    geolocation = _mapping(ip_info_entry.get("geolocation"))

    subdivisions = geolocation.get("subdivisions")
    subdivision_name = ""
    if isinstance(subdivisions, list) and subdivisions:
        subdivision_name = _clean_string(_mapping(subdivisions[0]).get("name"))

    return {
        "fingerprint_event_id": _clean_string(payload.get("event_id")) or fallback_event_id,
        "fingerprint_visitor_id": _clean_string(identification.get("visitor_id")) or fallback_visitor_id,
        "event_timestamp": _timestamp_ms_to_datetime(payload.get("timestamp")),
        "visitor_first_seen_at": _timestamp_ms_to_datetime(identification.get("first_seen_at")),
        "replayed": _coerce_bool(payload.get("replayed")),
        "visitor_found": _coerce_bool(identification.get("visitor_found")),
        "visitor_confidence_score": _coerce_float(confidence.get("score")),
        "suspect_score": _extract_numeric_signal(payload.get("suspect_score")),
        "bot": _extract_bot_value(payload.get("bot")),
        "vpn": _extract_signal_result(payload, "vpn"),
        "vpn_confidence": _extract_signal_confidence(payload, "vpn"),
        "proxy": _extract_signal_result(payload, "proxy"),
        "proxy_confidence": _extract_signal_confidence(payload, "proxy"),
        "proxy_type": _extract_proxy_type(payload),
        "tor": _extract_signal_result(payload, "tor"),
        "tampering": _extract_signal_result(payload, "tampering"),
        "tampering_confidence": _extract_signal_confidence(payload, "tampering"),
        "tampering_ml_score": _coerce_float(payload.get("tampering_ml_score")),
        "high_activity_device": _extract_signal_result(payload, "high_activity_device"),
        "ip_blocklist_email_spam": _coerce_bool(ip_blocklist.get("email_spam")),
        "ip_blocklist_attack_source": _coerce_bool(ip_blocklist.get("attack_source")),
        "ip_blocklist_tor_node": _coerce_bool(ip_blocklist.get("tor_node")),
        "datacenter": _coerce_bool(ip_info_entry.get("datacenter_result")),
        "asn": _clean_string(ip_info_entry.get("asn")),
        "asn_name": _clean_string(ip_info_entry.get("asn_name")),
        "asn_type": _clean_string(ip_info_entry.get("asn_type")),
        "country_code": _clean_string(geolocation.get("country_code")),
        "country_name": _clean_string(geolocation.get("country_name")),
        "subdivision_name": subdivision_name,
        "city_name": _clean_string(geolocation.get("city_name")),
        "timezone": _clean_string(geolocation.get("timezone")),
        "ip_address": _clean_string(payload.get("ip_address")) or None,
        "browser_name": _clean_string(browser_details.get("browser_name")),
        "browser_major_version": _clean_string(browser_details.get("browser_major_version")),
        "browser_full_version": _clean_string(browser_details.get("browser_full_version")),
        "os": _clean_string(browser_details.get("os")),
        "os_version": _clean_string(browser_details.get("os_version")),
        "device": _clean_string(browser_details.get("device")),
        "sdk": _mapping(payload.get("sdk")),
        "velocity": _mapping(payload.get("velocity")),
        "vpn_methods": _mapping(payload.get("vpn_methods")),
        "tampering_details": _mapping(payload.get("tampering_details")),
        "raw_payload": payload,
    }


def refresh_user_fingerprint_visit(visit: UserFingerprintVisit) -> dict[str, Any]:
    payload = fetch_fingerprint_event_payload(visit.fingerprint_event_id)
    updates = build_user_fingerprint_visit_updates(
        payload,
        fallback_event_id=visit.fingerprint_event_id,
        fallback_visitor_id=visit.fingerprint_visitor_id,
    )
    updates["fetch_status"] = UserFingerprintVisitFetchStatusChoices.SUCCEEDED
    updates["fetched_at"] = timezone.now()
    updates["error_message"] = ""
    UserFingerprintVisit.objects.filter(pk=visit.pk).update(**updates)
    return updates
