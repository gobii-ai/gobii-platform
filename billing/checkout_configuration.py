from typing import Any

DEFAULT_STRIPE_CHECKOUT_BILLING_ADDRESS_COLLECTION = "auto"
STRIPE_CHECKOUT_BILLING_ADDRESS_COLLECTION_CHOICES = (
    ("auto", "Auto"),
    ("required", "Required"),
)

_VALID_STRIPE_CHECKOUT_BILLING_ADDRESS_COLLECTIONS = frozenset(
    choice[0] for choice in STRIPE_CHECKOUT_BILLING_ADDRESS_COLLECTION_CHOICES
)
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


def normalize_stripe_checkout_billing_address_collection(
    value: object,
    *,
    default: str = DEFAULT_STRIPE_CHECKOUT_BILLING_ADDRESS_COLLECTION,
) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in _VALID_STRIPE_CHECKOUT_BILLING_ADDRESS_COLLECTIONS:
        return normalized
    return default


def normalize_stripe_checkout_name_collection_individual_enabled(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    return str(value or "").strip().lower() in _TRUE_VALUES


def build_checkout_session_collection_kwargs(stripe_settings: Any) -> dict[str, Any]:
    return {
        "billing_address_collection": normalize_stripe_checkout_billing_address_collection(
            getattr(
                stripe_settings,
                "checkout_billing_address_collection",
                DEFAULT_STRIPE_CHECKOUT_BILLING_ADDRESS_COLLECTION,
            )
        ),
        "name_collection": {
            "individual": {
                "enabled": normalize_stripe_checkout_name_collection_individual_enabled(
                    getattr(
                        stripe_settings,
                        "checkout_name_collection_individual_enabled",
                        False,
                    )
                ),
            },
        },
    }
