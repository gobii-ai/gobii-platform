"""Shared Stripe-related constants."""

CHECKOUT_PAYMENT_METHOD_TYPES = ["card", "link"]
PERSONAL_CHECKOUT_PAYMENT_METHOD_TYPES = ["card"]
EXCLUDED_PAYMENT_METHOD_TYPES = ["cashapp", "klarna"]

ORG_OVERAGE_STATE_META_KEY = "org_overage_sku_state"
ORG_OVERAGE_STATE_DETACHED_PENDING = "detached_pending"
