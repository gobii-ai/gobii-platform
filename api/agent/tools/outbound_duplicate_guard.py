"""
Helpers for preventing duplicate outbound agent messages.

This module centralises the logic used by individual communication tools
to detect recent duplicate sends before persisting a new message.
"""

import logging
import math
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional, Sequence
from uuid import UUID

from django.db.models import Prefetch

from ...encryption import SecretsEncryption
from ...llm.utils import normalize_model_name
from ...models import (
    EmbeddingsLLMTier,
    EmbeddingsTierEndpoint,
    PersistentAgent,
    PersistentAgentMessage,
)

import litellm

logger = logging.getLogger(__name__)

DEFAULT_SIMILARITY_THRESHOLD = 0.97


@dataclass
class DuplicateDetectionResult:
    """Outcome of a duplicate detection check."""

    reason: str
    previous_message: PersistentAgentMessage
    similarity: Optional[float] = None

    def to_error_response(self) -> Dict[str, Any]:
        """Return a serializable payload explaining the duplicate rejection."""
        if self.reason == "exact":
            detail = "matches"
            instruction = "Do not attempt to send this message again."
        else:
            detail = "is highly similar to"
            instruction = "Consider if this message should have been sent at all. If so, please revise before sending again."
        message = (
            f"Message blocked: content {detail} the previous message and may be a duplicate. "
            f"{instruction}"
        )
        payload: Dict[str, Any] = {
            "status": "error",
            "message": message,
            "duplicate_detected": True,
            "duplicate_reason": self.reason,
            "auto_sleep_ok": True
        }
        when = self.previous_message.timestamp
        if when:
            payload["duplicate_timestamp"] = when.isoformat()
        if self.similarity is not None:
            payload["duplicate_similarity"] = self.similarity
        return payload


def _compute_levenshtein_ratio(left: str, right: str) -> float:
    """Return the classic Levenshtein ratio based on edit distance."""
    if left == right:
        return 1.0
    if not left or not right:
        return 0.0

    rows = len(left) + 1
    cols = len(right) + 1
    previous_row = list(range(cols))
    for i in range(1, rows):
        current_row = [i]
        for j in range(1, cols):
            cost = 0 if left[i - 1] == right[j - 1] else 1
            insertions = previous_row[j] + 1
            deletions = current_row[j - 1] + 1
            substitutions = previous_row[j - 1] + cost
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row

    distance = previous_row[-1]
    total_length = len(left) + len(right)
    if total_length == 0:
        return 1.0
    return (total_length - distance) / total_length


def _cosine_from_dense(vec_a: Sequence[float], vec_b: Sequence[float]) -> float:
    if len(vec_a) != len(vec_b):
        raise ValueError(f"Embedding length mismatch ({len(vec_a)} vs {len(vec_b)})")
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


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


def _resolve_provider_api_key(provider) -> Optional[str]:
    if provider is None:
        return None
    if not getattr(provider, "enabled", True):
        return None

    api_key: Optional[str] = None

    if getattr(provider, "api_key_encrypted", None):
        try:
            api_key = SecretsEncryption.decrypt_value(provider.api_key_encrypted)
        except Exception as exc:  # pragma: no cover - depends on env configuration
            logger.warning(
                "Failed to decrypt embeddings API key for provider %s: %s",
                getattr(provider, "key", "unknown"),
                exc,
            )
            return None # Return None in case of decryption failure

    if not api_key and getattr(provider, "env_var_name", None):
        env_val = os.getenv(provider.env_var_name)
        if env_val:
            api_key = env_val

    return api_key or None


