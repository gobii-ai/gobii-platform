from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import TestCase, override_settings, tag
import litellm

from api.agent.core.llm_utils import InvalidLiteLLMResponseError, run_completion
from tests.utils.token_usage import make_completion_response


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
        self.assertNotIn("supports_reasoning", kwargs)

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
