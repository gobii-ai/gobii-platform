import logging
import os
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

import litellm
from django.db.models import Prefetch

from api.encryption import SecretsEncryption
from api.evals.execution import get_current_eval_routing_profile
from api.llm.utils import normalize_model_name
from api.models import (
    EmbeddingsLLMTier,
    EmbeddingsTierEndpoint,
    LLMRoutingProfile,
    ProfileEmbeddingsTier,
    ProfileEmbeddingsTierEndpoint,
)

logger = logging.getLogger(__name__)

LITELLM_EMBEDDING_ERRORS = (
    litellm.exceptions.APIConnectionError,
    litellm.exceptions.APIError,
    litellm.exceptions.APIResponseValidationError,
    litellm.exceptions.AuthenticationError,
    litellm.exceptions.BadRequestError,
    litellm.exceptions.InvalidRequestError,
    litellm.exceptions.NotFoundError,
    litellm.exceptions.PermissionDeniedError,
    litellm.exceptions.RateLimitError,
    litellm.exceptions.ServiceUnavailableError,
    litellm.exceptions.UnsupportedParamsError,
)


@dataclass(frozen=True)
class EmbeddingResult:
    vectors: list[list[float]]
    model: str

    @property
    def vector(self) -> list[float]:
        return self.vectors[0] if self.vectors else []

    @property
    def dimension(self) -> int:
        return len(self.vector)


def generate_embeddings(inputs: Sequence[str], *, routing_profile: Any = None) -> EmbeddingResult | None:
    clean_inputs = [str(value or "").strip() for value in inputs]
    if not clean_inputs or any(not value for value in clean_inputs):
        return None

    for endpoint in _iter_embedding_endpoints(routing_profile=routing_profile):
        result = _generate_embeddings_for_endpoint(endpoint, clean_inputs)
        if result is not None:
            return result
    return None


def _iter_embedding_endpoints(*, routing_profile: Any = None) -> Iterable[Any]:
    profile = routing_profile
    if profile is None:
        profile = get_current_eval_routing_profile()
    if profile is None:
        profile = LLMRoutingProfile.objects.filter(is_active=True).first()
    if profile is not None:
        tier_prefetch = Prefetch(
            "tier_endpoints",
            queryset=ProfileEmbeddingsTierEndpoint.objects.select_related("endpoint__provider").order_by("-weight"),
        )
        tiers = ProfileEmbeddingsTier.objects.filter(profile=profile).prefetch_related(tier_prefetch).order_by("order")
        for tier in tiers:
            for entry in tier.tier_endpoints.all():
                if entry.weight > 0:
                    yield entry.endpoint

    tier_prefetch = Prefetch(
        "tier_endpoints",
        queryset=EmbeddingsTierEndpoint.objects.select_related("endpoint__provider").order_by("-weight"),
    )
    tiers = EmbeddingsLLMTier.objects.prefetch_related(tier_prefetch).order_by("order")
    for tier in tiers:
        for entry in tier.tier_endpoints.all():
            if entry.weight > 0:
                yield entry.endpoint


def _generate_embeddings_for_endpoint(endpoint: Any, inputs: Sequence[str]) -> EmbeddingResult | None:
    if endpoint is None or not getattr(endpoint, "enabled", False):
        return None
    provider = getattr(endpoint, "provider", None)
    if provider is not None and not getattr(provider, "enabled", True):
        return None

    raw_model = str(getattr(endpoint, "litellm_model", "") or "").strip()
    model_name = normalize_model_name(provider, raw_model, api_base=getattr(endpoint, "api_base", None))
    if not model_name:
        return None

    params: dict[str, Any] = {}
    api_key = _resolve_provider_api_key(provider)
    api_base = str(getattr(endpoint, "api_base", "") or "").strip()
    if api_key:
        params["api_key"] = api_key
    if api_base:
        params["api_base"] = api_base
        params.setdefault("api_key", "sk-noauth")
    _apply_provider_overrides(provider, params)
    if "api_key" not in params and not api_base:
        return None

    try:
        response = litellm.embedding(model=model_name, input=list(inputs), **params)
        vectors = _extract_embeddings(response)
    except LITELLM_EMBEDDING_ERRORS as exc:
        logger.warning("Embedding endpoint %s failed: %s", getattr(endpoint, "key", model_name), exc)
        return None
    except (TypeError, ValueError) as exc:
        logger.warning("Embedding endpoint %s returned invalid data: %s", getattr(endpoint, "key", model_name), exc)
        return None
    if len(vectors) != len(inputs):
        logger.warning("Embedding endpoint %s returned %s vectors for %s inputs", getattr(endpoint, "key", model_name), len(vectors), len(inputs))
        return None
    return EmbeddingResult(vectors=vectors, model=model_name)


def _extract_embeddings(response: Any) -> list[list[float]]:
    data = getattr(response, "data", None)
    if data is None and isinstance(response, dict):
        data = response.get("data")
    if not data:
        raise ValueError("embedding response missing data")

    embeddings: list[list[float]] = []
    for entry in data:
        embedding = getattr(entry, "embedding", None)
        if embedding is None and isinstance(entry, dict):
            embedding = entry.get("embedding")
        if embedding is None:
            raise ValueError("embedding response missing embedding vector")
        embeddings.append([float(value) for value in embedding])
    return embeddings


def _resolve_provider_api_key(provider: Any) -> str | None:
    if provider is None or not getattr(provider, "enabled", True):
        return None
    api_key = None
    if getattr(provider, "api_key_encrypted", None):
        try:
            api_key = SecretsEncryption.decrypt_value(provider.api_key_encrypted)
        except ValueError as exc:
            logger.warning("Failed to decrypt embeddings API key for provider %s: %s", getattr(provider, "key", ""), exc)
            return None
    if not api_key and getattr(provider, "env_var_name", None):
        api_key = os.getenv(provider.env_var_name)
    return api_key or None


def _apply_provider_overrides(provider: Any, params: dict[str, Any]) -> None:
    if provider is not None and "google" in str(getattr(provider, "key", "")):
        params["vertex_project"] = provider.vertex_project or os.getenv("GOOGLE_CLOUD_PROJECT", "browser-use-458714")
        params["vertex_location"] = provider.vertex_location or os.getenv("GOOGLE_CLOUD_LOCATION", "us-east4")
