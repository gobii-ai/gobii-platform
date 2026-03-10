import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

from django.db.models import Prefetch, Q

from api.encryption import SecretsEncryption
from api.llm.utils import normalize_model_name
from api.models import (
    ImageGenerationLLMTier,
    ImageGenerationTierEndpoint,
)
from api.openrouter import get_attribution_headers

logger = logging.getLogger(__name__)

CREATE_IMAGE_USE_CASE = ImageGenerationLLMTier.UseCase.CREATE_IMAGE
AVATAR_IMAGE_USE_CASE = ImageGenerationLLMTier.UseCase.AVATAR
AVATAR_IMAGE_FALLBACK_USE_CASES = (CREATE_IMAGE_USE_CASE,)


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


def _build_eligible_tier_endpoint_queryset(use_case: str):
    return ImageGenerationTierEndpoint.objects.filter(
        tier__use_case=use_case,
        endpoint__enabled=True,
    ).filter(
        Q(endpoint__provider__isnull=True) | Q(endpoint__provider__enabled=True)
    )


def _iter_candidate_use_cases(
    use_case: str,
    fallback_use_cases: tuple[str, ...] | list[str] | None = None,
) -> list[str]:
    candidates = [use_case]
    if fallback_use_cases:
        for candidate in fallback_use_cases:
            if candidate and candidate not in candidates:
                candidates.append(candidate)
    return candidates


def _resolve_image_generation_tiers(
    use_case: str,
    fallback_use_cases: tuple[str, ...] | list[str] | None = None,
):
    tier_prefetch = Prefetch(
        "tier_endpoints",
        queryset=ImageGenerationTierEndpoint.objects.select_related("endpoint__provider").order_by("-weight"),
    )
    for candidate in _iter_candidate_use_cases(use_case, fallback_use_cases):
        if not _build_eligible_tier_endpoint_queryset(candidate).exists():
            continue
        return (
            ImageGenerationLLMTier.objects.filter(use_case=candidate)
            .prefetch_related(tier_prefetch)
            .order_by("order")
        )
    return None


def is_image_generation_configured(
    *,
    use_case: str = CREATE_IMAGE_USE_CASE,
    fallback_use_cases: tuple[str, ...] | list[str] | None = None,
) -> bool:
    """Return True when the requested image-generation workflow has at least one eligible tier endpoint."""
    return _resolve_image_generation_tiers(use_case, fallback_use_cases) is not None


def get_image_generation_llm_configs(
    *,
    use_case: str = CREATE_IMAGE_USE_CASE,
    fallback_use_cases: tuple[str, ...] | list[str] | None = None,
    limit: int = 5,
) -> list[ImageGenerationLLMConfig]:
    tiers = _resolve_image_generation_tiers(use_case, fallback_use_cases)
    if tiers is None:
        return []

    configs: list[ImageGenerationLLMConfig] = []
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


def is_create_image_generation_configured() -> bool:
    return is_image_generation_configured(use_case=CREATE_IMAGE_USE_CASE)


def get_create_image_generation_llm_configs(limit: int = 5) -> list[ImageGenerationLLMConfig]:
    return get_image_generation_llm_configs(use_case=CREATE_IMAGE_USE_CASE, limit=limit)


def is_avatar_image_generation_configured() -> bool:
    return is_image_generation_configured(
        use_case=AVATAR_IMAGE_USE_CASE,
        fallback_use_cases=AVATAR_IMAGE_FALLBACK_USE_CASES,
    )


def get_avatar_image_generation_llm_configs(limit: int = 5) -> list[ImageGenerationLLMConfig]:
    return get_image_generation_llm_configs(
        use_case=AVATAR_IMAGE_USE_CASE,
        fallback_use_cases=AVATAR_IMAGE_FALLBACK_USE_CASES,
        limit=limit,
    )
