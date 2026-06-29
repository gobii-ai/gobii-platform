import json
from collections import deque
from types import SimpleNamespace
from typing import Any, Iterable, Optional

import litellm


_PASSTHROUGH_REQUEST_KWARGS = (
    "temperature",
    "parallel_tool_calls",
    "safety_identifier",
    "store",
    "stream",
    "timeout",
    "top_p",
    "user",
    "extra_headers",
    "extra_query",
    "extra_body",
    "api_base",
    "api_version",
    "api_key",
)


def should_use_openai_responses(model: str, kwargs: dict[str, Any]) -> bool:
    if not isinstance(model, str):
        return False
    provider = kwargs.get("custom_llm_provider") or kwargs.get("provider")
    if model.startswith("azure/responses/"):
        return True
    if provider == "azure":
        return True
    if provider not in (None, "openai"):
        return False
    if kwargs.get("api_base"):
        return False
    return provider == "openai" or model.startswith("openai/") or model.startswith("gpt-")


def create_openai_responses_completion(
    *,
    model: str,
    messages: Iterable[dict[str, Any]],
    kwargs: dict[str, Any],
    tools: list[dict[str, Any]] | None,
    supports_reasoning: bool,
    reasoning_effort: Any = None,
) -> Any:
    request_kwargs = _build_responses_request_kwargs(
        model=model,
        messages=messages,
        kwargs=kwargs,
        tools=tools,
        supports_reasoning=supports_reasoning,
        reasoning_effort=reasoning_effort,
    )
    response = litellm.responses(**request_kwargs)
    if request_kwargs.get("stream"):
        return OpenAIResponsesStream(response, model=model)
    return normalize_openai_response(response, model=model)


def _build_responses_request_kwargs(
    *,
    model: str,
    messages: Iterable[dict[str, Any]],
    kwargs: dict[str, Any],
    tools: list[dict[str, Any]] | None,
    supports_reasoning: bool,
    reasoning_effort: Any = None,
) -> dict[str, Any]:
    request_kwargs: dict[str, Any] = {
        "model": _strip_responses_provider_prefix(model, kwargs),
        "input": _convert_messages(messages),
        "custom_llm_provider": _resolve_responses_provider(model, kwargs),
    }
    for key in _PASSTHROUGH_REQUEST_KWARGS:
        _copy_if_present(kwargs, request_kwargs, key)

    max_output_tokens = kwargs.get("max_output_tokens")
    if max_output_tokens is None:
        max_output_tokens = kwargs.get("max_completion_tokens")
    if max_output_tokens is None:
        max_output_tokens = kwargs.get("max_tokens")
    if max_output_tokens is not None:
        request_kwargs["max_output_tokens"] = max_output_tokens

    stream_options = kwargs.get("stream_options")
    if kwargs.get("stream"):
        if isinstance(stream_options, dict):
            stream_options = dict(stream_options)
        else:
            stream_options = {}
        stream_options["include_usage"] = True
        request_kwargs["stream_options"] = stream_options
    elif stream_options is not None:
        request_kwargs["stream_options"] = stream_options

    if supports_reasoning:
        reasoning: dict[str, Any] = {"summary": "auto"}
        if reasoning_effort:
            reasoning["effort"] = reasoning_effort
        request_kwargs["reasoning"] = reasoning

    responses_tools = _convert_tools(tools or [])
    if responses_tools:
        request_kwargs["tools"] = responses_tools
        tool_choice = _convert_tool_choice(kwargs.get("tool_choice"))
        if tool_choice is not None:
            request_kwargs["tool_choice"] = tool_choice

    return request_kwargs


def _copy_if_present(source: dict[str, Any], target: dict[str, Any], key: str) -> None:
    if key in source and source[key] is not None:
        target[key] = source[key]


def _strip_openai_prefix(model: str) -> str:
    return model[len("openai/"):] if model.startswith("openai/") else model


