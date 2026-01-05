from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Optional


def _read_attr(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _coerce_stream_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for part in value:
            if isinstance(part, str):
                parts.append(part)
                continue
            if isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts) if parts else None
    if isinstance(value, dict):
        text = value.get("text")
        if isinstance(text, str):
            return text
    try:
        return str(value)
    except Exception:
        return None


@dataclass
class StreamAccumulator:
    reasoning_parts: list[str] = field(default_factory=list)
    content_parts: list[str] = field(default_factory=list)
    tool_calls: dict[int, dict] = field(default_factory=dict)
    usage: Any = None
    finish_reason: Optional[str] = None
    response_headers: Optional[dict] = None

    def ingest_chunk(self, chunk: Any) -> tuple[Optional[str], Optional[str]]:
        choices = _read_attr(chunk, "choices") or []
        if not choices:
            self._capture_usage(chunk)
            return None, None

        choice = choices[0]
        delta = _read_attr(choice, "delta")
        if delta is None:
            return None, None

        reasoning_delta = _coerce_stream_text(_read_attr(delta, "reasoning_content"))
        content_delta = _coerce_stream_text(_read_attr(delta, "content"))

        if reasoning_delta:
            self.reasoning_parts.append(reasoning_delta)
        if content_delta:
            self.content_parts.append(content_delta)

        tool_calls_delta = _read_attr(delta, "tool_calls")
        if tool_calls_delta:
            self._ingest_tool_calls(tool_calls_delta)

        finish_reason = _read_attr(choice, "finish_reason")
        if finish_reason:
            self.finish_reason = finish_reason

        self._capture_usage(chunk)

        return reasoning_delta, content_delta

    def _capture_usage(self, chunk: Any) -> None:
        usage = _read_attr(chunk, "usage")
        if usage is None:
            model_extra = _read_attr(chunk, "model_extra")
            usage = _read_attr(model_extra, "usage")
        if usage is not None:
            self.usage = usage
        if self.response_headers is None:
            headers = _read_attr(chunk, "response_headers")
            if headers is None:
                model_extra = _read_attr(chunk, "model_extra")
                headers = _read_attr(model_extra, "response_headers") or _read_attr(model_extra, "headers")
            if isinstance(headers, dict):
                self.response_headers = headers

    def build_response(self, *, model: Optional[str], provider: Optional[str]) -> Any:
        message = SimpleNamespace(
            role="assistant",
            content="".join(self.content_parts) if self.content_parts else None,
            reasoning_content="".join(self.reasoning_parts) if self.reasoning_parts else None,
            tool_calls=self._build_tool_calls(),
        )
        choice = SimpleNamespace(
            message=message,
            finish_reason=self.finish_reason,
            index=0,
        )
        model_extra = {"usage": self.usage} if self.usage is not None else {}
        if self.response_headers:
            model_extra["response_headers"] = self.response_headers
        response = SimpleNamespace(
            choices=[choice],
            usage=self.usage,
            model=model,
            provider=provider,
            model_extra=model_extra or None,
        )
        return response

    def _ingest_tool_calls(self, tool_calls_delta: Any) -> None:
        if isinstance(tool_calls_delta, dict):
            tool_calls = [tool_calls_delta]
        else:
            tool_calls = list(tool_calls_delta)

        for call_delta in tool_calls:
            index = _read_attr(call_delta, "index", 0)
            try:
                index = int(index)
            except Exception:
                index = 0
            entry = self.tool_calls.setdefault(
                index,
                {"id": None, "type": "function", "function": {"name": "", "arguments": ""}},
            )

            call_id = _read_attr(call_delta, "id")
            if call_id:
                entry["id"] = call_id

            call_type = _read_attr(call_delta, "type")
            if call_type:
                entry["type"] = call_type

            function = _read_attr(call_delta, "function") or {}
            name = _read_attr(function, "name")
            if name:
                name_str = str(name)
                current = entry["function"]["name"]
                if not current:
                    entry["function"]["name"] = name_str
                elif current in name_str:
                    entry["function"]["name"] = name_str
                elif name_str not in current and not current.endswith(name_str):
                    entry["function"]["name"] += name_str

            arguments = _read_attr(function, "arguments")
            if arguments:
                entry["function"]["arguments"] += str(arguments)

    def _build_tool_calls(self) -> list[dict]:
        if not self.tool_calls:
            return []
        return [self.tool_calls[idx] for idx in sorted(self.tool_calls)]
