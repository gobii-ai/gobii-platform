import logging
from dataclasses import dataclass
from typing import Any, Dict

from django.db.models import Prefetch, Q

from api.agent.core.endpoint_config_utils import resolve_endpoint_model_and_params
from api.models import (
    ImageGenerationLLMTier,
    ImageGenerationTierEndpoint,
)

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
            result = resolve_endpoint_model_and_params(entry.endpoint)
            if result is None:
                continue
            model_name, params = result
            provider = getattr(entry.endpoint, "provider", None)

            configs.append(
                ImageGenerationLLMConfig(
                    model=model_name,
                    params=params,
                    endpoint_key=getattr(entry.endpoint, "key", ""),
                    supports_image_config=_supports_image_config(model_name, getattr(provider, "key", None)),
                    supports_image_to_image=bool(getattr(entry.endpoint, "supports_image_to_image", False)),
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
