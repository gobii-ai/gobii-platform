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

    - Removes internal hints (``supports_tool_choice`` and ``use_parallel_tool_calls``).
    - Only adds ``tool_choice`` / ``parallel_tool_calls`` when tools are provided.
    - Allows callers to control ``drop_params`` while keeping consistent defaults.
    """
    params = dict(params or {})

    tool_choice_supported = params.pop("supports_tool_choice", True)
    use_parallel_tool_calls = params.pop("use_parallel_tool_calls", True)

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
        if use_parallel_tool_calls is not None:
            kwargs["parallel_tool_calls"] = bool(use_parallel_tool_calls)
    else:
        # Ensure we don't pass tool-related kwargs when tools are absent
        kwargs.pop("tool_choice", None)
        kwargs.pop("parallel_tool_calls", None)

    return litellm.completion(**kwargs)


__all__ = ["run_completion"]
