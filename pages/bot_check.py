import json
import secrets
from ipaddress import ip_address
from urllib.parse import quote_plus, urlsplit, urlunsplit

from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.core.exceptions import RequestDataTooBig
from django.http import JsonResponse
from django.shortcuts import render
from django.utils.crypto import salted_hmac
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from api.services.user_fingerprint import (
    FingerprintConfigurationError,
    FingerprintRetryableError,
    FingerprintTerminalError,
    fetch_fingerprint_event_payload,
)
from util.analytics import Analytics


BOT_CHECK_SIGNING_SALT = "pages.bot-check.scan"
BOT_CHECK_RATE_CACHE_PREFIX = "bot-check:rate"
BOT_CHECK_POLL_CACHE_PREFIX = "bot-check:poll"
BOT_CHECK_MAX_BODY_BYTES = 32 * 1024
BOT_CHECK_MAX_TOKEN_LENGTH = 4096
BOT_CHECK_MAX_EVENT_ID_LENGTH = 255
BOT_CHECK_SCAN_RATE_LIMIT_PER_HOUR = 10
BOT_CHECK_SCAN_TOKEN_MAX_AGE_SECONDS = 120
BOT_CHECK_FINGERPRINT_MAX_POLLS = 4
BOT_CHECK_FINGERPRINT_RETRY_AFTER_MS = 2000

CLIENT_BOOLEAN_FIELDS = {
    "webdriver",
    "headless_user_agent",
    "devtools_agent",
    "cdp_detected",
    "ua_ch_mismatch",
    "software_renderer",
    "cookies_enabled",
    "local_storage",
    "session_storage",
    "indexed_db",
}
CLIENT_NUMBER_FIELDS = {
    "hardware_concurrency",
    "device_memory",
    "max_touch_points",
    "screen_width",
    "screen_height",
    "color_depth",
    "plugin_count",
    "mime_type_count",
}
CLIENT_STRING_LIMITS = {
    "user_agent": 2048,
    "platform": 128,
    "timezone": 128,
    "webgl_vendor": 256,
    "webgl_renderer": 512,
}


def _json_response(payload, *, status=200):
    response = JsonResponse(payload, status=status)
    response["Cache-Control"] = "no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


def _clean_string(value, *, max_length=512):
    if not isinstance(value, str):
        return ""
    return value.strip()[:max_length]


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _optional_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, dict):
        return _optional_bool(value.get("result"))
    return None


def _optional_number(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, dict):
        for key in ("result", "score", "value"):
            result = _optional_number(value.get(key))
            if result is not None:
                return result
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _signal_text(value):
    if isinstance(value, dict):
        value = value.get("result") or value.get("type")
    return _clean_string(value, max_length=128)


def _safe_scalar_mapping(value, *, max_items=16):
    result = {}
    for raw_key, raw_value in list(_mapping(value).items())[:max_items]:
        key = _clean_string(raw_key, max_length=64)
        if not key:
            continue
        if isinstance(raw_value, bool):
            result[key] = raw_value
        elif isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool):
            result[key] = raw_value
        elif isinstance(raw_value, str):
            result[key] = _clean_string(raw_value, max_length=128)
    return result


def _client_ip(request):
    value = Analytics.get_client_ip(request)
    return "" if not value or value == "0" else str(value).strip()


def _bound_hash(value, *, purpose):
    return salted_hmac(f"bot-check:{purpose}", value or "", secret=settings.SECRET_KEY).hexdigest()


def _rate_limit_key(request):
    digest = _bound_hash(_client_ip(request), purpose="rate")
    return f"{BOT_CHECK_RATE_CACHE_PREFIX}:{digest}"


def _admit_scan(request):
    key = _rate_limit_key(request)
    timeout = 60 * 60
    if cache.add(key, 1, timeout=timeout):
        return True
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=timeout)
        count = 1
    return count <= BOT_CHECK_SCAN_RATE_LIMIT_PER_HOUR


def _scan_identity(request):
    return {
        "ip_hash": _bound_hash(_client_ip(request), purpose="ip"),
        "user_agent_hash": _bound_hash(
            request.META.get("HTTP_USER_AGENT", ""),
            purpose="user-agent",
        ),
    }


def _make_scan_token(request):
    return signing.dumps(
        {
            **_scan_identity(request),
            "scan_id": secrets.token_urlsafe(12),
        },
        salt=BOT_CHECK_SIGNING_SALT,
        compress=True,
    )


def _read_scan_token(request, token):
    try:
        payload = signing.loads(
            token,
            salt=BOT_CHECK_SIGNING_SALT,
            max_age=BOT_CHECK_SCAN_TOKEN_MAX_AGE_SECONDS,
        )
    except signing.SignatureExpired:
        return None, "The scan expired. Reload the page to start a new scan."
    except signing.BadSignature:
        return None, "The scan token is invalid."

    if not isinstance(payload, dict):
        return None, "The scan token is invalid."
    expected = _scan_identity(request)
    if (
        payload.get("ip_hash") != expected["ip_hash"]
        or payload.get("user_agent_hash") != expected["user_agent_hash"]
    ):
        return None, "The scan token does not match this browser."
    scan_id = _clean_string(payload.get("scan_id"), max_length=128)
    if not scan_id:
        return None, "The scan token is invalid."
    return payload, ""


