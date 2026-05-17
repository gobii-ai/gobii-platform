"""Helpers for interacting with OpenRouter."""
import logging
from typing import Dict
from django.conf import settings

from observability import trace

DEFAULT_API_BASE = "https://openrouter.ai/api/v1"
DEFAULT_ATTRIBUTION_TITLE = "Gobii"
logger = logging.getLogger(__name__)
tracer = trace.get_tracer('gobii.utils')


def get_attribution_headers() -> Dict[str, str]:
    """Build the attribution headers required by OpenRouter."""
    referer = settings.PUBLIC_SITE_URL or ""
    title = settings.OPENROUTER_ATTRIBUTION_TITLE or DEFAULT_ATTRIBUTION_TITLE

    headers: Dict[str, str] = {}
    if referer:
        headers["HTTP-Referer"] = str(referer)
    if title:
        headers["X-Title"] = str(title).strip() or DEFAULT_ATTRIBUTION_TITLE

    logger.debug("OpenRouter attribution headers: %s", headers)

    return headers


__all__ = ["DEFAULT_API_BASE", "DEFAULT_ATTRIBUTION_TITLE", "get_attribution_headers"]
