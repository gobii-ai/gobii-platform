from typing import Any

from waffle import switch_is_active

from constants.feature_flags import STRIPE_CHECKOUT_TOS_CONSENT_REQUIRED


def apply_checkout_tos_consent_collection(checkout_kwargs: dict[str, Any]) -> None:
    if not switch_is_active(STRIPE_CHECKOUT_TOS_CONSENT_REQUIRED):
        return

    consent_collection = dict(checkout_kwargs.get("consent_collection") or {})
    consent_collection["terms_of_service"] = "required"
    checkout_kwargs["consent_collection"] = consent_collection


def create_stripe_checkout_session(stripe_module, **checkout_kwargs):
    apply_checkout_tos_consent_collection(checkout_kwargs)
    return stripe_module.checkout.Session.create(**checkout_kwargs)