def _fingerprint_browser_config():
    configured_loader_url = settings.FINGERPRINT_JS_URL.strip()
    parsed_loader_url = urlsplit(configured_loader_url)
    path_parts = [part for part in parsed_loader_url.path.split("/") if part]
    cdn_loader_has_embedded_key = bool(
        parsed_loader_url.hostname == "fpjscdn.net"
        and len(path_parts) == 2
        and path_parts[0] in {"v3", "v4"}
        and path_parts[1]
    )
    browser_enabled = bool(
        settings.GOBII_PROPRIETARY_MODE
        and settings.FINGERPRINT_JS_ENABLED
        and configured_loader_url
        and (cdn_loader_has_embedded_key or settings.FINGERPRINT_JS_API_KEY.strip())
    )
    server_intelligence_enabled = bool(
        browser_enabled and settings.FINGERPRINT_SERVER_API_KEY.strip()
    )
    if not browser_enabled:
        return {
            "enabled": False,
            "server_intelligence_enabled": False,
        }

    if cdn_loader_has_embedded_key:
        loader_url = urlunsplit(
            (
                parsed_loader_url.scheme,
                parsed_loader_url.netloc,
                parsed_loader_url.path,
                "",
                "",
            )
        )
    else:
        loader_url = configured_loader_url
        browser_key = settings.FINGERPRINT_JS_API_KEY.strip()
        separator = "&" if "?" in loader_url else "?"
        loader_url = f"{loader_url}{separator}apiKey={quote_plus(browser_key)}"
    return {
        "enabled": True,
        "server_intelligence_enabled": server_intelligence_enabled,
        "loader_url": loader_url,
        "behavior_url": settings.FINGERPRINT_JS_BEHAVIOR_URL.strip(),
    }


def _read_json_body(request):
    try:
        body = request.body
    except RequestDataTooBig:
        return None, "The request is too large."
    if len(body) > BOT_CHECK_MAX_BODY_BYTES:
        return None, "The request is too large."
    try:
        payload = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, "A valid JSON object is required."
    if not isinstance(payload, dict):
        return None, "A valid JSON object is required."
    return payload, ""


def normalize_client_signals(value):
    source = _mapping(value)
    normalized = {}

    for field in CLIENT_BOOLEAN_FIELDS:
        field_value = source.get(field)
        if isinstance(field_value, bool):
            normalized[field] = field_value
        elif field_value is None:
            normalized[field] = None

    for field in CLIENT_NUMBER_FIELDS:
        field_value = source.get(field)
        if isinstance(field_value, (int, float)) and not isinstance(field_value, bool):
            normalized[field] = max(-1, min(float(field_value), 100000))
        elif field_value is None:
            normalized[field] = None

    for field, max_length in CLIENT_STRING_LIMITS.items():
        normalized[field] = _clean_string(source.get(field), max_length=max_length)

    languages = source.get("languages")
    normalized["languages"] = (
        [_clean_string(item, max_length=64) for item in languages[:10] if isinstance(item, str)]
        if isinstance(languages, list)
        else []
    )
    automation_globals = source.get("automation_globals")
    normalized["automation_globals"] = (
        [_clean_string(item, max_length=64) for item in automation_globals[:12] if isinstance(item, str)]
        if isinstance(automation_globals, list)
        else []
    )
    return normalized


def _ip_info(payload):
    info = _mapping(payload.get("ip_info"))
    requested_ip = _clean_string(payload.get("ip_address"), max_length=64)
    if requested_ip:
        try:
            version = ip_address(requested_ip).version
        except ValueError:
            version = None
        if version:
            candidate = _mapping(info.get(f"v{version}"))
            if candidate:
                return candidate
    return _mapping(info.get("v4")) or _mapping(info.get("v6"))


