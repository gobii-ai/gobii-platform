from typing import Any, Mapping

STRIPE_CHECKOUT_FLOW_TYPE_PURCHASE = "purchase"
STRIPE_CHECKOUT_FLOW_TYPE_TRIAL = "trial"
STRIPE_CHECKOUT_CUSTOMER_FLOW_TYPE_META_KEY = "active_checkout_flow_type"
STRIPE_CHECKOUT_CUSTOMER_EVENT_ID_META_KEY = "active_checkout_event_id"


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
) -> dict[str, str]:
    return {
        STRIPE_CHECKOUT_CUSTOMER_FLOW_TYPE_META_KEY: flow_type,
        STRIPE_CHECKOUT_CUSTOMER_EVENT_ID_META_KEY: event_id,
    }


def clear_checkout_customer_metadata() -> dict[str, str]:
    return {
        STRIPE_CHECKOUT_CUSTOMER_FLOW_TYPE_META_KEY: "",
        STRIPE_CHECKOUT_CUSTOMER_EVENT_ID_META_KEY: "",
    }
