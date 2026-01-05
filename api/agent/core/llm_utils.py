"""Shared helpers for constructing LiteLLM completion calls."""
from __future__ import annotations

import logging
import time
from typing import Any, Iterable

from django.conf import settings
import litellm

_HINT_KEYS = (
    "supports_temperature",
    "supports_tool_choice",
    "use_parallel_tool_calls",
    "supports_vision",
    "supports_reasoning",
    "reasoning_effort",
    "low_latency",
)

logger = logging.getLogger(__name__)

_RETRYABLE_ERRORS = (
    litellm.Timeout,
    litellm.APIConnectionError,
    litellm.ServiceUnavailableError,
    litellm.RateLimitError,
)


def run_completion(
    *,
    model: str,
    messages: Iterable[dict[str, Any]],
    params: dict[str, Any],
    tools: list[dict[str, Any]] | None = None,
    drop_params: bool = False,
    **extra_kwargs: Any,
):
    """Invoke ``litellm.completion`` with shared parameter handling.

    - Removes internal hints (``supports_temperature``, ``supports_tool_choice``, ``use_parallel_tool_calls``, ``supports_vision``, and ``supports_reasoning``).
    - Adds ``tool_choice`` when tools are provided and supported.
    - Propagates ``parallel_tool_calls`` when tools are provided *or* the endpoint
      supplied an explicit hint.
    - Allows callers to control ``drop_params`` while keeping consistent defaults.
    """
    params = dict(params or {})

    parallel_hint_provided = "use_parallel_tool_calls" in params
    hints: dict[str, Any] = {key: params.pop(key, None) for key in _HINT_KEYS}

    supports_temperature_hint = hints.get("supports_temperature")
    supports_temperature = True if supports_temperature_hint is None else supports_temperature_hint
    if not supports_temperature:
        params.pop("temperature", None)

    tool_choice_hint = hints.get("supports_tool_choice")
    tool_choice_supported = True if tool_choice_hint is None else tool_choice_hint

    parallel_hint = hints.get("use_parallel_tool_calls")
    use_parallel_tool_calls = True if parallel_hint is None else parallel_hint

    supports_reasoning_hint = hints.get("supports_reasoning")
    supports_reasoning = False if supports_reasoning_hint is None else supports_reasoning_hint
    reasoning_effort = hints.get("reasoning_effort", None)

    extra_reasoning_effort = extra_kwargs.get("reasoning_effort")
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": list(messages),
        **params,
        **extra_kwargs,
    }

    kwargs.pop("reasoning_effort", None)
    if supports_reasoning:
        selected_reasoning_effort = extra_reasoning_effort or reasoning_effort
        if selected_reasoning_effort:
            kwargs["reasoning_effort"] = selected_reasoning_effort

    if drop_params:
        kwargs["drop_params"] = True

    if tools:
        kwargs["tools"] = tools
        if tool_choice_supported:
            kwargs.setdefault("tool_choice", "auto")
    else:
        # Ensure we don't pass tool-choice hints when tools are absent
        kwargs.pop("tool_choice", None)

    if use_parallel_tool_calls is not None and (tools or parallel_hint_provided):
        # Respect explicit hints even when no tools are provided; some providers
        # validate the flag independently of tool availability.
        kwargs["parallel_tool_calls"] = bool(use_parallel_tool_calls)
    else:
        kwargs.pop("parallel_tool_calls", None)

    if kwargs.get("timeout") is None:
        kwargs["timeout"] = getattr(settings, "LITELLM_TIMEOUT_SECONDS", 300)

    max_attempts = max(1, int(getattr(settings, "LITELLM_MAX_RETRIES", 2)))
    backoff_seconds = float(getattr(settings, "LITELLM_RETRY_BACKOFF_SECONDS", 1.0))

    for attempt in range(1, max_attempts + 1):
        try:
            return litellm.completion(**kwargs)
        except _RETRYABLE_ERRORS as exc:
            if attempt >= max_attempts:
                raise
            logger.warning(
                "LiteLLM request failed with %s; retrying (%d/%d)",
                type(exc).__name__,
                attempt,
                max_attempts,
            )
            if backoff_seconds > 0:
                time.sleep(backoff_seconds * (2 ** (attempt - 1)))


__all__ = ["run_completion"]
