"""Utilities for normalizing LLM model identifiers."""
from __future__ import annotations

from typing import Optional

from api.models import LLMProvider

OPENAI_BACKENDS = {
    LLMProvider.BrowserBackend.OPENAI,
    LLMProvider.BrowserBackend.OPENAI_COMPAT,
}


def normalize_model_name(
    provider: Optional[LLMProvider],
    raw_model: str,
    *,
    api_base: str | None = None,
) -> str:
    """Return the model identifier with any required provider prefixes applied.

    When ``api_base`` is provided for OpenAI-compatible providers, ensure LiteLLM's
    ``openai/`` prefix is prepended so proxy deployments behave like the
    first-party API. Providers can also define ``model_prefix`` to always prepend
    a vendor-specific namespace (e.g., ``openrouter/``).
    """

    model = (raw_model or "").strip()
    if not model:
        return model

    if provider is not None:
        # Only apply the provider's static prefix (e.g. "openrouter/") if we
        # are NOT using a custom base URL. When a custom base is present, we
        # assume the caller is targeting that specific endpoint directly and
        # wants to send the raw model ID (or handles routing differently).
        if not api_base:
            prefix = (provider.model_prefix or "").strip()
            if prefix and not model.startswith(prefix):
                model = f"{prefix}{model}"

    backend = getattr(provider, "browser_backend", None)
    if api_base and backend in OPENAI_BACKENDS and "/" not in model:
        model = f"openai/{model}"

    return model


__all__ = ["normalize_model_name"]
