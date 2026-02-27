"""Shared helpers for constructing LiteLLM completion calls."""
import json
import logging
import time
from typing import Any, Iterable

from django.conf import settings
import litellm

from api.services.system_settings import get_litellm_timeout_seconds
from .token_usage import extract_reasoning_content
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


class LiteLLMResponseError(RuntimeError):
    """Base class for LiteLLM response validation errors."""

    def __init__(self, message: str, *, model: str | None = None, provider: str | None = None) -> None:
        details = []
        if provider:
            details.append(f"provider={provider}")
        if model:
            details.append(f"model={model}")
        if details:
            message = f"{message} ({', '.join(details)})"
        super().__init__(message)
        self.model = model
        self.provider = provider


class EmptyLiteLLMResponseError(LiteLLMResponseError):
    """Raised when LiteLLM returns a response without content, reasoning, or tools."""


class InvalidLiteLLMResponseError(LiteLLMResponseError):
    """Raised when LiteLLM returns a response containing forbidden markers."""


_RETRYABLE_ERRORS = (
    litellm.Timeout,
    litellm.APIConnectionError,
    litellm.ServiceUnavailableError,
    litellm.RateLimitError,
    EmptyLiteLLMResponseError,
    InvalidLiteLLMResponseError,
)


def _attach_response_duration(response: Any, duration_ms: int | None) -> None:
    if response is None or duration_ms is None:
        return
    if isinstance(response, dict):
        response["request_duration_ms"] = duration_ms
        return
    try:
        setattr(response, "request_duration_ms", duration_ms)
    except Exception:
        model_extra = getattr(response, "model_extra", None)
        if isinstance(model_extra, dict):
            model_extra["request_duration_ms"] = duration_ms


def _first_message_from_response(response: Any) -> Any:
    if response is None:
        return None
    choices = getattr(response, "choices", None)
    if choices is None and isinstance(response, dict):
        choices = response.get("choices")
    if not choices:
        return None
    first_choice = choices[0]
    if isinstance(first_choice, dict):
        return first_choice.get("message")
    return getattr(first_choice, "message", None)


def _extract_message_content(message: Any) -> str:
    if message is None:
        return ""
    if isinstance(message, dict):
        content = message.get("content")
    else:
        content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
                continue
            if isinstance(part, dict):
                part_type = part.get("type")
                if isinstance(part_type, str) and part_type.lower() in {"reasoning", "thinking"}:
                    continue
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return ""


def _coerce_tool_calls(raw_tool_calls: Any) -> list[Any]:
    if raw_tool_calls is None:
        return []
    if isinstance(raw_tool_calls, str):
        try:
            raw_tool_calls = json.loads(raw_tool_calls)
        except json.JSONDecodeError:
            return [raw_tool_calls]
    if isinstance(raw_tool_calls, dict):
        return [raw_tool_calls]
    if isinstance(raw_tool_calls, list):
        return list(raw_tool_calls)
    try:
        return list(raw_tool_calls)
    except TypeError:
        return [raw_tool_calls]


def _message_has_images(message: Any) -> bool:
    if message is None:
        return False
    images = message.get("images") if isinstance(message, dict) else getattr(message, "images", None)
    if images:
        return True

    if isinstance(message, dict):
        content = message.get("content")
    else:
        content = getattr(message, "content", None)
    if isinstance(content, list):
        for part in content:
            part_type = part.get("type") if isinstance(part, dict) else getattr(part, "type", None)
            if isinstance(part_type, str) and part_type.lower() in {"image_url", "image", "output_image", "input_image"}:
                return True
    return False


def _message_has_tool_calls(message: Any) -> bool:
    if message is None:
        return False
    if isinstance(message, dict):
        raw_tool_calls = message.get("tool_calls")
    else:
        raw_tool_calls = getattr(message, "tool_calls", None)
    tool_calls = _coerce_tool_calls(raw_tool_calls)
    if tool_calls:
        return True
    if isinstance(message, dict):
        function_call = message.get("function_call")
    else:
        function_call = getattr(message, "function_call", None)
    if function_call:
        return True
    if isinstance(message, dict):
        content = message.get("content")
    else:
        content = getattr(message, "content", None)
    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            part_type = part.get("type")
            if isinstance(part_type, str) and part_type.lower() in {"tool_use", "tool_call"}:
                return True
    return False


_FORBIDDEN_COMPLETION_MARKERS = (
    "<\uFF5CDSML\uFF5Cfunction_calls>",
)


def _contains_forbidden_marker(text: str | None) -> bool:
    if not text:
        return False
    return any(marker in text for marker in _FORBIDDEN_COMPLETION_MARKERS)


def _response_has_forbidden_markers(response: Any) -> bool:
    message = _first_message_from_response(response)
    if message is None:
        return False
    content_text = _extract_message_content(message)
    if _contains_forbidden_marker(content_text):
        return True
    reasoning_text = extract_reasoning_content(response)
    return isinstance(reasoning_text, str) and _contains_forbidden_marker(reasoning_text)


def is_empty_litellm_response(response: Any) -> bool:
    message = _first_message_from_response(response)
    if message is None:
        return True
    content_text = _extract_message_content(message)
    if content_text.strip():
        return False
    reasoning_text = extract_reasoning_content(response)
    if isinstance(reasoning_text, str) and reasoning_text.strip():
        return False
    if _message_has_images(message):
        return False
    if _message_has_tool_calls(message):
        return False
    return True


def raise_if_empty_litellm_response(
    response: Any,
    *,
    model: str | None = None,
    provider: str | None = None,
) -> None:
    if is_empty_litellm_response(response):
        raise EmptyLiteLLMResponseError(
            "LiteLLM returned an empty response",
            model=model,
            provider=provider,
        )


def raise_if_invalid_litellm_response(
    response: Any,
    *,
    model: str | None = None,
    provider: str | None = None,
) -> None:
    if _response_has_forbidden_markers(response):
        raise InvalidLiteLLMResponseError(
            "LiteLLM returned a response with forbidden markers",
            model=model,
            provider=provider,
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
    - Enforces non-empty responses when not streaming.
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
        kwargs["timeout"] = get_litellm_timeout_seconds()

    max_attempts = max(1, int(getattr(settings, "LITELLM_MAX_RETRIES", 2)))
    backoff_seconds = float(getattr(settings, "LITELLM_RETRY_BACKOFF_SECONDS", 1.0))

    provider_hint = kwargs.get("custom_llm_provider")
    if not isinstance(provider_hint, str):
        provider_hint = kwargs.get("provider")
    if not isinstance(provider_hint, str):
        provider_hint = None

    for attempt in range(1, max_attempts + 1):
        try:
            duration_ms = None
            if not kwargs.get("stream"):
                start_time = time.monotonic()
                response = litellm.completion(**kwargs)
                duration_ms = int(round((time.monotonic() - start_time) * 1000))
            else:
                response = litellm.completion(**kwargs)
            if not kwargs.get("stream"):
                raise_if_empty_litellm_response(response, model=model, provider=provider_hint)
                raise_if_invalid_litellm_response(response, model=model, provider=provider_hint)
                _attach_response_duration(response, duration_ms)
            return response
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


__all__ = [
    "EmptyLiteLLMResponseError",
    "InvalidLiteLLMResponseError",
    "is_empty_litellm_response",
    "raise_if_empty_litellm_response",
    "raise_if_invalid_litellm_response",
    "run_completion",
]
