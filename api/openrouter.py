"""Helpers for interacting with OpenRouter."""
import logging
from typing import Dict
from django.conf import settings

from observability import trace

DEFAULT_API_BASE = "https://openrouter.ai/api/v1"
logger = logging.getLogger(__name__)
tracer = trace.get_tracer('gobii.utils')


def get_attribution_headers() -> Dict[str, str]:
    """Build the attribution headers required by OpenRouter."""
    referer = getattr(settings, "PUBLIC_SITE_URL", "") or ""
    title = getattr(settings, "PUBLIC_BRAND_NAME", "") or ""

    headers: Dict[str, str] = {}
    if referer:
        headers["HTTP-Referer"] = str(referer)
    if title:
        headers["X-Title"] = str(title)

    logger.info("OpenRouter attribution headers: %s", headers)

    return headers


__all__ = ["DEFAULT_API_BASE", "get_attribution_headers"]
