import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

from django.db import OperationalError, ProgrammingError
from django.db.models import Prefetch, Q

from api.encryption import SecretsEncryption
from api.llm.utils import normalize_model_name
from api.models import (
    ImageGenerationLLMTier,
    ImageGenerationTierEndpoint,
    SystemSetting,
)
from api.openrouter import get_attribution_headers

logger = logging.getLogger(__name__)


@dataclass
class ImageGenerationLLMConfig:
    model: str
    params: Dict[str, Any]
    endpoint_key: str
    supports_image_config: bool
    supports_image_to_image: bool


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
                "Failed to decrypt image generation API key for provider %s: %s",
                getattr(provider, "key", "unknown"),
                exc,
            )
            return None

    if not api_key:
        env_var = getattr(provider, "env_var_name", None)
        if env_var:
            api_key = os.getenv(env_var)

    return api_key or None


def _supports_image_config(model_name: str, provider_key: str | None) -> bool:
    lower_model = (model_name or "").lower()
    lower_provider = (provider_key or "").lower()
    return "gemini" in lower_model or "google" in lower_provider


def _setting_key_for_usage(usage: str | None) -> str | None:
    if usage == "create_image":
        return "CREATE_IMAGE_IMAGE_GENERATION_ENDPOINT_KEYS"
    if usage == "avatar":
        return "AGENT_AVATAR_IMAGE_GENERATION_ENDPOINT_KEYS"
    return None


def _parse_endpoint_keys(raw_value: Any) -> list[str]:
    if raw_value is None:
        return []

    if isinstance(raw_value, (list, tuple, set)):
        values = raw_value
    elif isinstance(raw_value, str):
        text = raw_value.strip()
        if not text:
            return []

        if text.startswith("["):
            try:
                decoded = json.loads(text)
            except json.JSONDecodeError:
                decoded = None
            if isinstance(decoded, list):
                values = decoded
            else:
                values = [item.strip() for item in text.split(",")]
        else:
            values = [item.strip() for item in text.split(",")]
    else:
        return []

    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value).strip()
        if not item or item in seen:
            continue
        seen.add(item)
        cleaned.append(item)
    return cleaned


def get_configured_image_generation_endpoint_keys(usage: str | None) -> list[str]:
    setting_key = _setting_key_for_usage(usage)
    if not setting_key:
        return []

    try:
        override = (
            SystemSetting.objects.filter(key=setting_key)
            .values_list("value_text", flat=True)
            .first()
        )
    except (OperationalError, ProgrammingError):
        override = None

    if override:
        return _parse_endpoint_keys(override)

    return []


def _configured_endpoint_keys_for_usage(usage: str | None) -> set[str] | None:
    cleaned = set(get_configured_image_generation_endpoint_keys(usage))
    if not cleaned:
        return None
    return cleaned


def is_image_generation_configured(usage: str | None = None) -> bool:
    """Return True when at least one enabled image-generation tier endpoint exists."""
    configured_keys = _configured_endpoint_keys_for_usage(usage)

    query = ImageGenerationTierEndpoint.objects.filter(
        endpoint__enabled=True,
    ).filter(
        Q(endpoint__provider__isnull=True) | Q(endpoint__provider__enabled=True)
    )
    if configured_keys is not None:
        query = query.filter(endpoint__key__in=configured_keys)
    return query.exists()


def get_image_generation_llm_configs(limit: int = 5, usage: str | None = None) -> list[ImageGenerationLLMConfig]:
    tier_prefetch = Prefetch(
        "tier_endpoints",
        queryset=ImageGenerationTierEndpoint.objects.select_related("endpoint__provider").order_by("-weight"),
    )
    tiers = ImageGenerationLLMTier.objects.prefetch_related(tier_prefetch).order_by("order")
    configured_keys = _configured_endpoint_keys_for_usage(usage)

    configs: list[ImageGenerationLLMConfig] = []
    for tier in tiers:
        for entry in tier.tier_endpoints.all():
            if entry.weight <= 0:
                continue
            endpoint = entry.endpoint
            if endpoint is None or not getattr(endpoint, "enabled", False):
                continue
            if configured_keys is not None and endpoint.key not in configured_keys:
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

            configs.append(
                ImageGenerationLLMConfig(
                    model=model_name,
                    params=params,
                    endpoint_key=getattr(endpoint, "key", ""),
                    supports_image_config=_supports_image_config(model_name, getattr(provider, "key", None)),
                    supports_image_to_image=bool(getattr(endpoint, "supports_image_to_image", False)),
                )
            )
            if len(configs) >= limit:
                return configs

    return configs
