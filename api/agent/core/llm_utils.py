"""Shared helpers for constructing LiteLLM completion calls."""
import json
import logging
import queue
import threading
import time
from typing import Any, Callable, Iterable

from asgiref.sync import async_to_sync

from django.conf import settings
import litellm

from api.llm.utils import is_openai_model_name
from api.services.system_settings import get_litellm_first_data_timeout_seconds, get_litellm_timeout_seconds
from api.utils.json_schema import sanitize_tool_parameters_schema_for_llm
from .token_usage import extract_reasoning_content
_HINT_KEYS = (
    "supports_temperature",
    "supports_tool_choice",
    "use_parallel_tool_calls",
    "allow_implied_send",
    "supports_vision",
    "supports_reasoning",
    "reasoning_effort",
    "low_latency",
    "pricing_model",
    "routing_token_range",
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


class _FirstDataTimeoutStream:
    class _StreamError:
        def __init__(self, exc: Exception) -> None:
            self.exc = exc

    def __init__(
        self,
        stream: Any,
        *,
        create_stream: Callable[[], Any],
        timeout_seconds: int,
        model: str,
        provider: str | None,
        initial_attempt: int,
        max_attempts: int,
        backoff_seconds: float,
    ) -> None:
        self._stream = stream
        self._create_stream = create_stream
        self._timeout_seconds = timeout_seconds
        self._model = model
        self._provider = provider
        self._attempt = initial_attempt
        self._max_attempts = max_attempts
        self._backoff_seconds = backoff_seconds
        self._received_first_chunk = False
        self._timed_out = False

    def __iter__(self) -> "_FirstDataTimeoutStream":
        return self

    def __next__(self) -> Any:
        if self._timed_out:
            raise StopIteration
        if self._received_first_chunk:
            return next(self._stream)

        while True:
            try:
                result = self._read_first_chunk()
            except _RETRYABLE_ERRORS as exc:
                self._retry_or_raise(exc)
                continue
            self._received_first_chunk = True
            return result

    def _retry_or_raise(self, exc: Exception) -> None:
        while True:
            if self._attempt >= self._max_attempts:
                raise exc
            self.close()
            logger.warning(
                "LLM request failed with %s; retrying (%d/%d)",
                type(exc).__name__,
                self._attempt,
                self._max_attempts,
            )
            if self._backoff_seconds > 0:
                time.sleep(self._backoff_seconds * (2 ** (self._attempt - 1)))
            self._attempt += 1
            try:
                self._stream = self._create_stream()
                return
            except _RETRYABLE_ERRORS as retry_exc:
                exc = retry_exc

    def _read_first_chunk(self) -> Any:
        result_queue: queue.Queue[Any] = queue.Queue(maxsize=1)

        def _read_first_chunk() -> None:
            try:
                result_queue.put(next(self._stream))
            except Exception as exc:
                result_queue.put(self._StreamError(exc))

        thread = threading.Thread(target=_read_first_chunk, daemon=True)
        thread.start()
        try:
            result = result_queue.get(timeout=self._timeout_seconds)
        except queue.Empty:
            self._timed_out = True
            self.close()
            raise litellm.Timeout(
                message=f"LLM stream produced no data within {self._timeout_seconds} seconds",
                model=self._model,
                llm_provider=self._provider or "unknown",
            )

        if isinstance(result, self._StreamError):
            raise result.exc
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)

    def close(self) -> None:
        aclose = getattr(self._stream, "aclose", None)
        if callable(aclose):
            try:
                async_to_sync(aclose)()
            except Exception:
                logger.debug("Failed to async-close LiteLLM stream", exc_info=True)
            return

        close = getattr(self._stream, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                logger.debug("Failed to close LiteLLM stream", exc_info=True)


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


def _wrap_stream_with_first_data_timeout(
    stream: Any,
    *,
    create_stream: Callable[[], Any],
    model: str,
    provider: str | None,
    initial_attempt: int,
    max_attempts: int,
    backoff_seconds: float,
) -> _FirstDataTimeoutStream:
    return _FirstDataTimeoutStream(
        stream,
        create_stream=create_stream,
        timeout_seconds=get_litellm_first_data_timeout_seconds(),
        model=model,
        provider=provider,
        initial_attempt=initial_attempt,
        max_attempts=max_attempts,
        backoff_seconds=backoff_seconds,
    )


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


def sanitize_tools_for_llm(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sanitized_tools: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            sanitized_tools.append(tool)
            continue
        function_block = tool.get("function")
        if not isinstance(function_block, dict):
            sanitized_tools.append(tool)
            continue
        sanitized_tool = dict(tool)
        sanitized_function = dict(function_block)
        sanitized_function["parameters"] = sanitize_tool_parameters_schema_for_llm(
            function_block.get("parameters")
        )
        sanitized_tool["function"] = sanitized_function
        sanitized_tools.append(sanitized_tool)
    return sanitized_tools


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

    - Removes internal capability, pricing, latency, and routing hints.
    - Allows ``reasoning_effort`` through LiteLLM's OpenAI parameter filter when reasoning is supported.
    - Adds ``tool_choice`` when tools are provided and supported.
    - Propagates ``parallel_tool_calls`` only when tools are provided.
    - Allows callers to control ``drop_params`` while keeping consistent defaults.
    - Enforces non-empty responses when not streaming.
    """
    params = dict(params or {})

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
    selected_reasoning_effort = extra_reasoning_effort or reasoning_effort
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": list(messages),
        **params,
        **extra_kwargs,
    }

    kwargs.pop("reasoning_effort", None)
    if (
        supports_reasoning
        and selected_reasoning_effort
        and not (_is_azure_non_openai_model(model, kwargs) and selected_reasoning_effort == "none")
    ):
        kwargs["reasoning_effort"] = selected_reasoning_effort

    if drop_params:
        kwargs["drop_params"] = True

    sanitized_tools = sanitize_tools_for_llm(tools) if tools else None
    if tools:
        kwargs["tools"] = sanitized_tools
        if tool_choice_supported:
            kwargs.setdefault("tool_choice", "auto")
    else:
        # Ensure we don't pass tool-choice hints when tools are absent
        kwargs.pop("tool_choice", None)

    if tools:
        kwargs["parallel_tool_calls"] = bool(use_parallel_tool_calls)
    else:
        kwargs.pop("parallel_tool_calls", None)

    if kwargs.get("timeout") is None:
        kwargs["timeout"] = get_litellm_timeout_seconds()

    responses_bridge_model = _litellm_responses_bridge_model(model, kwargs)
    use_responses_bridge = responses_bridge_model is not None
    if use_responses_bridge:
        kwargs["model"] = responses_bridge_model
        extra_body = kwargs.get("extra_body")
        extra_body = dict(extra_body) if isinstance(extra_body, dict) else {}
        responses_tool_choice = _litellm_responses_bridge_tool_choice(kwargs.get("tool_choice"))
        if responses_tool_choice is not None:
            extra_body["tool_choice"] = responses_tool_choice
        if supports_reasoning and selected_reasoning_effort and isinstance(selected_reasoning_effort, str):
            reasoning_params = {"effort": selected_reasoning_effort, "summary": "detailed"}
            kwargs["reasoning_effort"] = reasoning_params
            extra_body["reasoning_effort"] = reasoning_params
        if extra_body:
            kwargs["extra_body"] = extra_body
    if "reasoning_effort" in kwargs and not use_responses_bridge:
        allowed_openai_params = kwargs.get("allowed_openai_params")
        if allowed_openai_params is None:
            allowed_openai_params = []
        elif isinstance(allowed_openai_params, str):
            allowed_openai_params = [allowed_openai_params]
        else:
            allowed_openai_params = list(allowed_openai_params)
        if "reasoning_effort" not in allowed_openai_params:
            allowed_openai_params.append("reasoning_effort")
        kwargs["allowed_openai_params"] = allowed_openai_params

    max_attempts = max(1, int(getattr(settings, "LITELLM_MAX_RETRIES", 2)))
    backoff_seconds = float(getattr(settings, "LITELLM_RETRY_BACKOFF_SECONDS", 1.0))

    provider_hint = kwargs.get("custom_llm_provider")
    if not isinstance(provider_hint, str):
        provider_hint = kwargs.get("provider")
    if not isinstance(provider_hint, str):
        provider_hint = None
    if use_responses_bridge and provider_hint is None:
        provider_hint = "openai"

    for attempt in range(1, max_attempts + 1):
        try:
            duration_ms = None
            if not kwargs.get("stream"):
                start_time = time.monotonic()
                response = litellm.completion(**kwargs)
                duration_ms = int(round((time.monotonic() - start_time) * 1000))
            else:
                response = litellm.completion(**kwargs)
                response = _wrap_stream_with_first_data_timeout(
                    response,
                    create_stream=lambda: litellm.completion(**kwargs),
                    model=model,
                    provider=provider_hint,
                    initial_attempt=attempt,
                    max_attempts=max_attempts,
                    backoff_seconds=backoff_seconds,
                )
            if not kwargs.get("stream"):
                raise_if_empty_litellm_response(response, model=model, provider=provider_hint)
                raise_if_invalid_litellm_response(response, model=model, provider=provider_hint)
                _attach_response_duration(response, duration_ms)
            return response
        except _RETRYABLE_ERRORS as exc:
            if attempt >= max_attempts:
                raise
            logger.warning(
                "LLM request failed with %s; retrying (%d/%d)",
                type(exc).__name__,
                attempt,
                max_attempts,
            )
            if backoff_seconds > 0:
                time.sleep(backoff_seconds * (2 ** (attempt - 1)))


def _litellm_responses_bridge_model(model: str, kwargs: dict[str, Any]) -> str | None:
    if not isinstance(model, str):
        return None
    provider = kwargs.get("custom_llm_provider") or kwargs.get("provider")
    if model.startswith("azure/responses/"):
        return model
    if model.startswith("openai/responses/"):
        return model
    if model.startswith("responses/"):
        if provider in {"azure", "azure_openai"}:
            return f"azure/{model}" if is_openai_model_name(model) else None
        if provider in (None, "openai") and not kwargs.get("api_base"):
            return f"openai/{model}"
        return None
    if provider in {"azure", "azure_openai"}:
        if _is_azure_non_openai_model(model, kwargs):
            return None
        return model if model.startswith("azure/responses/") else f"azure/responses/{model.split('/', 1)[-1]}"
    if provider not in (None, "openai") or kwargs.get("api_base"):
        return None
    if model.startswith("openai/"):
        return f"openai/responses/{model.removeprefix('openai/')}"
    if model.startswith("gpt-"):
        return f"openai/responses/{model}"
    return None


def _is_azure_non_openai_model(model: str, kwargs: dict[str, Any]) -> bool:
    provider = kwargs.get("custom_llm_provider") or kwargs.get("provider")
    return provider in {"azure", "azure_openai"} and not is_openai_model_name(model)


def _litellm_responses_bridge_tool_choice(tool_choice: Any) -> Any:
    if not isinstance(tool_choice, dict):
        return None
    if tool_choice.get("type") != "function":
        return None
    function_choice = tool_choice.get("function")
    if not isinstance(function_choice, dict):
        return None
    function_name = function_choice.get("name")
    if not isinstance(function_name, str) or not function_name:
        return None
    return {"type": "function", "name": function_name}


__all__ = [
    "EmptyLiteLLMResponseError",
    "InvalidLiteLLMResponseError",
    "is_empty_litellm_response",
    "raise_if_empty_litellm_response",
    "raise_if_invalid_litellm_response",
    "run_completion",
    "sanitize_tools_for_llm",
]
