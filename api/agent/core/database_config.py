import logging
import os
from dataclasses import dataclass
from typing import Any

from django.db.models import Prefetch

from api.encryption import SecretsEncryption
from api.llm.utils import normalize_model_name
from api.models import DatabaseLLMTier, DatabaseTierEndpoint
from api.openrouter import get_attribution_headers

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DatabaseTranslationConfig:
    endpoint_id: str
    endpoint_key: str
    provider_key: str | None
    model: str
    params: dict[str, Any]
    tier_order: int
    weight: float


def _resolve_provider_api_key(provider) -> str | None:
    if provider is None or not getattr(provider, "enabled", True):
        return None

    api_key: str | None = None
    encrypted = getattr(provider, "api_key_encrypted", None)
    if encrypted:
        try:
            api_key = SecretsEncryption.decrypt_value(encrypted)
        except Exception as exc:
            logger.warning(
                "Failed to decrypt database translation API key for provider %s: %s",
                getattr(provider, "key", "unknown"),
                exc,
            )
            return None

    if not api_key:
        env_var = getattr(provider, "env_var_name", None)
        if env_var:
            api_key = os.getenv(env_var)

    return api_key or None


def get_database_translation_configs(limit: int | None = None) -> list[DatabaseTranslationConfig]:
    tier_prefetch = Prefetch(
        "tier_endpoints",
        queryset=DatabaseTierEndpoint.objects.select_related("endpoint__provider").order_by("-weight"),
    )
    tiers = DatabaseLLMTier.objects.prefetch_related(tier_prefetch).order_by("order")

    configs: list[DatabaseTranslationConfig] = []
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

            params: dict[str, Any] = {}
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

            configs.append(
                DatabaseTranslationConfig(
                    endpoint_id=str(endpoint.id),
                    endpoint_key=getattr(endpoint, "key", ""),
                    provider_key=getattr(provider, "key", None),
                    model=model_name,
                    params=params,
                    tier_order=tier.order,
                    weight=float(entry.weight),
                )
            )
            if limit is not None and len(configs) >= limit:
                return configs

    return configs