def normalize_fingerprint_signals(payload):
    source = _mapping(payload)
    identification = _mapping(source.get("identification"))
    confidence = _mapping(identification.get("confidence"))
    bot_info = _mapping(source.get("bot_info"))
    tampering_payload = _mapping(source.get("tampering"))
    tampering_details = _mapping(source.get("tampering_details")) or _mapping(
        tampering_payload.get("details")
    )
    proxy_payload = _mapping(source.get("proxy"))
    proxy_details = _mapping(source.get("proxy_details")) or _mapping(
        proxy_payload.get("details")
    )
    ip_blocklist = _mapping(source.get("ip_blocklist"))
    ip_info = _ip_info(source)
    geolocation = _mapping(ip_info.get("geolocation"))
    browser_details = _mapping(source.get("browser_details"))

    return {
        "bot": _signal_text(source.get("bot")),
        "bot_type": _clean_string(source.get("bot_type"), max_length=128),
        "bot_info": {
            "category": _clean_string(bot_info.get("category"), max_length=128),
            "provider": _clean_string(bot_info.get("provider"), max_length=128),
            "name": _clean_string(bot_info.get("name"), max_length=128),
            "identity": _clean_string(bot_info.get("identity"), max_length=64).lower(),
        },
        "suspect_score": _optional_number(source.get("suspect_score")),
        "developer_tools": _optional_bool(source.get("developer_tools")),
        "replayed": _optional_bool(source.get("replayed")),
        "tampering": _optional_bool(source.get("tampering")),
        "tampering_confidence": _clean_string(
            source.get("tampering_confidence"),
            max_length=64,
        ),
        "tampering_ml_score": _optional_number(source.get("tampering_ml_score")),
        "anti_detect_browser": _optional_bool(tampering_details.get("anti_detect_browser")),
        "virtual_machine": _optional_bool(source.get("virtual_machine")),
        "virtual_machine_ml_score": _optional_number(
            source.get("virtual_machine_ml_score")
        ),
        "privacy_settings": _optional_bool(source.get("privacy_settings")),
        "rare_device": _optional_bool(source.get("rare_device")),
        "rare_device_bucket": _clean_string(
            source.get("rare_device_percentile_bucket"),
            max_length=64,
        ),
        "high_activity_device": _optional_bool(source.get("high_activity_device")),
        "vpn": _optional_bool(source.get("vpn")),
        "vpn_confidence": _clean_string(source.get("vpn_confidence"), max_length=64),
        "vpn_methods": _safe_scalar_mapping(source.get("vpn_methods")),
        "proxy": _optional_bool(source.get("proxy")),
        "proxy_type": _clean_string(proxy_details.get("proxy_type"), max_length=64),
        "tor": _optional_bool(source.get("tor")),
        "ip_blocklist_email_spam": _optional_bool(ip_blocklist.get("email_spam")),
        "ip_blocklist_attack_source": _optional_bool(ip_blocklist.get("attack_source")),
        "ip_blocklist_tor_node": _optional_bool(ip_blocklist.get("tor_node")),
        "datacenter": _optional_bool(ip_info.get("datacenter_result")),
        "asn": _clean_string(ip_info.get("asn"), max_length=64),
        "asn_name": _clean_string(ip_info.get("asn_name"), max_length=255),
        "asn_type": _clean_string(ip_info.get("asn_type"), max_length=64),
        "country_code": _clean_string(geolocation.get("country_code"), max_length=8),
        "country_name": _clean_string(geolocation.get("country_name"), max_length=128),
        "city_name": _clean_string(geolocation.get("city_name"), max_length=128),
        "ip_address": _clean_string(source.get("ip_address"), max_length=64),
        "browser_name": _clean_string(browser_details.get("browser_name"), max_length=64),
        "browser_version": _clean_string(browser_details.get("browser_full_version"), max_length=64),
        "os": _clean_string(browser_details.get("os"), max_length=64),
        "os_version": _clean_string(browser_details.get("os_version"), max_length=64),
        "device": _clean_string(browser_details.get("device"), max_length=64),
        "visitor_found": _optional_bool(identification.get("visitor_found")),
        "visitor_confidence": _optional_number(confidence.get("score")),
        "velocity": _mapping(source.get("velocity")),
    }


def normalize_fingerprint_client_signals(value):
    source = _mapping(value)
    visitor_found = source.get("visitor_found")
    if not isinstance(visitor_found, bool):
        visitor_found = None
    confidence = _optional_number(source.get("confidence"))
    if confidence is not None:
        confidence = max(0, min(confidence, 1))
    integration_error = _clean_string(source.get("integration_error"), max_length=32)
    if integration_error not in {
        "agent_error",
        "csp_block",
        "forbidden_origin",
        "invalid_browser_key",
        "network_error",
        "timeout",
    }:
        integration_error = ""
    return {
        "visitor_found": visitor_found,
        "visitor_confidence": confidence,
        "integration_error": integration_error,
    }


def _server_signals(request):
    observed_ip = _client_ip(request)
    ip_version = None
    ip_scope = "Unavailable"
    if observed_ip:
        try:
            parsed_ip = ip_address(observed_ip)
            ip_version = parsed_ip.version
            ip_scope = "Public" if parsed_ip.is_global else "Private or reserved"
        except ValueError:
            ip_scope = "Invalid"

    return {
        "ip_address": observed_ip,
        "ip_version": ip_version,
        "ip_scope": ip_scope,
        "user_agent": _clean_string(request.META.get("HTTP_USER_AGENT"), max_length=2048),
        "accept_language": _clean_string(request.META.get("HTTP_ACCEPT_LANGUAGE"), max_length=256),
        "sec_ch_ua": _clean_string(request.META.get("HTTP_SEC_CH_UA"), max_length=512),
        "sec_ch_ua_platform": _clean_string(
            request.META.get("HTTP_SEC_CH_UA_PLATFORM"),
            max_length=128,
        ),
        "sec_ch_ua_mobile": _clean_string(
            request.META.get("HTTP_SEC_CH_UA_MOBILE"),
            max_length=32,
        ),
        "sec_fetch_site": _clean_string(request.META.get("HTTP_SEC_FETCH_SITE"), max_length=32),
        "sec_fetch_mode": _clean_string(request.META.get("HTTP_SEC_FETCH_MODE"), max_length=32),
    }


def _status(value, *, flagged_when=True):
    if value is None:
        return "unavailable"
    return "flagged" if value is flagged_when else "clear"


def _display_bool(value):
    if value is None:
        return "Unavailable"
    return "Detected" if value else "Not detected"


def _check(key, label, status, value, detail, *, source, contribution=0):
    return {
        "key": key,
        "label": label,
        "status": status,
        "value": value,
        "detail": detail,
        "source": source,
        "contribution": contribution,
    }


