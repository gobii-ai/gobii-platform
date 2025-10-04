"""Shared helpers for constructing LiteLLM completion calls."""
from __future__ import annotations

from typing import Any, Iterable

import litellm


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

    - Removes internal hints (``supports_tool_choice``, ``use_parallel_tool_calls``, and ``supports_vision``).
    - Adds ``tool_choice`` when tools are provided and supported.
    - Propagates ``parallel_tool_calls`` when tools are provided *or* the endpoint
      supplied an explicit hint.
    - Allows callers to control ``drop_params`` while keeping consistent defaults.
    """
    params = dict(params or {})

    tool_choice_supported = params.pop("supports_tool_choice", True)
    parallel_hint_provided = "use_parallel_tool_calls" in params
    use_parallel_tool_calls = params.pop("use_parallel_tool_calls", True)
    params.pop("supports_vision", None)

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": list(messages),
        **params,
        **extra_kwargs,
    }

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

    return litellm.completion(**kwargs)


__all__ = ["run_completion"]
