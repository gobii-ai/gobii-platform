from decimal import Decimal

# Shared limits for agent daily credit management.
SOFT_TARGET_MIN = Decimal("0")
SOFT_TARGET_MAX = Decimal("50")
# Slider/UI precision for soft target adjustments.
SOFT_TARGET_STEP = Decimal("0.25")

# Burn-rate calculations use a single global rolling window (in minutes).
BURN_RATE_WINDOW_MINUTES = 60

__all__ = [
    "SOFT_TARGET_MIN",
    "SOFT_TARGET_MAX",
    "SOFT_TARGET_STEP",
    "BURN_RATE_WINDOW_MINUTES",
]
