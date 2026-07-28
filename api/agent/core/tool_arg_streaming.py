"""Stream the body of a chat-style message tool call as the model writes it.

A web reply is a tool call, so its text arrives token by token inside the JSON ``arguments``
fragments of the tool-call delta — invisible to the content stream. Thinking streams because it
is ``reasoning_content``; without this extractor the message itself only appears when the tool
executes, which reads as "streaming is broken" to anyone watching the chat.

The extractor is a minimal incremental JSON lexer: it walks argument fragments character by
character, tracks string/escape/depth state across fragment boundaries (including ``\\uXXXX``
escapes and surrogate pairs split mid-escape), and emits the decoded value of the top-level
``body`` key of the first web-chat or MCP reply call. Everything else is ignored.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

_CHAT_TOOL_NAMES = ("send_chat_message", "send_mcp_message")

_ESCAPE_MAP = {
    '"': '"',
    "\\": "\\",
    "/": "/",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
}


class _ArgLexer:
    """Incremental scan of one tool call's arguments for the top-level "body" string value."""

    def __init__(self) -> None:
        self._depth = 0
        self._in_string = False
        self._escape = False
        self._unicode_pending: str | None = None
        self._high_surrogate: int | None = None
        self._expecting_key = False
        self._current_is_key = False
        self._current_key_chars: list[str] = []
        self._streaming_body = False
        self._body_next_value = False
        self._done = False

    @property
    def done(self) -> bool:
        return self._done

    def feed(self, fragment: str) -> str:
        if self._done or not fragment:
            return ""
        out: list[str] = []
        for ch in fragment:
            self._feed_char(ch, out)
            if self._done:
                break
        return "".join(out)

    def _emit(self, ch: str, out: list[str]) -> None:
        if self._current_is_key:
            self._current_key_chars.append(ch)
        elif self._streaming_body:
            out.append(ch)

    def _emit_codepoint(self, code: int, out: list[str]) -> None:
        if self._high_surrogate is not None:
            high = self._high_surrogate
            self._high_surrogate = None
            if 0xDC00 <= code <= 0xDFFF:
                combined = 0x10000 + ((high - 0xD800) << 10) + (code - 0xDC00)
                self._emit(chr(combined), out)
                return
            self._emit("�", out)
            # fall through to handle `code` on its own
        if 0xD800 <= code <= 0xDBFF:
            self._high_surrogate = code
            return
        if 0xDC00 <= code <= 0xDFFF:
            self._emit("�", out)
            return
        self._emit(chr(code), out)

    def _feed_char(self, ch: str, out: list[str]) -> None:
        if self._in_string:
            if self._unicode_pending is not None:
                self._unicode_pending += ch
                if len(self._unicode_pending) == 4:
                    try:
                        code = int(self._unicode_pending, 16)
                    except ValueError:
                        self._emit("�", out)
                    else:
                        self._emit_codepoint(code, out)
                    self._unicode_pending = None
                return
            if self._escape:
                self._escape = False
                if ch == "u":
                    self._unicode_pending = ""
                    return
                self._emit(_ESCAPE_MAP.get(ch, ch), out)
                return
            if ch == "\\":
                self._escape = True
                return
            if ch == '"':
                self._in_string = False
                if self._current_is_key:
                    key = "".join(self._current_key_chars)
                    self._current_key_chars = []
                    self._current_is_key = False
                    self._body_next_value = self._depth == 1 and key == "body"
                elif self._streaming_body:
                    self._streaming_body = False
                    self._done = True
                return
            self._emit(ch, out)
            return

        if ch == '"':
            self._in_string = True
            if self._expecting_key:
                self._current_is_key = True
                self._current_key_chars = []
                self._expecting_key = False
            elif self._body_next_value:
                self._streaming_body = True
                self._body_next_value = False
            return
        if ch == "{":
            self._depth += 1
            self._expecting_key = True
            return
        if ch == "[":
            self._depth += 1
            self._expecting_key = False
            self._body_next_value = False
            return
        if ch in "}]":
            self._depth -= 1
            self._expecting_key = False
            return
        if ch == ",":
            # In an object the next string is a key; keys only matter at depth 1.
            self._expecting_key = True
            self._body_next_value = False
            return
        if ch == ":":
            self._expecting_key = False
            return
        # whitespace / literals / numbers: a non-string value after "body": means no string body
        if self._body_next_value and not ch.isspace():
            self._body_next_value = False


class ChatBodyStreamExtractor:
    """Surface the body of the first chat-style message call across streamed tool-call deltas."""

    def __init__(self) -> None:
        self._names: dict[int, list[str]] = {}
        self._chosen_index: int | None = None
        self._rejected: set[int] = set()
        self._lexer = _ArgLexer()

    def ingest(self, tool_calls_delta: Optional[Iterable[Any]]) -> Optional[str]:
        if not tool_calls_delta:
            return None
        out: list[str] = []
        for entry in tool_calls_delta:
            index = _read(entry, "index")
            index = int(index) if index is not None else 0
            function = _read(entry, "function") or {}
            name_fragment = _read(function, "name")
            args_fragment = _read(function, "arguments")

            if self._chosen_index is None and index not in self._rejected:
                name = self._names.setdefault(index, [])
                if name_fragment:
                    name.append(str(name_fragment))
                joined = "".join(name)
                if joined in _CHAT_TOOL_NAMES:
                    self._chosen_index = index
                elif not any(tool_name.startswith(joined) for tool_name in _CHAT_TOOL_NAMES):
                    self._rejected.add(index)

            if index == self._chosen_index and args_fragment and not self._lexer.done:
                out.append(self._lexer.feed(str(args_fragment)))
        text = "".join(out)
        return text or None


def _read(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)
