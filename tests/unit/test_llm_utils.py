import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import TestCase, override_settings, tag
import litellm

from api.agent.core.llm_utils import InvalidLiteLLMResponseError, run_completion
from tests.utils.token_usage import make_completion_response


class _ClosableStream:
    def __init__(self, chunks):
        self.chunks = iter(chunks)
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self):
        return next(self.chunks)

    def close(self):
        self.closed = True


class _BlockingFirstChunkStream:
    def __init__(self):
        self.closed = False
        self.started = threading.Event()
        self.released = threading.Event()

    def __iter__(self):
        return self

    def __next__(self):
        self.started.set()
        self.released.wait(timeout=5)
        if self.closed:
            raise StopIteration
        return "late-chunk"

    def close(self):
        self.closed = True
        self.released.set()


class RunCompletionReasoningTests(TestCase):
    @tag("batch_event_llm")
    @patch("api.agent.core.llm_utils.litellm.completion")
    def test_reasoning_effort_omitted_when_not_supported(self, mock_completion):
        run_completion(
            model="mock-model",
            messages=[],
            params={"supports_reasoning": False, "reasoning_effort": "high"},
        )

        _, kwargs = mock_completion.call_args
        self.assertNotIn("reasoning_effort", kwargs)
        self.assertNotIn("allowed_openai_params", kwargs)
        self.assertNotIn("supports_reasoning", kwargs)

    @tag("batch_event_llm")
    @patch("api.agent.core.llm_utils.litellm.completion")
    def test_reasoning_effort_forwarded_when_supported(self, mock_completion):
        run_completion(
            model="mock-model",
            messages=[],
            params={"supports_reasoning": True, "reasoning_effort": "low"},
        )

        _, kwargs = mock_completion.call_args
        self.assertEqual(kwargs.get("reasoning_effort"), "low")
        self.assertEqual(kwargs.get("allowed_openai_params"), ["reasoning_effort"])
        self.assertNotIn("supports_reasoning", kwargs)

    @tag("batch_event_llm")
    @patch("api.agent.core.llm_utils.litellm.completion")
    def test_reasoning_effort_allowed_param_preserves_existing_values(self, mock_completion):
        run_completion(
            model="mock-model",
            messages=[],
            params={"supports_reasoning": True, "reasoning_effort": "low"},
            allowed_openai_params=["extra_body"],
        )

        _, kwargs = mock_completion.call_args
        self.assertEqual(kwargs.get("reasoning_effort"), "low")
        self.assertEqual(kwargs.get("allowed_openai_params"), ["extra_body", "reasoning_effort"])

    @tag("batch_event_llm")
    @patch("api.agent.core.llm_utils.litellm.completion")
    def test_allow_implied_send_hint_is_not_forwarded(self, mock_completion):
        run_completion(
            model="mock-model",
            messages=[],
            params={"allow_implied_send": False},
        )

        _, kwargs = mock_completion.call_args
        self.assertNotIn("allow_implied_send", kwargs)

    @tag("batch_event_llm")
    @patch("api.agent.core.llm_utils.litellm.completion")
    def test_tools_are_sanitized_before_litellm_call(self, mock_completion):
        mock_completion.return_value = make_completion_response(content="ok")

        run_completion(
            model="mock-model",
            messages=[],
            params={},
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "mcp_analytics-db_pg_execute_sql",
                        "description": "Execute SQL",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "sql": {
                                    "type": "object",
                                    "properties": {
                                        "query": {"type": "string"},
                                        "params": {"type": "array"},
                                    },
                                },
                            },
                        },
                    },
                }
            ],
        )

        _, kwargs = mock_completion.call_args
        params_schema = kwargs["tools"][0]["function"]["parameters"]
        self.assertEqual(
            params_schema["properties"]["sql"]["properties"]["params"]["items"],
            {"type": "string"},
        )

    @tag("batch_event_llm")
    @override_settings(LITELLM_TIMEOUT_SECONDS=321)
    @patch("api.agent.core.llm_utils.litellm.completion")
    def test_timeout_defaults_to_settings_value(self, mock_completion):
        run_completion(
            model="mock-model",
            messages=[],
            params={},
        )

        _, kwargs = mock_completion.call_args
        self.assertEqual(kwargs.get("timeout"), 321)

    @tag("batch_event_llm")
    @override_settings(LITELLM_TIMEOUT_SECONDS=321)
    @patch("api.agent.core.llm_utils.litellm.completion")
    def test_timeout_respects_explicit_value(self, mock_completion):
        run_completion(
            model="mock-model",
            messages=[],
            params={},
            timeout=42,
        )

        _, kwargs = mock_completion.call_args
        self.assertEqual(kwargs.get("timeout"), 42)

    @tag("batch_event_llm")
    @override_settings(LITELLM_TIMEOUT_SECONDS=321)
    @patch("api.agent.core.llm_utils.litellm.completion")
    def test_streaming_completion_is_wrapped_for_first_data_timeout(self, mock_completion):
        stream = _ClosableStream(["first-chunk"])
        mock_completion.return_value = stream

        result = run_completion(
            model="mock-model",
            messages=[],
            params={},
            stream=True,
        )

        self.assertIsNot(result, stream)
        self.assertEqual(next(result), "first-chunk")
        _, kwargs = mock_completion.call_args
        self.assertEqual(kwargs.get("timeout"), 321)

    @tag("batch_event_llm")
    @patch("api.agent.core.llm_utils.get_litellm_first_data_timeout_seconds", return_value=1)
    @patch("api.agent.core.llm_utils.litellm.completion")
    def test_streaming_first_chunk_succeeds_within_timeout(
        self,
        mock_completion,
        _mock_first_data_timeout,
    ):
        stream = _ClosableStream(["first-chunk", "second-chunk"])
        mock_completion.return_value = stream

        result = run_completion(
            model="mock-model",
            messages=[],
            params={},
            stream=True,
        )

        self.assertEqual(next(result), "first-chunk")
        self.assertEqual(next(result), "second-chunk")
        self.assertFalse(stream.closed)

    @tag("batch_event_llm")
    @patch("api.agent.core.llm_utils.get_litellm_first_data_timeout_seconds", return_value=0.01)
    @patch("api.agent.core.llm_utils.litellm.completion")
    def test_streaming_first_chunk_timeout_raises_litellm_timeout(
        self,
        mock_completion,
        _mock_first_data_timeout,
    ):
        stream = _BlockingFirstChunkStream()
        mock_completion.return_value = stream

        result = run_completion(
            model="mock-model",
            messages=[],
            params={},
            stream=True,
        )

        with self.assertRaises(litellm.Timeout):
            next(result)
        self.assertTrue(stream.started.wait(timeout=1))

    @tag("batch_event_llm")
    @patch("api.agent.core.llm_utils.get_litellm_first_data_timeout_seconds", return_value=0.01)
    @patch("api.agent.core.llm_utils.litellm.completion")
    def test_streaming_first_chunk_timeout_closes_underlying_stream(
        self,
        mock_completion,
        _mock_first_data_timeout,
    ):
        stream = _BlockingFirstChunkStream()
        mock_completion.return_value = stream

        result = run_completion(
            model="mock-model",
            messages=[],
            params={},
            stream=True,
        )

        with self.assertRaises(litellm.Timeout):
            next(result)
        self.assertTrue(stream.closed)

    @tag("batch_event_llm")
    @override_settings(LITELLM_MAX_RETRIES=2, LITELLM_RETRY_BACKOFF_SECONDS=0)
    @patch("api.agent.core.llm_utils.litellm.completion")
    def test_retries_on_retryable_error(self, mock_completion):
        response = make_completion_response()
        mock_completion.side_effect = [litellm.Timeout("timeout", model="mock-model", llm_provider="mock"), response]

        result = run_completion(
            model="mock-model",
            messages=[],
            params={},
        )

        self.assertIs(result, response)
        self.assertEqual(mock_completion.call_count, 2)

    @tag("batch_event_llm")
    @override_settings(LITELLM_MAX_RETRIES=3, LITELLM_RETRY_BACKOFF_SECONDS=0)
    @patch("api.agent.core.llm_utils.litellm.completion")
    def test_does_not_retry_on_non_retryable_error(self, mock_completion):
        mock_completion.side_effect = ValueError("boom")

        with self.assertRaises(ValueError):
            run_completion(
                model="mock-model",
                messages=[],
                params={},
            )

        self.assertEqual(mock_completion.call_count, 1)

    @tag("batch_event_llm")
    @override_settings(LITELLM_MAX_RETRIES=2, LITELLM_RETRY_BACKOFF_SECONDS=0)
    @patch("api.agent.core.llm_utils.litellm.completion")
    def test_retries_on_empty_response(self, mock_completion):
        empty_message = SimpleNamespace(content="")
        empty_response = SimpleNamespace(choices=[SimpleNamespace(message=empty_message)])
        non_empty_response = make_completion_response(content="Hello")
        mock_completion.side_effect = [empty_response, non_empty_response]

        result = run_completion(
            model="mock-model",
            messages=[],
            params={},
        )

        self.assertIs(result, non_empty_response)
        self.assertEqual(mock_completion.call_count, 2)

    @tag("batch_event_llm")
    @patch("api.agent.core.llm_utils.litellm.completion")
    def test_image_response_with_message_images_is_not_empty(self, mock_completion):
        image_response = {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "images": [{"image_url": {"url": "data:image/png;base64,Zm9v"}}],
                    }
                }
            ]
        }
        mock_completion.return_value = image_response

        result = run_completion(
            model="mock-model",
            messages=[],
            params={},
        )

        self.assertIs(result, image_response)
        self.assertEqual(mock_completion.call_count, 1)

    @tag("batch_event_llm")
    @patch("api.agent.core.llm_utils.litellm.completion")
    def test_image_response_with_output_image_content_is_not_empty(self, mock_completion):
        image_response = {
            "choices": [
                {
                    "message": {
                        "content": [
                            {"type": "output_image", "image_url": {"url": "https://example.com/generated.png"}}
                        ]
                    }
                }
            ]
        }
        mock_completion.return_value = image_response

        result = run_completion(
            model="mock-model",
            messages=[],
            params={},
        )

        self.assertIs(result, image_response)
        self.assertEqual(mock_completion.call_count, 1)

    @tag("batch_event_llm")
    @override_settings(LITELLM_MAX_RETRIES=2, LITELLM_RETRY_BACKOFF_SECONDS=0)
    @patch("api.agent.core.llm_utils.litellm.completion")
    def test_retries_on_forbidden_marker_response(self, mock_completion):
        forbidden_response = make_completion_response(content="ok <\uFF5CDSML\uFF5Cfunction_calls>")
        valid_response = make_completion_response(content="All clear")
        mock_completion.side_effect = [forbidden_response, valid_response]

        result = run_completion(
            model="mock-model",
            messages=[],
            params={},
        )

        self.assertIs(result, valid_response)
        self.assertEqual(mock_completion.call_count, 2)

    @tag("batch_event_llm")
    @override_settings(LITELLM_MAX_RETRIES=1, LITELLM_RETRY_BACKOFF_SECONDS=0)
    @patch("api.agent.core.llm_utils.litellm.completion")
    def test_raises_on_forbidden_marker_response(self, mock_completion):
        forbidden_response = make_completion_response(content="ok <\uFF5CDSML\uFF5Cfunction_calls>")
        mock_completion.return_value = forbidden_response

        with self.assertRaises(InvalidLiteLLMResponseError):
            run_completion(
                model="mock-model",
                messages=[],
                params={},
            )

        self.assertEqual(mock_completion.call_count, 1)
