from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from django.core.cache import cache

from api.models import DailyCreditConfig

DEFAULT_SLIDER_MIN = Decimal("0")
DEFAULT_SLIDER_MAX = Decimal("50")
DEFAULT_SLIDER_STEP = Decimal("1")
DEFAULT_BURN_RATE_THRESHOLD = Decimal("3")
DEFAULT_BURN_RATE_WINDOW_MINUTES = 60

_CACHE_KEY = "daily_credit_settings:v1"
_CACHE_TTL_SECONDS = 300


@dataclass(frozen=True)
class DailyCreditSettings:
    slider_min: Decimal
    slider_max: Decimal
    slider_step: Decimal
    burn_rate_threshold_per_hour: Decimal
    burn_rate_window_minutes: int


def _serialise(config: DailyCreditConfig) -> dict:
    return {
        "slider_min": config.slider_min or DEFAULT_SLIDER_MIN,
        "slider_max": config.slider_max or DEFAULT_SLIDER_MAX,
        "slider_step": config.slider_step or DEFAULT_SLIDER_STEP,
        "burn_rate_threshold_per_hour": (
            config.burn_rate_threshold_per_hour or DEFAULT_BURN_RATE_THRESHOLD
        ),
        "burn_rate_window_minutes": (
            config.burn_rate_window_minutes or DEFAULT_BURN_RATE_WINDOW_MINUTES
        ),
    }


def get_daily_credit_settings() -> DailyCreditSettings:
    """Return the cached daily credit settings (falls back to defaults)."""
    cached: Optional[dict] = cache.get(_CACHE_KEY)
    if cached:
        return DailyCreditSettings(**cached)

    config = DailyCreditConfig.objects.order_by("singleton_id").first()
    if config is None:
        config = DailyCreditConfig.objects.create(
            slider_min=DEFAULT_SLIDER_MIN,
            slider_max=DEFAULT_SLIDER_MAX,
            slider_step=DEFAULT_SLIDER_STEP,
            burn_rate_threshold_per_hour=DEFAULT_BURN_RATE_THRESHOLD,
            burn_rate_window_minutes=DEFAULT_BURN_RATE_WINDOW_MINUTES,
        )
    data = _serialise(config)
    cache.set(_CACHE_KEY, data, _CACHE_TTL_SECONDS)
    return DailyCreditSettings(**data)


def invalidate_daily_credit_settings_cache() -> None:
    cache.delete(_CACHE_KEY)
