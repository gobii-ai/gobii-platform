from typing import Any, Mapping

from api.services.user_fingerprint import (
    get_fp_bot,
    get_fp_country,
    get_fp_proxy,
    get_fp_suspect_score,
    get_fp_tampering,
)

STRIPE_CHECKOUT_FLOW_TYPE_PURCHASE = "purchase"
STRIPE_CHECKOUT_FLOW_TYPE_TRIAL = "trial"
STRIPE_CHECKOUT_CUSTOMER_FLOW_TYPE_META_KEY = "active_checkout_flow_type"
STRIPE_CHECKOUT_CUSTOMER_EVENT_ID_META_KEY = "active_checkout_event_id"
STRIPE_CHECKOUT_CUSTOMER_PLAN_META_KEY = "active_checkout_plan"
STRIPE_CHECKOUT_CUSTOMER_PLAN_LABEL_META_KEY = "active_checkout_plan_label"
STRIPE_CHECKOUT_CUSTOMER_VALUE_META_KEY = "active_checkout_value"
STRIPE_CHECKOUT_CUSTOMER_CURRENCY_META_KEY = "active_checkout_currency"
STRIPE_CHECKOUT_CUSTOMER_SOURCE_URL_META_KEY = "active_checkout_source_url"
STRIPE_CHECKOUT_FP_SUSPECT_SCORE_META_KEY = "fp_suspect_score"
STRIPE_CHECKOUT_FP_COUNTRY_META_KEY = "fp_country"
STRIPE_CHECKOUT_FP_PROXY_META_KEY = "fp_proxy"
STRIPE_CHECKOUT_FP_TAMPERING_META_KEY = "fp_tampering"
STRIPE_CHECKOUT_FP_BOT_META_KEY = "fp_bot"
STRIPE_CHECKOUT_CUSTOMER_FP_SUSPECT_SCORE_META_KEY = "active_checkout_fp_suspect_score"
STRIPE_CHECKOUT_CUSTOMER_FP_COUNTRY_META_KEY = "active_checkout_fp_country"
STRIPE_CHECKOUT_CUSTOMER_FP_PROXY_META_KEY = "active_checkout_fp_proxy"
STRIPE_CHECKOUT_CUSTOMER_FP_TAMPERING_META_KEY = "active_checkout_fp_tampering"
STRIPE_CHECKOUT_CUSTOMER_FP_BOT_META_KEY = "active_checkout_fp_bot"

_UNKNOWN_METADATA_VALUE = "unknown"


def _normalize_checkout_metadata_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_fp_bool_metadata(value: bool | None) -> str:
    if value is None:
        return _UNKNOWN_METADATA_VALUE
    return "true" if value else "false"


def _normalize_fp_country_metadata(value: str | None) -> str:
    normalized = _normalize_checkout_metadata_value(value).upper()
    return normalized or _UNKNOWN_METADATA_VALUE


def _normalize_fp_suspect_score_metadata(value: float | None) -> str:
    normalized = _normalize_checkout_metadata_value(value)
    return normalized or _UNKNOWN_METADATA_VALUE


def _normalize_fp_bot_metadata(value: str | None) -> str:
    normalized = _normalize_checkout_metadata_value(value)
    if not normalized:
        return _UNKNOWN_METADATA_VALUE

    collapsed = normalized.replace("_", "").replace(" ", "").lower()
    if collapsed == "good":
        return "good"
    if collapsed == "bad":
        return "bad"
    if collapsed == "notdetected":
        return "notDetected"
    return _UNKNOWN_METADATA_VALUE