def _score_embeddings_for_endpoint(
    tier: EmbeddingsLLMTier,
    endpoint,
    left: str,
    right: str,
) -> Optional[float]:
    if endpoint is None or not getattr(endpoint, "enabled", False):
        return None

    provider = getattr(endpoint, "provider", None)
    if provider is not None and not getattr(provider, "enabled", True):
        return None

    if litellm is None:
        return None

    raw_model = getattr(endpoint, "litellm_model", "").strip()
    model_name = normalize_model_name(provider, raw_model, api_base=getattr(endpoint, "api_base", None))
    if not model_name:
        return None

    params: Dict[str, Any] = {}
    api_key = _resolve_provider_api_key(provider)
    if api_key:
        params["api_key"] = api_key

    api_base = getattr(endpoint, "api_base", "").strip()
    if api_base:
        params["api_base"] = api_base
        params["api_key"] = "sk-noauth" # Set api_key only if api_base is present

    if provider is not None and "google" in getattr(provider, "key", ""):
        project = provider.vertex_project or os.getenv("GOOGLE_CLOUD_PROJECT", "browser-use-458714")
        location = provider.vertex_location or os.getenv("GOOGLE_CLOUD_LOCATION", "us-east4")
        params["vertex_project"] = project
        params["vertex_location"] = location

    if "api_key" not in params and not api_base:
        # No credentials available; skip this tier.
        return None

    try:
        response = litellm.embedding(model=model_name, input=[left, right], **params)
        embeddings = _extract_embeddings(response)
        if len(embeddings) < 2:
            raise ValueError("embedding response missing comparison vectors")
        cosine = _cosine_from_dense(embeddings[0], embeddings[1])
        ratio = (cosine + 1.0) / 2.0
        return max(0.0, min(1.0, ratio))
    except Exception as exc:  # pragma: no cover - depends on external API
        logger.warning(
            "Embeddings tier %s (%s) failed: %s",
            tier.order,
            getattr(endpoint, "key", "unknown"),
            exc,
        )
        return None


def _embedding_similarity(left: str, right: str) -> Optional[float]:
    if litellm is None:
        return None

    tier_prefetch = Prefetch(
        "tier_endpoints",
        queryset=EmbeddingsTierEndpoint.objects.select_related("endpoint__provider").order_by("-weight"),
    )
    tiers = EmbeddingsLLMTier.objects.prefetch_related(tier_prefetch).order_by("order")
    for tier in tiers:
        tier_endpoints = [
            entry for entry in tier.tier_endpoints.all()
            if entry.weight > 0
        ]
        for entry in tier_endpoints:
            ratio = _score_embeddings_for_endpoint(tier, entry.endpoint, left, right)
            if ratio is not None:
                return ratio
    return None


def detect_recent_duplicate_message(
        agent: PersistentAgent,
        *,
        channel: str,
        body: str,
        to_address: Optional[str] = None,
        conversation_id: Optional[UUID] = None,
        similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> Optional[DuplicateDetectionResult]:
    """
    Check whether the pending outbound message is a recent duplicate.

    The inspection operates in two passes:
    1. Exact match comparison on the full payload (subject + body where applicable).
    2. Embeddings-based cosine similarity (with database-configured tiers) if no exact match is found.
       When no embeddings are available, the check falls back to a Levenshtein ratio.
    """
    if not body:
        return None

    qs = PersistentAgentMessage.objects.filter(
        owner_agent=agent,
        is_outbound=True,
        from_endpoint__channel=channel,
    )

    if conversation_id:
        qs = qs.filter(conversation_id=conversation_id)

    if to_address:
        qs = qs.filter(to_endpoint__address=to_address)

    current_body = (body or "").strip()
    previous_message = qs.order_by("-timestamp").first()
    if not previous_message:
        return None

    previous_body = (previous_message.body or "").strip()
    if not previous_body:
        return None

    if previous_body == current_body:
        return DuplicateDetectionResult(reason="exact", previous_message=previous_message)

    similarity = _embedding_similarity(previous_body, current_body)
    if similarity is None:
        similarity = _compute_levenshtein_ratio(previous_body, current_body)

    if similarity >= similarity_threshold:
        return DuplicateDetectionResult(
            reason="similarity", previous_message=previous_message, similarity=similarity
        )

    return None
