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


class _AsyncClosableBlockingFirstChunkStream(_BlockingFirstChunkStream):
    async def aclose(self):
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
    def test_first_party_openai_uses_litellm_responses_bridge(self, mock_completion):
        response = make_completion_response(content="Final answer")
        mock_completion.return_value = response

        result = run_completion(
            model="openai/gpt-5",
            messages=[{"role": "user", "content": "Hello"}],
            params={"api_key": "sk-test", "supports_reasoning": True, "reasoning_effort": "low"},
        )

        self.assertIs(result, response)
        _, kwargs = mock_completion.call_args
        self.assertEqual(kwargs["model"], "openai/responses/gpt-5")
        self.assertEqual(kwargs["messages"], [{"role": "user", "content": "Hello"}])
        self.assertEqual(kwargs["api_key"], "sk-test")
        self.assertEqual(kwargs["reasoning_effort"], {"summary": "detailed", "effort": "low"})
        self.assertEqual(kwargs["extra_body"]["reasoning_effort"], {"summary": "detailed", "effort": "low"})

    @tag("batch_event_llm")
    @patch("api.agent.core.llm_utils.litellm.completion")
    def test_openai_compatible_api_base_stays_on_completion_model(self, mock_completion):
        response = make_completion_response(content="compat")
        mock_completion.return_value = response

        result = run_completion(
            model="openai/custom-model",
            messages=[{"role": "user", "content": "Hello"}],
            params={"api_key": "sk-test", "api_base": "https://proxy.example/v1", "supports_reasoning": True},
        )

        self.assertIs(result, response)
        _, kwargs = mock_completion.call_args
        self.assertEqual(kwargs["model"], "openai/custom-model")
        self.assertEqual(kwargs["api_base"], "https://proxy.example/v1")

    @tag("batch_event_llm")
    @patch("api.agent.core.llm_utils.litellm.completion")
    def test_azure_responses_uses_litellm_responses_bridge(self, mock_completion):
        response = make_completion_response(content="Azure answer")
        mock_completion.return_value = response

        result = run_completion(
            model="azure/responses/gpt-5-deployment",
            messages=[{"role": "user", "content": "Hello"}],
            params={
                "api_key": "azure-key",
                "api_base": "https://example.openai.azure.com",
                "api_version": "v1",
                "custom_llm_provider": "azure",
                "supports_reasoning": True,
                "reasoning_effort": "low",
            },
        )

        self.assertIs(result, response)
        _, kwargs = mock_completion.call_args
        self.assertEqual(kwargs["model"], "azure/responses/gpt-5-deployment")
        self.assertEqual(kwargs["api_base"], "https://example.openai.azure.com")
        self.assertEqual(kwargs["api_version"], "v1")
        self.assertEqual(kwargs["api_key"], "azure-key")
        self.assertEqual(kwargs["custom_llm_provider"], "azure")
        self.assertEqual(kwargs["reasoning_effort"], {"summary": "detailed", "effort": "low"})
        self.assertEqual(kwargs["extra_body"]["reasoning_effort"], {"summary": "detailed", "effort": "low"})

    @tag("batch_event_llm")
    @patch("api.agent.core.llm_utils.litellm.completion")
    def test_azure_non_openai_model_uses_chat_completions_with_reasoning_effort(self, mock_completion):
        response = make_completion_response(content="Azure DeepSeek answer")
        mock_completion.return_value = response

        result = run_completion(
            model="azure/deepseek-v4-flash",
            messages=[{"role": "user", "content": "Hello"}],
            params={
                "api_key": "azure-key",
                "api_base": "https://example.services.ai.azure.com",
                "api_version": "v1",
                "custom_llm_provider": "azure",
                "supports_reasoning": True,
                "reasoning_effort": "medium",
            },
        )

        self.assertIs(result, response)
        _, kwargs = mock_completion.call_args
        self.assertEqual(kwargs["model"], "azure/deepseek-v4-flash")
        self.assertEqual(kwargs["reasoning_effort"], "medium")
        self.assertEqual(kwargs["allowed_openai_params"], ["reasoning_effort"])
        self.assertNotIn("extra_body", kwargs)

    @tag("batch_event_llm")
    @patch("api.agent.core.llm_utils.litellm.completion")
    def test_azure_non_openai_model_respects_reasoning_effort_none(self, mock_completion):
        response = make_completion_response(content="Azure DeepSeek answer")
        mock_completion.return_value = response

        run_completion(
            model="azure/deepseek-v4-flash",
            messages=[{"role": "user", "content": "Hello"}],
            params={
                "api_key": "azure-key",
                "api_base": "https://example.services.ai.azure.com",
                "api_version": "v1",
                "custom_llm_provider": "azure",
                "supports_reasoning": True,
                "reasoning_effort": "none",
            },
        )

        _, kwargs = mock_completion.call_args
        self.assertEqual(kwargs["model"], "azure/deepseek-v4-flash")
        self.assertNotIn("reasoning_effort", kwargs)
        self.assertNotIn("extra_body", kwargs)
        self.assertNotIn("allowed_openai_params", kwargs)

    @tag("batch_event_llm")
    @patch("api.agent.core.llm_utils.litellm.completion")
    def test_responses_bridge_converts_forced_function_tool_choice(self, mock_completion):
        response = make_completion_response(content="ok")
        mock_completion.return_value = response

        run_completion(
            model="azure/responses/gpt-5-deployment",
            messages=[{"role": "user", "content": "Hello"}],
            params={
                "api_key": "azure-key",
                "api_base": "https://example.openai.azure.com",
                "api_version": "v1",
                "custom_llm_provider": "azure",
            },
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "enable_system_skills",
                        "description": "Enable system skills",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
            tool_choice={"type": "function", "function": {"name": "enable_system_skills"}},
        )

        _, kwargs = mock_completion.call_args
        self.assertEqual(kwargs["tool_choice"], {"type": "function", "function": {"name": "enable_system_skills"}})
        self.assertEqual(kwargs["extra_body"]["tool_choice"], {"type": "function", "name": "enable_system_skills"})

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
    @patch("api.agent.core.llm_utils.litellm.completion")
    def test_parallel_tool_calls_omitted_when_tools_absent(self, mock_completion):
        mock_completion.return_value = make_completion_response(content="ok")

        run_completion(
            model="mock-model",
            messages=[],
            params={"use_parallel_tool_calls": True},
        )

        _, kwargs = mock_completion.call_args
        self.assertNotIn("parallel_tool_calls", kwargs)
        self.assertNotIn("use_parallel_tool_calls", kwargs)

    @tag("batch_event_llm")
    @patch("api.agent.core.llm_utils.litellm.completion")
    def test_parallel_tool_calls_forwarded_when_tools_present(self, mock_completion):
        mock_completion.return_value = make_completion_response(content="ok")

        run_completion(
            model="mock-model",
            messages=[],
            params={"use_parallel_tool_calls": True},
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "search",
                        "description": "Search",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
        )

        _, kwargs = mock_completion.call_args
        self.assertTrue(kwargs["parallel_tool_calls"])
        self.assertNotIn("use_parallel_tool_calls", kwargs)

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
    @override_settings(LITELLM_MAX_RETRIES=1)
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
    @override_settings(LITELLM_MAX_RETRIES=2, LITELLM_RETRY_BACKOFF_SECONDS=0)
    @patch("api.agent.core.llm_utils.get_litellm_first_data_timeout_seconds", return_value=0.01)
    @patch("api.agent.core.llm_utils.litellm.completion")
    def test_streaming_first_chunk_timeout_uses_retry_budget(
        self,
        mock_completion,
        _mock_first_data_timeout,
    ):
        stalled_stream = _BlockingFirstChunkStream()
        retry_stream = _ClosableStream(["retry-chunk"])
        mock_completion.side_effect = [stalled_stream, retry_stream]

        result = run_completion(
            model="mock-model",
            messages=[],
            params={},
            stream=True,
        )

        self.assertEqual(next(result), "retry-chunk")
        self.assertTrue(stalled_stream.closed)
        self.assertEqual(mock_completion.call_count, 2)

    @tag("batch_event_llm")
    @override_settings(LITELLM_MAX_RETRIES=3, LITELLM_RETRY_BACKOFF_SECONDS=0)
    @patch("api.agent.core.llm_utils.get_litellm_first_data_timeout_seconds", return_value=0.01)
    @patch("api.agent.core.llm_utils.litellm.completion")
    def test_streaming_retry_budget_covers_replacement_stream_creation(
        self,
        mock_completion,
        _mock_first_data_timeout,
    ):
        stalled_stream = _BlockingFirstChunkStream()
        retry_stream = _ClosableStream(["retry-chunk"])
        mock_completion.side_effect = [
            stalled_stream,
            litellm.Timeout("timeout", model="mock-model", llm_provider="mock"),
            retry_stream,
        ]

        result = run_completion(
            model="mock-model",
            messages=[],
            params={},
            stream=True,
        )

        self.assertEqual(next(result), "retry-chunk")
        self.assertTrue(stalled_stream.closed)
        self.assertEqual(mock_completion.call_count, 3)

    @tag("batch_event_llm")
    @override_settings(LITELLM_MAX_RETRIES=1)
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
    @override_settings(LITELLM_MAX_RETRIES=1)
    @patch("api.agent.core.llm_utils.get_litellm_first_data_timeout_seconds", return_value=0.01)
    @patch("api.agent.core.llm_utils.litellm.completion")
    def test_streaming_first_chunk_timeout_async_closes_underlying_stream(
        self,
        mock_completion,
        _mock_first_data_timeout,
    ):
        stream = _AsyncClosableBlockingFirstChunkStream()
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
