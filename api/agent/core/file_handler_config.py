import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

from django.db.models import Prefetch

from api.encryption import SecretsEncryption
from api.llm.utils import normalize_model_name
from api.models import FileHandlerLLMTier, FileHandlerTierEndpoint
from api.openrouter import get_attribution_headers

logger = logging.getLogger(__name__)


@dataclass
class FileHandlerLLMConfig:
    model: str
    params: Dict[str, Any]
    supports_vision: bool
    endpoint_key: str


def _resolve_provider_api_key(provider) -> Optional[str]:
    if provider is None or not getattr(provider, "enabled", True):
        return None

    api_key: Optional[str] = None
    encrypted = getattr(provider, "api_key_encrypted", None)
    if encrypted:
        try:
            api_key = SecretsEncryption.decrypt_value(encrypted)
        except Exception as exc:
            logger.warning(
                "Failed to decrypt file handler API key for provider %s: %s",
                getattr(provider, "key", "unknown"),
                exc,
            )
            return None

    if not api_key:
        env_var = getattr(provider, "env_var_name", None)
        if env_var:
            api_key = os.getenv(env_var)

    return api_key or None


def get_file_handler_llm_config() -> Optional[FileHandlerLLMConfig]:
    tier_prefetch = Prefetch(
        "tier_endpoints",
        queryset=FileHandlerTierEndpoint.objects.select_related("endpoint__provider").order_by("-weight"),
    )
    tiers = FileHandlerLLMTier.objects.prefetch_related(tier_prefetch).order_by("order")

    for tier in tiers:
        for entry in tier.tier_endpoints.all():
            if entry.weight <= 0:
                continue
            endpoint = entry.endpoint
            if endpoint is None or not getattr(endpoint, "enabled", False):
                continue

            provider = getattr(endpoint, "provider", None)
            if provider is not None and not getattr(provider, "enabled", True):
                continue

            model_name = normalize_model_name(provider, endpoint.litellm_model, api_base=endpoint.api_base)
            if not model_name:
                continue

            params: Dict[str, Any] = {}
            api_key = _resolve_provider_api_key(provider)
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
                continue

            if provider is not None and getattr(provider, "key", "") == "openrouter":
                headers = get_attribution_headers()
                if headers:
                    params["extra_headers"] = headers

            return FileHandlerLLMConfig(
                model=model_name,
                params=params,
                supports_vision=bool(getattr(endpoint, "supports_vision", False)),
                endpoint_key=getattr(endpoint, "key", ""),
            )

    return None