def _fingerprint_check(
    key,
    label,
    value,
    detail,
    *,
    flagged_when=True,
    contribution=0,
):
    return _check(
        key,
        label,
        _status(value, flagged_when=flagged_when),
        _display_bool(value),
        detail,
        source="Fingerprint",
        contribution=contribution if value is flagged_when else 0,
    )


def _navigator_anomalies(client):
    anomalies = []
    languages = client.get("languages")
    if isinstance(languages, list) and not languages:
        anomalies.append("No browser languages reported")
    hardware = client.get("hardware_concurrency")
    if hardware is not None and hardware < 1:
        anomalies.append("Invalid processor count")
    width = client.get("screen_width")
    height = client.get("screen_height")
    if width is not None and height is not None and (width <= 0 or height <= 0):
        anomalies.append("Invalid screen dimensions")
    touch_points = client.get("max_touch_points")
    if touch_points is not None and touch_points < 0:
        anomalies.append("Invalid touch-point count")
    return anomalies


def _summarize_mapping(value, *, max_items=5):
    parts = []
    for raw_key, raw_value in list(_mapping(value).items())[:max_items]:
        label = _clean_string(raw_key, max_length=64).replace("_", " ")
        if not label:
            continue
        if isinstance(raw_value, dict):
            nested = []
            for nested_key, nested_value in list(raw_value.items())[:3]:
                if isinstance(nested_value, (int, float)) and not isinstance(
                    nested_value,
                    bool,
                ):
                    nested.append(
                        f"{_clean_string(nested_key, max_length=32).replace('_', ' ')}: {nested_value:g}"
                    )
            if nested:
                parts.append(f"{label} ({', '.join(nested)})")
        elif isinstance(raw_value, (bool, int, float, str)):
            parts.append(f"{label}: {raw_value}")
    return "; ".join(parts)


def _score_evidence(client, fingerprint):
    automation = []
    if client.get("webdriver") is True:
        automation.append(("webdriver", 70))
    if client.get("headless_user_agent") is True:
        automation.append(("headless_user_agent", 60))
    if client.get("devtools_agent") is True:
        automation.append(("devtools_agent", 45))
    if client.get("automation_globals"):
        automation.append(("automation_globals", 35))
    if client.get("cdp_detected") is True or fingerprint.get("developer_tools") is True:
        automation.append(("devtools_cdp", 25))

    integrity = []
    if fingerprint.get("anti_detect_browser") is True:
        integrity.append(("anti_detect_browser", 30))
    if fingerprint.get("tampering") is True:
        integrity.append(("tampering", 20))
    if client.get("ua_ch_mismatch") is True:
        integrity.append(("ua_ch_mismatch", 15))
    anomaly_count = min(len(_navigator_anomalies(client)), 3)
    if anomaly_count:
        integrity.append(("navigator_anomalies", anomaly_count * 5))
    if client.get("software_renderer") is True:
        integrity.append(("software_renderer", 8))

    activity = []
    if fingerprint.get("replayed") is True:
        activity.append(("replayed", 25))
    if fingerprint.get("high_activity_device") is True:
        activity.append(("high_activity_device", 15))

    network = []
    if fingerprint.get("ip_blocklist_attack_source") is True:
        network.append(("ip_attack_source", 10))
    proxy_type = fingerprint.get("proxy_type")
    if fingerprint.get("proxy") is True and proxy_type == "data_center":
        network.append(("datacenter_proxy", 8))
    elif fingerprint.get("proxy") is True and proxy_type == "residential":
        network.append(("residential_proxy", 4))

    groups = {
        "automation": {"cap": 90, "evidence": automation},
        "integrity": {"cap": 35, "evidence": integrity},
        "activity": {"cap": 25, "evidence": activity},
        "network": {"cap": 10, "evidence": network},
    }
    score = sum(min(group["cap"], sum(weight for _, weight in group["evidence"])) for group in groups.values())
    contributions = {
        key: weight
        for group in groups.values()
        for key, weight in group["evidence"]
    }
    return min(100, score), contributions


def _verdict(client, fingerprint):
    bot = fingerprint.get("bot", "").lower()
    bot_info = fingerprint.get("bot_info") or {}
    identity = _clean_string(bot_info.get("identity"), max_length=64).lower()
    provider = _clean_string(bot_info.get("provider"), max_length=128)
    name = _clean_string(bot_info.get("name"), max_length=128)
    identity_label = " ".join(part for part in (provider, name) if part) or "Recognized agent"

    if identity in {"verified", "signed"}:
        return {
            "code": "verified_automation",
            "label": "Verified automation",
            "score": 100,
            "tone": "info",
            "summary": f"{identity_label} presented a {identity} identity.",
        }, {}
    if identity == "spoofed":
        return {
            "code": "spoofed_automation",
            "label": "Likely automated",
            "score": 100,
            "tone": "danger",
            "summary": "A recognized automated identity failed verification.",
        }, {}
    if identity == "unknown":
        return {
            "code": "recognized_unverified_automation",
            "label": "Recognized automation",
            "score": 100,
            "tone": "warning",
            "summary": f"{identity_label} was recognized, but its identity was not verified.",
        }, {}
    if bot == "good":
        return {
            "code": "recognized_automation",
            "label": "Recognized automation",
            "score": 100,
            "tone": "info",
            "summary": f"{identity_label} was recognized as an allowed bot.",
        }, {}
    if bot == "bad":
        return {
            "code": "likely_automated",
            "label": "Likely automated",
            "score": 100,
            "tone": "danger",
            "summary": "Fingerprint classified this browser as unrecognized automation.",
        }, {}

    score, contributions = _score_evidence(client, fingerprint)
    if score >= 70:
        code = "likely_automated"
        label = "Likely automated"
        tone = "danger"
        summary = "Multiple strong browser automation indicators were detected."
    elif score >= 35:
        code = "automation_signals"
        label = "Automation signals detected"
        tone = "warning"
        summary = "Some automation indicators were detected, but the result is not conclusive."
    else:
        code = "no_strong_signals"
        label = "No strong bot signals"
        tone = "success"
        summary = "The scan did not find strong evidence of browser automation."
    return {
        "code": code,
        "label": label,
        "score": score,
        "tone": tone,
        "summary": summary,
    }, contributions


