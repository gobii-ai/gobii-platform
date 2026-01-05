from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings, tag
import litellm

from api.agent.core.llm_utils import run_completion


class RunCompletionReasoningTests(SimpleTestCase):
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
        response = Mock()
        mock_completion.side_effect = [litellm.Timeout("timeout"), response]

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
