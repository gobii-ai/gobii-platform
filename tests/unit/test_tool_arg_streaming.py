"""A chat-style reply tool must stream like the agent's thinking does.

Web replies are tool calls, so the body arrives token by token inside the JSON `arguments`
fragment of the tool-call delta — and the streaming loop dropped those on the floor. Thinking
streamed (it is `reasoning_content`); the message itself appeared only after the tool executed.
These pin the incremental extractor that surfaces the body as it is generated.
"""
from django.test import SimpleTestCase, tag

from api.agent.core.tool_arg_streaming import ChatBodyStreamExtractor


def _delta(index: int, name: str | None = None, args: str | None = None) -> list[dict]:
    function: dict = {}
    if name is not None:
        function["name"] = name
    if args is not None:
        function["arguments"] = args
    return [{"index": index, "function": function}]


@tag("batch_event_processing")
class ChatBodyStreamExtractorTests(SimpleTestCase):
    def test_streams_the_body_of_a_send_chat_message_call(self):
        ex = ChatBodyStreamExtractor()
        out = []
        out.append(ex.ingest(_delta(0, name="send_chat", args="")))
        out.append(ex.ingest(_delta(0, name="_message", args='{"bo')))
        out.append(ex.ingest(_delta(0, args='dy": "Hel')))
        out.append(ex.ingest(_delta(0, args='lo the')))
        out.append(ex.ingest(_delta(0, args='re!", "will_continue_work": false}')))
        self.assertEqual("".join(part for part in out if part), "Hello there!")

    def test_streams_the_body_of_a_send_mcp_message_call(self):
        ex = ChatBodyStreamExtractor()
        out = []
        out.append(ex.ingest(_delta(0, name="send_mcp", args="")))
        out.append(ex.ingest(_delta(0, name="_message", args='{"body": "MCP re')))
        out.append(ex.ingest(_delta(0, args='ply", "will_continue_work": false}')))
        self.assertEqual("".join(part for part in out if part), "MCP reply")

    def test_json_escapes_decode_across_fragment_boundaries(self):
        ex = ChatBodyStreamExtractor()
        out = []
        out.append(ex.ingest(_delta(0, name="send_chat_message", args='{"body": "line1\\')))
        out.append(ex.ingest(_delta(0, args='nline2 \\u00e9 and \\')))
        out.append(ex.ingest(_delta(0, args='"quoted\\""}')))
        self.assertEqual("".join(part for part in out if part), 'line1\nline2 é and "quoted"')

    def test_surrogate_pair_split_across_fragments(self):
        ex = ChatBodyStreamExtractor()
        out = []
        out.append(ex.ingest(_delta(0, name="send_chat_message", args='{"body": "\\ud83d')))
        out.append(ex.ingest(_delta(0, args='\\ude00 done"}')))
        self.assertEqual("".join(part for part in out if part), "\U0001f600 done")

    def test_other_tools_do_not_stream(self):
        ex = ChatBodyStreamExtractor()
        out = []
        out.append(ex.ingest(_delta(0, name="send_email", args='{"body": "external mail text"}')))
        self.assertEqual("".join(part for part in out if part), "")

    def test_keys_before_body_are_skipped(self):
        ex = ChatBodyStreamExtractor()
        out = []
        out.append(ex.ingest(_delta(0, name="send_chat_message", args='{"will_continue_work": false, "bo')))
        out.append(ex.ingest(_delta(0, args='dy": "after other keys"}')))
        self.assertEqual("".join(part for part in out if part), "after other keys")

    def test_a_body_valued_string_inside_another_key_is_not_mistaken(self):
        """A value containing the text "body": must not open extraction."""
        ex = ChatBodyStreamExtractor()
        out = []
        out.append(ex.ingest(_delta(0, name="send_chat_message", args='{"note": "the \\"body\\": key", "body": "real"}')))
        self.assertEqual("".join(part for part in out if part), "real")

    def test_only_the_first_chat_send_streams(self):
        """Two send calls in one completion: streaming both would concatenate two messages."""
        ex = ChatBodyStreamExtractor()
        out = []
        out.append(ex.ingest(_delta(0, name="send_chat_message", args='{"body": "first"}')))
        out.append(ex.ingest(_delta(1, name="send_chat_message", args='{"body": "second"}')))
        self.assertEqual("".join(part for part in out if part), "first")

    def test_none_and_empty_deltas_are_harmless(self):
        ex = ChatBodyStreamExtractor()
        self.assertIsNone(ex.ingest(None))
        self.assertIsNone(ex.ingest([]))


@tag("batch_event_processing")
class StreamLoopBodyBroadcastTests(SimpleTestCase):
    """The loop must push tool-call body text to the web stream even when plain content
    streaming is disabled — that combination (tool-send models) is exactly the fleet state in
    which "thinking streams, messages don't" was reported."""

    def test_send_chat_message_body_reaches_the_broadcaster(self):
        from types import SimpleNamespace
        from unittest.mock import patch

        from api.agent.core.event_processing import _stream_completion_with_broadcast

        def chunk(args=None, name=None, content=None, reasoning=None, finish=None):
            function = {}
            if name is not None:
                function["name"] = name
            if args is not None:
                function["arguments"] = args
            delta = SimpleNamespace(
                content=content,
                reasoning_content=reasoning,
                tool_calls=[{"index": 0, "id": "call_1", "type": "function", "function": function}] if function else None,
            )
            return SimpleNamespace(id="resp", choices=[SimpleNamespace(delta=delta, finish_reason=finish)], usage=None)

        chunks = [
            chunk(reasoning="thinking..."),
            chunk(name="send_chat_message", args='{"bo'),
            chunk(args='dy": "Hi An'),
            chunk(args='drew!", "will_continue_work": false}', finish="tool_calls"),
        ]

        pushed: list[tuple] = []
        broadcaster = SimpleNamespace(
            start=lambda: None,
            push_delta=lambda r, c: pushed.append((r, c)),
            finish=lambda: None,
            cancel=lambda: None,
        )

        with patch("api.agent.core.event_processing.run_completion", return_value=iter(chunks)):
            response = _stream_completion_with_broadcast(
                model="m",
                messages=[{"role": "user", "content": "hi"}],
                params={},
                tools=None,
                provider="p",
                stream_broadcaster=broadcaster,
                stream_content=False,  # tool-send model: implied-send content disabled
            )

        body_stream = "".join(c for _r, c in pushed if c)
        self.assertEqual(body_stream, "Hi Andrew!")
        reasoning_stream = "".join(r for r, _c in pushed if r)
        self.assertEqual(reasoning_stream, "thinking...")
        # accumulation is unaffected: the tool call still materializes whole for execution
        tool_calls = response.choices[0].message.tool_calls
        self.assertEqual(len(tool_calls), 1)
        self.assertIn("Hi Andrew!", str(tool_calls[0]))