def build_bot_check_report(client, server, fingerprint=None, *, fingerprint_status):
    fingerprint = fingerprint or {}
    verdict, contributions = _verdict(client, fingerprint)
    anomalies = _navigator_anomalies(client)
    automation_globals = client.get("automation_globals") or []
    cdp_detected = bool(
        client.get("cdp_detected") is True or fingerprint.get("developer_tools") is True
    )

    automation_checks = [
        _check(
            "webdriver",
            "WebDriver flag",
            _status(client.get("webdriver")),
            _display_bool(client.get("webdriver")),
            "Browsers controlled through common automation APIs often expose this flag.",
            source="Browser",
            contribution=contributions.get("webdriver", 0),
        ),
        _check(
            "headless_user_agent",
            "Headless browser signature",
            _status(client.get("headless_user_agent")),
            _display_bool(client.get("headless_user_agent")),
            "Headless tokens in the browser identity are a strong automation indicator.",
            source="Browser",
            contribution=contributions.get("headless_user_agent", 0),
        ),
        _check(
            "automation_globals",
            "Automation framework artifacts",
            "flagged" if automation_globals else "clear",
            ", ".join(automation_globals) if automation_globals else "Not detected",
            "Some browser-driving tools leave recognizable global objects behind.",
            source="Browser",
            contribution=contributions.get("automation_globals", 0),
        ),
        _check(
            "devtools_agent",
            "DevTools agent discovery",
            _status(client.get("devtools_agent")),
            _display_bool(client.get("devtools_agent")),
            "Chrome DevTools for agents can announce an active agent-capable session.",
            source="Browser",
            contribution=contributions.get("devtools_agent", 0),
        ),
        _check(
            "devtools_cdp",
            "Developer Tools / CDP",
            "flagged" if cdp_detected else (
                "clear"
                if client.get("cdp_detected") is False
                or fingerprint.get("developer_tools") is False
                else "unavailable"
            ),
            "Detected" if cdp_detected else (
                "Not detected"
                if client.get("cdp_detected") is False
                or fingerprint.get("developer_tools") is False
                else "Unavailable"
            ),
            "This may indicate an active CDP controller or developer tools opened manually.",
            source="Browser + Fingerprint",
            contribution=contributions.get("devtools_cdp", 0),
        ),
    ]

    integrity_checks = [
        _fingerprint_check(
            "anti_detect_browser",
            "Anti-detect browser",
            fingerprint.get("anti_detect_browser"),
            "Anti-detect browsers deliberately alter identifying browser characteristics.",
            contribution=contributions.get("anti_detect_browser", 0),
        ),
        _fingerprint_check(
            "tampering",
            "Browser tampering",
            fingerprint.get("tampering"),
            (
                "Fingerprint checks for anomalous or deliberately modified browser attributes. "
                f"Confidence: {fingerprint.get('tampering_confidence') or 'not provided'}; "
                f"ML score: {fingerprint.get('tampering_ml_score') if fingerprint.get('tampering_ml_score') is not None else 'not provided'}."
            ),
            contribution=contributions.get("tampering", 0),
        ),
        _check(
            "ua_ch_mismatch",
            "Browser identity consistency",
            _status(client.get("ua_ch_mismatch")),
            "Mismatch detected" if client.get("ua_ch_mismatch") else (
                "Consistent" if client.get("ua_ch_mismatch") is False else "Unavailable"
            ),
            "The User-Agent and modern Client Hints should describe a consistent platform.",
            source="Browser + server",
            contribution=contributions.get("ua_ch_mismatch", 0),
        ),
        _check(
            "navigator_anomalies",
            "Navigator values",
            "flagged" if anomalies else "clear",
            "; ".join(anomalies) if anomalies else "Plausible values",
            "Impossible or empty browser values can reveal an incomplete synthetic environment.",
            source="Browser",
            contribution=contributions.get("navigator_anomalies", 0),
        ),
        _check(
            "software_renderer",
            "Software graphics renderer",
            _status(client.get("software_renderer")),
            _display_bool(client.get("software_renderer")),
            "Software rendering is common in virtualized browsers, but can also be legitimate.",
            source="Browser",
            contribution=contributions.get("software_renderer", 0),
        ),
        _check(
            "request_headers",
            "Request identity headers",
            "info" if server.get("user_agent") else "unavailable",
            server.get("user_agent") or "Unavailable",
            (
                f"Accept-Language: {server.get('accept_language') or 'not provided'}; "
                f"Client-Hints platform: {server.get('sec_ch_ua_platform') or 'not provided'}; "
                f"fetch context: {server.get('sec_fetch_site') or 'not provided'}/"
                f"{server.get('sec_fetch_mode') or 'not provided'}."
            ),
            source="Server",
        ),
    ]

    ip_display = server.get("ip_address") or "Unavailable"
    fingerprint_ip = fingerprint.get("ip_address")
    ip_matches = None
    if server.get("ip_address") and fingerprint_ip:
        ip_matches = server["ip_address"] == fingerprint_ip
    network_checks = [
        _check(
            "observed_ip",
            "Observed IP address",
            "info" if server.get("ip_address") else "unavailable",
            ip_display,
            f"{server.get('ip_scope', 'Unavailable')} IPv{server.get('ip_version') or '?'} address observed by Gobii.",
            source="Server",
        ),
        _check(
            "fingerprint_ip_match",
            "Network path consistency",
            "info" if ip_matches is not None else "unavailable",
            "Matches" if ip_matches else ("Different paths observed" if ip_matches is False else "Unavailable"),
            "Fingerprint and Gobii may see different addresses when IPv4, IPv6, relays, or proxies are involved.",
            source="Server + Fingerprint",
        ),
        _fingerprint_check(
            "proxy",
            "Proxy",
            fingerprint.get("proxy"),
            f"Proxy type: {fingerprint.get('proxy_type') or 'not provided'}. A proxy is context, not proof of a bot.",
            contribution=(
                contributions.get("datacenter_proxy", 0)
                or contributions.get("residential_proxy", 0)
            ),
        ),
        _fingerprint_check(
            "vpn",
            "VPN",
            fingerprint.get("vpn"),
            (
                "VPN use is privacy and network context; it does not increase the automation score. "
                f"Confidence: {fingerprint.get('vpn_confidence') or 'not provided'}; "
                f"methods: {_summarize_mapping(fingerprint.get('vpn_methods')) or 'not provided'}."
            ),
            contribution=0,
        ),
        _fingerprint_check(
            "tor",
            "Tor exit node",
            fingerprint.get("tor"),
            "Tor use is privacy and network context; it does not independently indicate automation.",
            contribution=0,
        ),
        _fingerprint_check(
            "datacenter",
            "Datacenter network",
            fingerprint.get("datacenter"),
            "Datacenter hosting is common for automation and also for legitimate corporate traffic.",
            contribution=0,
        ),
        _fingerprint_check(
            "ip_attack_source",
            "IP attack reputation",
            fingerprint.get("ip_blocklist_attack_source"),
            "The address has recently appeared as an attack source in reputation data.",
            contribution=contributions.get("ip_attack_source", 0),
        ),
        _fingerprint_check(
            "ip_email_spam",
            "IP email-spam reputation",
            fingerprint.get("ip_blocklist_email_spam"),
            "The address has recently appeared in email-spam reputation data.",
            contribution=0,
        ),
        _fingerprint_check(
            "ip_tor_node",
            "IP blocklist Tor match",
            fingerprint.get("ip_blocklist_tor_node"),
            "A separate reputation source identified the address as a Tor node.",
            contribution=0,
        ),
    ]

    screen_value = "Unavailable"
    if client.get("screen_width") is not None and client.get("screen_height") is not None:
        screen_value = f"{int(client['screen_width'])} × {int(client['screen_height'])}"
    renderer = client.get("webgl_renderer") or "Unavailable"
    device_checks = [
        _check(
            "platform",
            "Platform and timezone",
            "info",
            f"{client.get('platform') or 'Unknown'} · {client.get('timezone') or 'Unknown timezone'}",
            "Browser-reported platform context.",
            source="Browser",
        ),
        _check(
            "screen",
            "Screen",
            "info" if screen_value != "Unavailable" else "unavailable",
            screen_value,
            f"Color depth: {client.get('color_depth') if client.get('color_depth') is not None else 'unknown'}.",
            source="Browser",
        ),
        _check(
            "hardware",
            "Hardware profile",
            "info",
            (
                f"{int(client['hardware_concurrency'])} logical processors"
                if client.get("hardware_concurrency") is not None
                else "Processor count unavailable"
            ),
            (
                f"Reported memory: {client.get('device_memory')} GB; "
                f"touch points: {int(client.get('max_touch_points') or 0)}."
            ),
            source="Browser",
        ),
        _check(
            "webgl",
            "Graphics renderer",
            "info" if renderer != "Unavailable" else "unavailable",
            renderer,
            f"Vendor: {client.get('webgl_vendor') or 'unavailable'}.",
            source="Browser",
        ),
        _check(
            "storage",
            "Browser storage",
            "info",
            (
                f"Cookies {'on' if client.get('cookies_enabled') else 'off'} · "
                f"local {'on' if client.get('local_storage') else 'off'} · "
                f"session {'on' if client.get('session_storage') else 'off'}"
            ),
            "Storage restrictions can reduce scan coverage but are not automation evidence.",
            source="Browser",
        ),
    ]

    bot_info = fingerprint.get("bot_info") or {}
    bot_value = fingerprint.get("bot")
    if bot_info.get("name") or bot_info.get("provider"):
        bot_display = " · ".join(
            item
            for item in (
                bot_info.get("provider"),
                bot_info.get("name"),
                bot_info.get("identity"),
            )
            if item
        )
    else:
        bot_display = bot_value or "Unavailable"
    fingerprint_checks = [
        _check(
            "fingerprint_bot",
            "Bot classification",
            "flagged" if bot_value in {"bad", "good"} else (
                "clear" if bot_value == "not_detected" else "unavailable"
            ),
            bot_display,
            "Verified identity metadata takes precedence over the legacy good/bad classification.",
            source="Fingerprint",
        ),
        _check(
            "suspect_score",
            "Fingerprint Suspect Score",
            "info" if fingerprint.get("suspect_score") is not None else "unavailable",
            (
                str(fingerprint.get("suspect_score"))
                if fingerprint.get("suspect_score") is not None
                else "Unavailable"
            ),
            "A configurable fraud-context score shown separately from this page's automation score.",
            source="Fingerprint",
        ),
        _fingerprint_check(
            "replayed",
            "Replayed request",
            fingerprint.get("replayed"),
            "A replayed identification event can indicate reuse of captured browser traffic.",
            contribution=contributions.get("replayed", 0),
        ),
        _fingerprint_check(
            "virtual_machine",
            "Virtual machine",
            fingerprint.get("virtual_machine"),
            (
                "Virtual machines are common in automated infrastructure and legitimate development. "
                f"ML score: {fingerprint.get('virtual_machine_ml_score') if fingerprint.get('virtual_machine_ml_score') is not None else 'not provided'}."
            ),
            contribution=0,
        ),
        _fingerprint_check(
            "privacy_settings",
            "Privacy-focused settings",
            fingerprint.get("privacy_settings"),
            "Privacy protections are informational and never independently treated as bot evidence.",
            contribution=0,
        ),
        _fingerprint_check(
            "rare_device",
            "Rare device profile",
            fingerprint.get("rare_device"),
            f"Rarity bucket: {fingerprint.get('rare_device_bucket') or 'not provided'}.",
            contribution=0,
        ),
        _fingerprint_check(
            "high_activity",
            "High-activity device",
            fingerprint.get("high_activity_device"),
            "This device is unusually active relative to other visitors.",
            contribution=contributions.get("high_activity_device", 0),
        ),
        _check(
            "visitor_confidence",
            "Visitor recognition",
            (
                "info"
                if fingerprint.get("visitor_found") is not None
                or fingerprint.get("visitor_confidence") is not None
                else "unavailable"
            ),
            (
                "Seen before"
                if fingerprint.get("visitor_found") is True
                else (
                    "First observed"
                    if fingerprint.get("visitor_found") is False
                    else "Recognition unavailable"
                )
            ),
            (
                "Identification confidence: "
                f"{fingerprint.get('visitor_confidence') if fingerprint.get('visitor_confidence') is not None else 'not provided'}."
            ),
            source="Fingerprint",
        ),
        _check(
            "velocity",
            "Recent activity velocity",
            "info" if fingerprint.get("velocity") else "unavailable",
            _summarize_mapping(fingerprint.get("velocity")) or "Unavailable",
            "Recent event, IP, country, or linked-identity counts when supplied by Fingerprint.",
            source="Fingerprint",
        ),
        _check(
            "fingerprint_device",
            "Provider-observed browser",
            (
                "info"
                if fingerprint.get("browser_name")
                or fingerprint.get("os")
                or fingerprint.get("device")
                else "unavailable"
            ),
            " · ".join(
                item
                for item in (
                    " ".join(
                        item
                        for item in (
                            fingerprint.get("browser_name"),
                            fingerprint.get("browser_version"),
                        )
                        if item
                    ),
                    " ".join(
                        item
                        for item in (
                            fingerprint.get("os"),
                            fingerprint.get("os_version"),
                        )
                        if item
                    ),
                    fingerprint.get("device"),
                )
                if item
            ) or "Unavailable",
            "Browser, operating system, and device classification from the provider event.",
            source="Fingerprint",
        ),
        _check(
            "network_owner",
            "Network owner",
            "info" if fingerprint.get("asn") or fingerprint.get("asn_name") else "unavailable",
            " · ".join(
                item
                for item in (
                    fingerprint.get("asn"),
                    fingerprint.get("asn_name"),
                    fingerprint.get("asn_type"),
                )
                if item
            ) or "Unavailable",
            "ASN and organization type provide network context.",
            source="Fingerprint",
        ),
        _check(
            "approximate_location",
            "Approximate location",
            "info" if fingerprint.get("country_name") else "unavailable",
            ", ".join(
                item
                for item in (
                    fingerprint.get("city_name"),
                    fingerprint.get("country_name"),
                )
                if item
            ) or "Unavailable",
            "Coarse IP-derived location; no precise coordinates are included.",
            source="Fingerprint",
        ),
    ]

    categories = [
        {
            "key": "automation",
            "label": "Automation",
            "description": "Direct browser-control and headless-environment indicators.",
            "checks": automation_checks,
        },
        {
            "key": "integrity",
            "label": "Browser integrity",
            "description": "Consistency and tampering signals from the runtime.",
            "checks": integrity_checks,
        },
        {
            "key": "network",
            "label": "Network & IP",
            "description": "The public network path and reputation context.",
            "checks": network_checks,
        },
        {
            "key": "device",
            "label": "Device",
            "description": "Browser-reported hardware, display, graphics, and storage context.",
            "checks": device_checks,
        },
        {
            "key": "fingerprint",
            "label": "Fingerprint intelligence",
            "description": "Server-verified device intelligence when Fingerprint is configured.",
            "checks": fingerprint_checks,
        },
    ]
    all_checks = [check for category in categories for check in category["checks"]]
    completed = sum(check["status"] != "unavailable" for check in all_checks)

    return {
        "verdict": verdict,
        "coverage": {
            "completed": completed,
            "total": len(all_checks),
        },
        "categories": categories,
        "fingerprint_status": fingerprint_status,
    }