def _resolve_responses_provider(model: str, kwargs: dict[str, Any]) -> str:
    provider = kwargs.get("custom_llm_provider") or kwargs.get("provider")
    if provider == "azure" or model.startswith("azure/"):
        return "azure"
    return "openai"


def _strip_responses_provider_prefix(model: str, kwargs: dict[str, Any]) -> str:
    provider = _resolve_responses_provider(model, kwargs)
    if provider == "azure" and model.startswith("azure/"):
        return model[len("azure/"):]
    return _strip_openai_prefix(model)


def _convert_messages(messages: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        if role == "tool":
            converted.append(
                {
                    "type": "function_call_output",
                    "call_id": message.get("tool_call_id") or message.get("id") or "",
                    "output": _coerce_text(message.get("content")),
                }
            )
            continue
        if role == "function":
            converted.append(
                {
                    "type": "function_call_output",
                    "call_id": message.get("name") or message.get("id") or "",
                    "output": _coerce_text(message.get("content")),
                }
            )
            continue

        content = message.get("content")
        if role == "assistant":
            if content:
                converted.append({"role": "assistant", "content": _assistant_content_text(content)})
            for tool_call in _coerce_tool_calls(message.get("tool_calls")):
                converted.append(_convert_prior_tool_call(tool_call))
            continue

        if role in {"user", "system", "developer"}:
            converted.append({"role": role, "content": _convert_input_content(content)})
            continue

        converted.append({"role": "user", "content": _convert_input_content(content)})
    return converted


def _convert_input_content(content: Any) -> Any:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return _coerce_text(content)

    parts: list[dict[str, Any]] = []
    for part in content:
        converted = _convert_input_part(part)
        if converted is not None:
            parts.append(converted)
    return parts if parts else ""


def _convert_input_part(part: Any) -> Optional[dict[str, Any]]:
    if isinstance(part, str):
        return {"type": "input_text", "text": part}
    if not isinstance(part, dict):
        return {"type": "input_text", "text": _coerce_text(part)}

    part_type = part.get("type")
    if part_type in {"text", "input_text"}:
        return {"type": "input_text", "text": _coerce_text(part.get("text"))}
    if part_type in {"image_url", "image", "input_image"}:
        image_url = part.get("image_url")
        if isinstance(image_url, dict):
            image_url = image_url.get("url")
        converted = {
            "type": "input_image",
            "image_url": image_url,
            "detail": part.get("detail") or "auto",
        }
        if not converted["image_url"] and part.get("file_id"):
            converted["file_id"] = part.get("file_id")
            converted.pop("image_url", None)
        return converted
    if part_type in {"file", "input_file"}:
        converted = {"type": "input_file"}
        for key in ("file_id", "file_url", "file_data", "filename"):
            if part.get(key):
                converted[key] = part[key]
        return converted
    text = part.get("text") or part.get("content")
    if text is not None:
        return {"type": "input_text", "text": _coerce_text(text)}
    return None


def _assistant_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
                continue
            if isinstance(part, dict):
                text = part.get("text") or part.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return _coerce_text(content)


def _convert_prior_tool_call(tool_call: Any) -> dict[str, Any]:
    if not isinstance(tool_call, dict):
        return {
            "type": "function_call",
            "call_id": "",
            "id": "",
            "name": "",
            "arguments": _coerce_text(tool_call),
        }
    function = tool_call.get("function") or {}
    if not isinstance(function, dict):
        function = {}
    call_id = tool_call.get("id") or tool_call.get("call_id") or ""
    return {
        "type": "function_call",
        "call_id": call_id,
        "id": call_id,
        "name": function.get("name") or tool_call.get("name") or "",
        "arguments": function.get("arguments") or tool_call.get("arguments") or "",
    }


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
        return raw_tool_calls
    try:
        return list(raw_tool_calls)
    except TypeError:
        return [raw_tool_calls]


def _convert_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function")
        if tool.get("type") != "function" or not isinstance(function, dict):
            converted.append(tool)
            continue
        converted_tool = {
            "type": "function",
            "name": function.get("name") or "",
            "description": function.get("description") or "",
            "parameters": function.get("parameters") or {"type": "object", "properties": {}},
            "strict": function.get("strict", False),
        }
        converted.append(converted_tool)
    return converted


def _convert_tool_choice(tool_choice: Any) -> Any:
    if tool_choice is None:
        return None
    if isinstance(tool_choice, str):
        return tool_choice
    if not isinstance(tool_choice, dict):
        return None
    if tool_choice.get("type") == "function":
        function = tool_choice.get("function") or {}
        name = function.get("name") if isinstance(function, dict) else None
        if name:
            return {"type": "function", "name": name}
    return tool_choice


def normalize_openai_response(response: Any, *, model: str) -> Any:
    content = _extract_response_content(response)
    reasoning_content = _extract_response_reasoning(response)
    tool_calls = _extract_response_tool_calls(response)
    usage = _normalize_usage(getattr(response, "usage", None))
    response_id = _coerce_optional_str(getattr(response, "id", None))
    message = SimpleNamespace(
        role="assistant",
        content=content,
        reasoning_content=reasoning_content,
        tool_calls=tool_calls,
    )
    finish_reason = _finish_reason_from_response(response)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish_reason, index=0)],
        usage=usage,
        model=model,
        provider="openai",
        id=response_id,
        response_id=response_id,
        model_extra={"usage": usage} if usage is not None else None,
    )


