from typing import Any, Mapping

STRIPE_CHECKOUT_FLOW_TYPE_PURCHASE = "purchase"
STRIPE_CHECKOUT_FLOW_TYPE_TRIAL = "trial"


def build_checkout_flow_metadata(
    metadata: Mapping[str, Any] | None,
    *,
    flow_type: str,
) -> dict[str, Any]:
    enriched_metadata = dict(metadata or {})
    enriched_metadata["flow_type"] = flow_type
    return enriched_metadata