@require_GET
@never_cache
@ensure_csrf_cookie
def bot_check_page(request):
    response = render(
        request,
        "bot_check.html",
        {
            "suppress_public_conversion_assets": True,
            "suppress_signup_tracking_snippet": True,
        },
    )
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


@require_POST
def bot_check_start(request):
    if not _admit_scan(request):
        response = _json_response(
            {
                "error": "Too many scans from this network. Try again later.",
                "code": "rate_limited",
            },
            status=429,
        )
        response["Retry-After"] = "3600"
        return response

    return _json_response(
        {
            "scan_token": _make_scan_token(request),
            "fingerprint": _fingerprint_browser_config(),
        }
    )


@require_POST
def bot_check_complete(request):
    payload, error = _read_json_body(request)
    if error:
        return _json_response({"error": error, "code": "invalid_request"}, status=400)

    raw_token = payload.get("scan_token")
    if (
        not isinstance(raw_token, str)
        or not raw_token.strip()
        or len(raw_token.strip()) > BOT_CHECK_MAX_TOKEN_LENGTH
    ):
        return _json_response(
            {"error": "A scan token is required.", "code": "invalid_token"},
            status=400,
        )
    token = raw_token.strip()
    token_payload, error = _read_scan_token(request, token)
    if error:
        return _json_response({"error": error, "code": "invalid_token"}, status=400)

    if not isinstance(payload.get("client_signals"), dict):
        return _json_response(
            {
                "error": "Browser signals must be a JSON object.",
                "code": "invalid_request",
            },
            status=400,
        )
    client = normalize_client_signals(payload["client_signals"])
    server = _server_signals(request)
    raw_event_id = payload.get("fingerprint_event_id", "")
    if not isinstance(raw_event_id, str) or len(raw_event_id.strip()) > BOT_CHECK_MAX_EVENT_ID_LENGTH:
        return _json_response(
            {
                "error": "The Fingerprint event identifier is invalid.",
                "code": "invalid_request",
            },
            status=400,
        )
    event_id = raw_event_id.strip()
    fingerprint_client = normalize_fingerprint_client_signals(
        payload.get("fingerprint_client")
    )
    fingerprint = (
        fingerprint_client
        if any(value not in (None, "") for value in fingerprint_client.values())
        else None
    )
    fingerprint_status = "unavailable"
    fingerprint_config = _fingerprint_browser_config()

    if fingerprint_client["integration_error"]:
        fingerprint_status = (
            "client_error"
            if fingerprint_client["integration_error"] == "agent_error"
            else f"client_{fingerprint_client['integration_error']}"
        )
    elif fingerprint_config["enabled"] and not event_id:
        fingerprint_status = "missing_event"

    if event_id and fingerprint_config["enabled"]:
        fingerprint_status = (
            "browser_only"
            if not fingerprint_config["server_intelligence_enabled"]
            else "unavailable"
        )

    if event_id and fingerprint_config["server_intelligence_enabled"]:
        try:
            fingerprint_payload = fetch_fingerprint_event_payload(event_id)
        except FingerprintRetryableError:
            poll_key = f"{BOT_CHECK_POLL_CACHE_PREFIX}:{token_payload['scan_id']}"
            if cache.add(
                poll_key,
                1,
                timeout=BOT_CHECK_SCAN_TOKEN_MAX_AGE_SECONDS,
            ):
                poll_count = 1
            else:
                try:
                    poll_count = cache.incr(poll_key)
                except ValueError:
                    cache.set(
                        poll_key,
                        1,
                        timeout=BOT_CHECK_SCAN_TOKEN_MAX_AGE_SECONDS,
                    )
                    poll_count = 1
            if poll_count <= BOT_CHECK_FINGERPRINT_MAX_POLLS:
                response = _json_response(
                    {
                        "status": "fingerprint_pending",
                        "retry_after_ms": BOT_CHECK_FINGERPRINT_RETRY_AFTER_MS,
                    },
                    status=202,
                )
                response["Retry-After"] = str(
                    max(1, BOT_CHECK_FINGERPRINT_RETRY_AFTER_MS // 1000)
                )
                return response
            fingerprint_status = "timed_out"
        except FingerprintConfigurationError:
            fingerprint_status = "unavailable"
        except FingerprintTerminalError:
            fingerprint_status = "error"
        else:
            fingerprint = normalize_fingerprint_signals(fingerprint_payload)
            fingerprint_status = "complete"
            cache.delete(f"{BOT_CHECK_POLL_CACHE_PREFIX}:{token_payload['scan_id']}")

    report = build_bot_check_report(
        client,
        server,
        fingerprint,
        fingerprint_status=fingerprint_status,
    )
    return _json_response({"status": "complete", "report": report})
