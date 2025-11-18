"""Serialization helpers for the console LLM configuration UI."""

from __future__ import annotations

import os
from typing import Any

from django.db.models import Prefetch

from api.models import (
    BrowserLLMPolicy,
    BrowserLLMTier,
    BrowserTierEndpoint,
    BrowserModelEndpoint,
    EmbeddingsLLMTier,
    EmbeddingsTierEndpoint,
    EmbeddingsModelEndpoint,
    LLMProvider,
    PersistentLLMTier,
    PersistentTierEndpoint,
    PersistentModelEndpoint,
    PersistentTokenRange,
)


def _provider_key_status(provider: LLMProvider) -> str:
    has_admin_key = bool(provider.api_key_encrypted)
    has_env = bool(provider.env_var_name and os.getenv(provider.env_var_name))
    if not (has_admin_key or has_env):
        return "Missing key"
    if has_admin_key:
        return "Admin key"
    return "Env var"


def _serialize_endpoint_common(endpoint, *, label: str) -> dict[str, Any]:
    return {
        "id": str(endpoint.id),
        "label": label,
        "enabled": bool(endpoint.enabled),
    }


def _serialize_persistent_endpoint(endpoint: PersistentModelEndpoint) -> dict[str, Any]:
    label = f"{endpoint.provider.display_name} · {endpoint.litellm_model}"
    data = _serialize_endpoint_common(endpoint, label=label)
    data.update(
        {
            "key": endpoint.key,
            "model": endpoint.litellm_model,
            "temperature_override": endpoint.temperature_override,
            "supports_tool_choice": endpoint.supports_tool_choice,
            "use_parallel_tool_calls": endpoint.use_parallel_tool_calls,
            "supports_vision": endpoint.supports_vision,
            "api_base": endpoint.api_base,
            "provider_id": str(endpoint.provider_id),
            "type": "persistent",
        }
    )
    return data


def _serialize_browser_endpoint(endpoint: BrowserModelEndpoint) -> dict[str, Any]:
    label = f"{endpoint.provider.display_name} · {endpoint.browser_model}"
    data = _serialize_endpoint_common(endpoint, label=label)
    data.update(
        {
            "key": endpoint.key,
            "model": endpoint.browser_model,
            "browser_base_url": endpoint.browser_base_url,
            "supports_vision": endpoint.supports_vision,
            "max_output_tokens": endpoint.max_output_tokens,
            "provider_id": str(endpoint.provider_id),
            "type": "browser",
        }
    )
    return data


def _serialize_embedding_endpoint(endpoint: EmbeddingsModelEndpoint) -> dict[str, Any]:
    label = f"{endpoint.provider.display_name if endpoint.provider else 'Unlinked'} · {endpoint.litellm_model}"
    data = _serialize_endpoint_common(endpoint, label=label)
    data.update(
        {
            "key": endpoint.key,
            "model": endpoint.litellm_model,
            "api_base": endpoint.api_base,
            "provider_id": str(endpoint.provider_id) if endpoint.provider_id else None,
            "type": "embedding",
        }
    )
    return data