def _extract_response_content(response: Any) -> Optional[str]:
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str) and output_text:
        return output_text

    parts: list[str] = []
    for item in getattr(response, "output", []) or []:
        if getattr(item, "type", None) != "message":
            continue
        for content_part in getattr(item, "content", []) or []:
            if getattr(content_part, "type", None) == "output_text":
                text = getattr(content_part, "text", None)
                if isinstance(text, str):
                    parts.append(text)
    return "".join(parts) if parts else None


def _extract_response_reasoning(response: Any) -> Optional[str]:
    parts: list[str] = []
    for item in getattr(response, "output", []) or []:
        if getattr(item, "type", None) != "reasoning":
            continue
        for summary_part in getattr(item, "summary", []) or []:
            text = getattr(summary_part, "text", None)
            if isinstance(text, str) and text.strip():
                parts.append(text)
    return "\n".join(parts) if parts else None


def _extract_response_tool_calls(response: Any) -> list[dict[str, Any]]:
    tool_calls: list[dict[str, Any]] = []
    for item in getattr(response, "output", []) or []:
        if getattr(item, "type", None) != "function_call":
            continue
        call_id = getattr(item, "call_id", None) or getattr(item, "id", None) or f"function_call_{len(tool_calls)}"
        tool_calls.append(
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": getattr(item, "name", None) or "",
                    "arguments": getattr(item, "arguments", None) or "",
                },
            }
        )
    return tool_calls


def _normalize_usage(usage: Any) -> Any:
    if usage is None:
        return None
    input_tokens = getattr(usage, "input_tokens", None)
    output_tokens = getattr(usage, "output_tokens", None)
    total_tokens = getattr(usage, "total_tokens", None)
    input_details = getattr(usage, "input_tokens_details", None)
    cached_tokens = getattr(input_details, "cached_tokens", 0) if input_details is not None else 0
    prompt_tokens_details = SimpleNamespace(cached_tokens=cached_tokens)
    output_details = getattr(usage, "output_tokens_details", None)
    completion_tokens_details = SimpleNamespace(
        reasoning_tokens=getattr(output_details, "reasoning_tokens", 0) if output_details is not None else 0
    )
    return SimpleNamespace(
        prompt_tokens=input_tokens,
        completion_tokens=output_tokens,
        total_tokens=total_tokens,
        prompt_tokens_details=prompt_tokens_details,
        completion_tokens_details=completion_tokens_details,
    )