def build_checkout_fingerprint_metadata(
    user,
    *,
    customer_context: bool = False,
) -> dict[str, str]:
    if customer_context:
        suspect_score_key = STRIPE_CHECKOUT_CUSTOMER_FP_SUSPECT_SCORE_META_KEY
        country_key = STRIPE_CHECKOUT_CUSTOMER_FP_COUNTRY_META_KEY
        proxy_key = STRIPE_CHECKOUT_CUSTOMER_FP_PROXY_META_KEY
        tampering_key = STRIPE_CHECKOUT_CUSTOMER_FP_TAMPERING_META_KEY
        bot_key = STRIPE_CHECKOUT_CUSTOMER_FP_BOT_META_KEY
    else:
        suspect_score_key = STRIPE_CHECKOUT_FP_SUSPECT_SCORE_META_KEY
        country_key = STRIPE_CHECKOUT_FP_COUNTRY_META_KEY
        proxy_key = STRIPE_CHECKOUT_FP_PROXY_META_KEY
        tampering_key = STRIPE_CHECKOUT_FP_TAMPERING_META_KEY
        bot_key = STRIPE_CHECKOUT_FP_BOT_META_KEY

    return {
        suspect_score_key: _normalize_fp_suspect_score_metadata(get_fp_suspect_score(user)),
        country_key: _normalize_fp_country_metadata(get_fp_country(user)),
        proxy_key: _normalize_fp_bool_metadata(get_fp_proxy(user)),
        tampering_key: _normalize_fp_bool_metadata(get_fp_tampering(user)),
        bot_key: _normalize_fp_bot_metadata(get_fp_bot(user)),
    }


def build_checkout_flow_metadata(
    metadata: Mapping[str, Any] | None,
    *,
    flow_type: str,
    extra_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    enriched_metadata = dict(metadata or {})
    enriched_metadata["flow_type"] = flow_type
    if extra_metadata:
        enriched_metadata.update(extra_metadata)
    return enriched_metadata


def build_checkout_customer_metadata(
    *,
    flow_type: str,
    event_id: str,
    plan: str | None = None,
    plan_label: str | None = None,
    value: float | None = None,
    currency: str | None = None,
    checkout_source_url: str | None = None,
    extra_metadata: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    metadata = {
        STRIPE_CHECKOUT_CUSTOMER_FLOW_TYPE_META_KEY: flow_type,
        STRIPE_CHECKOUT_CUSTOMER_EVENT_ID_META_KEY: event_id,
    }
    optional_metadata = {
        STRIPE_CHECKOUT_CUSTOMER_PLAN_META_KEY: plan,
        STRIPE_CHECKOUT_CUSTOMER_PLAN_LABEL_META_KEY: plan_label,
        STRIPE_CHECKOUT_CUSTOMER_VALUE_META_KEY: value,
        STRIPE_CHECKOUT_CUSTOMER_CURRENCY_META_KEY: currency,
        STRIPE_CHECKOUT_CUSTOMER_SOURCE_URL_META_KEY: checkout_source_url,
    }
    for key, raw_value in optional_metadata.items():
        normalized_value = _normalize_checkout_metadata_value(raw_value)
        if normalized_value:
            metadata[key] = normalized_value
    if extra_metadata:
        for key, raw_value in extra_metadata.items():
            normalized_value = _normalize_checkout_metadata_value(raw_value)
            if normalized_value:
                metadata[key] = normalized_value
    return metadata


def clear_checkout_customer_metadata() -> dict[str, str]:
    return {
        STRIPE_CHECKOUT_CUSTOMER_FLOW_TYPE_META_KEY: "",
        STRIPE_CHECKOUT_CUSTOMER_EVENT_ID_META_KEY: "",
        STRIPE_CHECKOUT_CUSTOMER_PLAN_META_KEY: "",
        STRIPE_CHECKOUT_CUSTOMER_PLAN_LABEL_META_KEY: "",
        STRIPE_CHECKOUT_CUSTOMER_VALUE_META_KEY: "",
        STRIPE_CHECKOUT_CUSTOMER_CURRENCY_META_KEY: "",
        STRIPE_CHECKOUT_CUSTOMER_SOURCE_URL_META_KEY: "",
        STRIPE_CHECKOUT_CUSTOMER_FP_SUSPECT_SCORE_META_KEY: "",
        STRIPE_CHECKOUT_CUSTOMER_FP_COUNTRY_META_KEY: "",
        STRIPE_CHECKOUT_CUSTOMER_FP_PROXY_META_KEY: "",
        STRIPE_CHECKOUT_CUSTOMER_FP_TAMPERING_META_KEY: "",
        STRIPE_CHECKOUT_CUSTOMER_FP_BOT_META_KEY: "",
    }