def build_llm_overview() -> dict[str, Any]:
    providers = (
        LLMProvider.objects.all()
        .order_by("display_name")
        .prefetch_related(
            Prefetch("persistent_endpoints", queryset=PersistentModelEndpoint.objects.select_related("provider")),
            Prefetch("browser_endpoints", queryset=BrowserModelEndpoint.objects.select_related("provider")),
            Prefetch("embedding_endpoints", queryset=EmbeddingsModelEndpoint.objects.select_related("provider")),
        )
    )

    provider_payload: list[dict[str, Any]] = []
    persistent_choices: list[dict[str, Any]] = []
    browser_choices: list[dict[str, Any]] = []
    embedding_choices: list[dict[str, Any]] = []

    for provider in providers:
        persistent_endpoints = [
            _serialize_persistent_endpoint(endpoint)
            for endpoint in provider.persistent_endpoints.all()
        ]
        browser_endpoints = [
            _serialize_browser_endpoint(endpoint)
            for endpoint in provider.browser_endpoints.all()
        ]
        embedding_endpoints = [
            _serialize_embedding_endpoint(endpoint)
            for endpoint in provider.embedding_endpoints.all()
        ]

        persistent_choices.extend(persistent_endpoints)
        browser_choices.extend(browser_endpoints)
        embedding_choices.extend(embedding_endpoints)

        provider_payload.append(
            {
                "id": str(provider.id),
                "name": provider.display_name,
                "key": provider.key,
                "enabled": bool(provider.enabled),
                "env_var": provider.env_var_name,
                "browser_backend": provider.browser_backend,
                "supports_safety_identifier": provider.supports_safety_identifier,
                "vertex_project": provider.vertex_project,
                "vertex_location": provider.vertex_location,
                "status": _provider_key_status(provider),
                "endpoints": persistent_endpoints + browser_endpoints + embedding_endpoints,
            }
        )

    persistent_ranges = (
        PersistentTokenRange.objects.order_by("min_tokens")
        .prefetch_related(
            Prefetch(
                "tiers",
                queryset=PersistentLLMTier.objects.order_by("is_premium", "is_max", "order").prefetch_related(
                    Prefetch(
                        "tier_endpoints",
                        queryset=PersistentTierEndpoint.objects.select_related("endpoint__provider").order_by("endpoint__litellm_model"),
                    )
                ),
            )
        )
    )

    persistent_payload: list[dict[str, Any]] = []
    for token_range in persistent_ranges:
        tiers_payload: list[dict[str, Any]] = []
        for tier in token_range.tiers.all():
            tier_endpoints = []
            for te in tier.tier_endpoints.all():
                endpoint = te.endpoint
                tier_endpoints.append(
                    {
                        "id": str(te.id),
                        "endpoint_id": str(endpoint.id),
                        "label": f"{endpoint.provider.display_name} · {endpoint.litellm_model}",
                        "weight": float(te.weight),
                        "endpoint_key": endpoint.key,
                    }
                )
            tiers_payload.append(
                {
                    "id": str(tier.id),
                    "order": tier.order,
                    "description": tier.description,
                    "is_premium": tier.is_premium,
                    "is_max": tier.is_max,
                    "endpoints": tier_endpoints,
                }
            )
        persistent_payload.append(
            {
                "id": str(token_range.id),
                "name": token_range.name,
                "min_tokens": token_range.min_tokens,
                "max_tokens": token_range.max_tokens,
                "tiers": tiers_payload,
            }
        )

    policy = (
        BrowserLLMPolicy.objects.filter(is_active=True)
        .prefetch_related(
            Prefetch(
                "tiers",
                queryset=BrowserLLMTier.objects.order_by("is_premium", "order").prefetch_related(
                    Prefetch(
                        "tier_endpoints",
                        queryset=BrowserTierEndpoint.objects.select_related("endpoint__provider").order_by("endpoint__browser_model"),
                    )
                ),
            )
        )
        .first()
    )

    browser_payload: dict[str, Any] | None = None
    if policy:
        tiers_payload: list[dict[str, Any]] = []
        for tier in policy.tiers.all():
            tier_endpoints = []
            for te in tier.tier_endpoints.all():
                endpoint = te.endpoint
                tier_endpoints.append(
                    {
                        "id": str(te.id),
                        "endpoint_id": str(endpoint.id),
                        "label": f"{endpoint.provider.display_name} · {endpoint.browser_model}",
                        "weight": float(te.weight),
                        "endpoint_key": endpoint.key,
                    }
                )
            tiers_payload.append(
                {
                    "id": str(tier.id),
                    "order": tier.order,
                    "description": tier.description,
                    "is_premium": tier.is_premium,
                    "endpoints": tier_endpoints,
                }
            )
        browser_payload = {
            "id": str(policy.id),
            "name": policy.name,
            "tiers": tiers_payload,
        }

    embedding_payload: list[dict[str, Any]] = []
    embedding_tiers = EmbeddingsLLMTier.objects.prefetch_related(
        Prefetch(
            "tier_endpoints",
            queryset=EmbeddingsTierEndpoint.objects.select_related("endpoint__provider").order_by("-weight"),
        )
    ).order_by("order")
    for tier in embedding_tiers:
        tier_endpoints = []
        for te in tier.tier_endpoints.all():
            endpoint = te.endpoint
            label_provider = endpoint.provider.display_name if endpoint.provider else "Unlinked"
            tier_endpoints.append(
                {
                    "id": str(te.id),
                    "endpoint_id": str(endpoint.id),
                    "label": f"{label_provider} · {endpoint.litellm_model}",
                    "weight": float(te.weight),
                    "endpoint_key": endpoint.key,
                }
            )
        embedding_payload.append(
            {
                "id": str(tier.id),
                "order": tier.order,
                "description": tier.description,
                "endpoints": tier_endpoints,
            }
        )

    stats = {
        "active_providers": LLMProvider.objects.filter(enabled=True).count(),
        "persistent_endpoints": PersistentModelEndpoint.objects.filter(enabled=True).count(),
        "browser_endpoints": BrowserModelEndpoint.objects.filter(enabled=True).count(),
        "premium_persistent_tiers": PersistentLLMTier.objects.filter(is_premium=True).count(),
    }

    return {
        "stats": stats,
        "providers": provider_payload,
        "persistent": {"ranges": persistent_payload},
        "browser": browser_payload,
        "embeddings": {"tiers": embedding_payload},
        "choices": {
            "persistent_endpoints": persistent_choices,
            "browser_endpoints": browser_choices,
            "embedding_endpoints": embedding_choices,
        },
    }


__all__ = ["build_llm_overview"]
