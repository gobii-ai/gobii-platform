from typing import Any, Mapping

STRIPE_CHECKOUT_FLOW_TYPE_PURCHASE = "purchase"
STRIPE_CHECKOUT_FLOW_TYPE_TRIAL = "trial"
STRIPE_CHECKOUT_CUSTOMER_FLOW_TYPE_META_KEY = "active_checkout_flow_type"
STRIPE_CHECKOUT_CUSTOMER_EVENT_ID_META_KEY = "active_checkout_event_id"
STRIPE_CHECKOUT_CUSTOMER_PLAN_META_KEY = "active_checkout_plan"
STRIPE_CHECKOUT_CUSTOMER_PLAN_LABEL_META_KEY = "active_checkout_plan_label"
STRIPE_CHECKOUT_CUSTOMER_VALUE_META_KEY = "active_checkout_value"
STRIPE_CHECKOUT_CUSTOMER_CURRENCY_META_KEY = "active_checkout_currency"
STRIPE_CHECKOUT_CUSTOMER_SOURCE_URL_META_KEY = "active_checkout_source_url"


def build_checkout_flow_metadata(
    metadata: Mapping[str, Any] | None,
    *,
    flow_type: str,
) -> dict[str, Any]:
    enriched_metadata = dict(metadata or {})
    enriched_metadata["flow_type"] = flow_type
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
        normalized_value = str(raw_value).strip() if raw_value is not None else ""
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
    }
