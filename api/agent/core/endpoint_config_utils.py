"""Shared utilities for tiered endpoint config resolution.

Used by image_generation_config, video_generation_config, and file_handler_config
to avoid duplicating provider key resolution and endpoint param building.
"""

import logging
import os
from typing import Any, Dict, Optional

from api.encryption import SecretsEncryption
from api.llm.utils import normalize_model_name
from api.openrouter import get_attribution_headers

logger = logging.getLogger(__name__)


def resolve_provider_api_key(provider) -> Optional[str]:
    """Decrypt or look up the API key for a provider."""
    if provider is None or not getattr(provider, "enabled", True):
        return None

    api_key: Optional[str] = None
    encrypted = getattr(provider, "api_key_encrypted", None)
    if encrypted:
        try:
            api_key = SecretsEncryption.decrypt_value(encrypted)
        except Exception as exc:
            logger.warning(
                "Failed to decrypt API key for provider %s: %s",
                getattr(provider, "key", "unknown"),
                exc,
            )
            return None

    if not api_key:
        env_var = getattr(provider, "env_var_name", None)
        if env_var:
            api_key = os.getenv(env_var)

    return api_key or None


def build_endpoint_params(endpoint, provider) -> Optional[Dict[str, Any]]:
    """Build the common params dict for a tiered endpoint.

    Returns None if no valid credentials can be resolved (caller should skip).
    """
    params: Dict[str, Any] = {}
    api_key = resolve_provider_api_key(provider)
    if api_key:
        params["api_key"] = api_key

    api_base = (endpoint.api_base or "").strip()
    if api_base:
        params["api_base"] = api_base
        params.setdefault("api_key", "sk-noauth")

    if provider is not None and "google" in getattr(provider, "key", ""):
        params["vertex_project"] = provider.vertex_project or os.getenv("GOOGLE_CLOUD_PROJECT", "browser-use-458714")
        params["vertex_location"] = provider.vertex_location or os.getenv("GOOGLE_CLOUD_LOCATION", "us-east4")

    if "api_key" not in params and not api_base:
        return None

    if provider is not None and getattr(provider, "key", "") == "openrouter":
        headers = get_attribution_headers()
        if headers:
            params["extra_headers"] = headers

    return params


def resolve_endpoint_model_and_params(endpoint):
    """Validate an endpoint and return (model_name, params) or None if ineligible."""
    if endpoint is None or not getattr(endpoint, "enabled", False):
        return None

    provider = getattr(endpoint, "provider", None)
    if provider is not None and not getattr(provider, "enabled", True):
        return None

    model_name = normalize_model_name(provider, endpoint.litellm_model, api_base=endpoint.api_base)
    if not model_name:
        return None

    params = build_endpoint_params(endpoint, provider)
    if params is None:
        return None

    return model_name, params