def _finish_reason_from_response(response: Any) -> Optional[str]:
    status = getattr(response, "status", None)
    if status == "completed":
        return "stop"
    if status == "incomplete":
        details = getattr(response, "incomplete_details", None)
        reason = getattr(details, "reason", None)
        return reason or "incomplete"
    return status


def _coerce_optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    return str(value)


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value)
    except (TypeError, ValueError):
        return str(value)


def _chat_stream_chunk(
    *,
    model: str,
    content_delta: Optional[str] = None,
    reasoning_delta: Optional[str] = None,
    tool_calls: Optional[list[dict[str, Any]]] = None,
    usage: Any = None,
    response_id: Optional[str] = None,
    finish_reason: Optional[str] = None,
) -> Any:
    delta = SimpleNamespace(
        content=content_delta,
        reasoning_content=reasoning_delta,
        tool_calls=tool_calls,
    )
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason, index=0)
    return SimpleNamespace(
        choices=[choice],
        usage=usage,
        model=model,
        provider="openai",
        id=response_id,
        response_id=response_id,
        model_extra={"usage": usage} if usage is not None else None,
    )


class OpenAIResponsesStream:
    def __init__(self, stream: Any, *, model: str) -> None:
        self._stream = iter(stream)
        self._model = model
        self._pending_chunks: deque[Any] = deque()
        self._response_id: Optional[str] = None
        self._saw_content_delta = False
        self._saw_reasoning_delta = False
        self._tool_argument_delta_seen: set[str] = set()
        self._emitted_tool_calls: set[str] = set()

    def __iter__(self) -> "OpenAIResponsesStream":
        return self

    def __next__(self) -> Any:
        while not self._pending_chunks:
            event = next(self._stream)
            self._ingest_event(event)
        return self._pending_chunks.popleft()

    def close(self) -> None:
        close = getattr(self._stream, "close", None)
        if callable(close):
            close()

    async def aclose(self) -> None:
        aclose = getattr(self._stream, "aclose", None)
        if callable(aclose):
            await aclose()
            return
        self.close()

    def _ingest_event(self, event: Any) -> None:
        event_type = getattr(event, "type", None)
        if event_type == "response.created":
            response = getattr(event, "response", None)
            self._response_id = _coerce_optional_str(getattr(response, "id", None)) or self._response_id
            return
        if event_type == "response.output_text.delta":
            delta = getattr(event, "delta", None)
            if delta:
                self._saw_content_delta = True
                self._pending_chunks.append(
                    _chat_stream_chunk(model=self._model, content_delta=str(delta), response_id=self._response_id)
                )
            return
        if event_type == "response.reasoning_summary_text.delta":
            delta = getattr(event, "delta", None)
            if delta:
                self._saw_reasoning_delta = True
                self._pending_chunks.append(
                    _chat_stream_chunk(model=self._model, reasoning_delta=str(delta), response_id=self._response_id)
                )
            return
        if event_type == "response.function_call_arguments.delta":
            output_index = getattr(event, "output_index", 0)
            call_id = getattr(event, "item_id", None) or f"function_call_{output_index}"
            delta = getattr(event, "delta", None)
            if delta:
                self._tool_argument_delta_seen.add(str(call_id))
                self._append_tool_call_delta(
                    call_id=str(call_id),
                    name=getattr(event, "name", None) or "",
                    arguments=str(delta),
                    index=output_index or 0,
                )
            return
        if event_type == "response.function_call_arguments.done":
            output_index = getattr(event, "output_index", 0)
            call_id = getattr(event, "item_id", None) or f"function_call_{output_index}"
            arguments = "" if str(call_id) in self._tool_argument_delta_seen else getattr(event, "arguments", None) or ""
            self._append_tool_call(
                call_id=str(call_id),
                name=getattr(event, "name", None) or "",
                arguments=arguments,
                index=output_index or 0,
            )
            return
        if event_type == "response.output_item.done":
            item = getattr(event, "item", None)
            if getattr(item, "type", None) == "function_call":
                output_index = getattr(event, "output_index", 0)
                call_id = getattr(item, "call_id", None) or getattr(item, "id", None) or f"function_call_{output_index}"
                self._append_tool_call(
                    call_id=call_id,
                    name=getattr(item, "name", None) or "",
                    arguments=getattr(item, "arguments", None) or "",
                    index=output_index or 0,
                )
            return
        if event_type == "response.completed":
            response = getattr(event, "response", None)
            self._response_id = _coerce_optional_str(getattr(response, "id", None)) or self._response_id
            self._append_final_response_chunks(response, finish_reason="stop")
            return
        if event_type == "response.incomplete":
            response = getattr(event, "response", None)
            self._response_id = _coerce_optional_str(getattr(response, "id", None)) or self._response_id
            self._append_final_response_chunks(response, finish_reason=_finish_reason_from_response(response))
            return
        if event_type == "response.failed":
            response = getattr(event, "response", None)
            error = getattr(response, "error", None)
            _raise_litellm_stream_error(error, "OpenAI Responses stream failed")
        if event_type == "error":
            error = getattr(event, "error", None)
            _raise_litellm_stream_error(error or event, "OpenAI Responses stream error")

    def _append_tool_call(self, *, call_id: str, name: str, arguments: str, index: int) -> None:
        if call_id in self._emitted_tool_calls:
            return
        self._emitted_tool_calls.add(call_id)
        self._pending_chunks.append(
            _chat_stream_chunk(
                model=self._model,
                tool_calls=[
                    {
                        "index": index,
                        "id": call_id,
                        "type": "function",
                        "function": {"name": name, "arguments": arguments},
                    }
                ],
                response_id=self._response_id,
            )
        )

    def _append_tool_call_delta(self, *, call_id: str, name: str, arguments: str, index: int) -> None:
        self._pending_chunks.append(
            _chat_stream_chunk(
                model=self._model,
                tool_calls=[
                    {
                        "index": index,
                        "id": call_id,
                        "type": "function",
                        "function": {"name": name, "arguments": arguments},
                    }
                ],
                response_id=self._response_id,
            )
        )

    def _append_final_response_chunks(self, response: Any, *, finish_reason: Optional[str]) -> None:
        if not self._saw_content_delta:
            content = _extract_response_content(response)
            if content:
                self._pending_chunks.append(
                    _chat_stream_chunk(model=self._model, content_delta=content, response_id=self._response_id)
                )
        if not self._saw_reasoning_delta:
            reasoning = _extract_response_reasoning(response)
            if reasoning:
                self._pending_chunks.append(
                    _chat_stream_chunk(model=self._model, reasoning_delta=reasoning, response_id=self._response_id)
                )
        for index, tool_call in enumerate(_extract_response_tool_calls(response)):
            self._append_tool_call(
                call_id=tool_call["id"],
                name=tool_call["function"]["name"],
                arguments=tool_call["function"]["arguments"],
                index=index,
            )
        usage = _normalize_usage(getattr(response, "usage", None))
        self._pending_chunks.append(
            _chat_stream_chunk(
                model=self._model,
                usage=usage,
                response_id=self._response_id,
                finish_reason=finish_reason,
            )
        )


def _raise_litellm_stream_error(error: Any, default_message: str) -> None:
    if isinstance(error, dict):
        code = error.get("code")
        message = error.get("message") or default_message
    else:
        code = getattr(error, "code", None)
        message = getattr(error, "message", None) or default_message
    if code == "rate_limit_exceeded":
        raise litellm.RateLimitError(message=message, llm_provider="openai", model="openai/responses")
    if code == "internal_error":
        raise litellm.ServiceUnavailableError(message=message, llm_provider="openai", model="openai/responses")
    raise litellm.APIError(
        status_code=400,
        message=f"{message} (code={code or 'unknown'})",
        llm_provider="openai",
        model="openai/responses",
    )


__all__ = [
    "create_openai_responses_completion",
    "normalize_openai_response",
    "should_use_openai_responses",
]
