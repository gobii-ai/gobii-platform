"""
Unit tests for event processing LLM selection and token estimation.
"""
from datetime import timedelta
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone
from api.agent.core.event_processing import (
    _completion_with_failover,
    _get_recent_preferred_config,
)
from api.models import PersistentAgent, BrowserUseAgent, PersistentAgentCompletion


@tag("batch_event_llm")
class TestEventProcessingLLMSelection(TestCase):
    """Test LLM selection functionality in event processing."""

    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="llm-selection@example.com",
            email="llm-selection@example.com",
            password="secret",
        )
        self.browser_agent = BrowserUseAgent.objects.create(user=self.user, name="LLM BA")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="LLM Tester",
            charter="Ensure LLM selection helper works.",
            browser_use_agent=self.browser_agent,
        )

    @patch('api.agent.core.event_processing.get_llm_config_with_failover')
    @patch('api.agent.core.event_processing.litellm.completion')
    def test_completion_with_failover_uses_preselected_config(self, mock_completion, mock_get_config):
        """_completion_with_failover uses the failover_configs passed to it."""
        # This test ensures that _completion_with_failover does NOT call get_llm_config_with_failover itself.
        
        # Setup mocks
        failover_configs = [("google", "vertex_ai/gemini-2.5-pro", {"temperature": 0.1})]
        mock_completion.return_value = Mock()
        
        messages = [{"role": "user", "content": "Test message"}]
        tools = []
        
        _completion_with_failover(messages, tools, failover_configs=failover_configs, agent_id="test-agent")
        
        # Verify that get_llm_config_with_failover was NOT called inside _completion_with_failover
        mock_get_config.assert_not_called()
        
        # Verify that litellm.completion was called with the correct, pre-selected model
        mock_completion.assert_called_once()
        call_args = mock_completion.call_args
        self.assertEqual(call_args.kwargs['model'], "vertex_ai/gemini-2.5-pro")

    @patch('api.agent.core.event_processing.litellm.completion')
    def test_parallel_tool_calls_flag_is_passed(self, mock_completion):
        """_completion_with_failover passes parallel_tool_calls when endpoint enables it."""
        from unittest.mock import MagicMock
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_message = MagicMock()
        mock_message.content = "ok"
        setattr(mock_message, 'tool_calls', [])
        mock_choice.message = mock_message
        mock_response.choices = [mock_choice]
        mock_response.model_extra = {}
        mock_completion.return_value = mock_response

        messages = [{"role": "user", "content": "hello"}]
        tools = []
        # Provide endpoint params with our hint
        failover_configs = [
            (
                "openai",
                "openai/gpt-4.1",
                {
                    "temperature": 0.1,
                    "supports_tool_choice": True,
                    "use_parallel_tool_calls": True,
                    "supports_vision": True,
                },
            )
        ]

        from api.agent.core.event_processing import _completion_with_failover
        _completion_with_failover(messages, tools, failover_configs=failover_configs, agent_id="agent-1")

        self.assertTrue(mock_completion.called)
        kwargs = mock_completion.call_args.kwargs
        self.assertIn('parallel_tool_calls', kwargs)
        self.assertTrue(kwargs['parallel_tool_calls'])
        # drop_params helps avoid provider rejections
        self.assertIn('drop_params', kwargs)
        self.assertTrue(kwargs['drop_params'])

    @patch('api.agent.core.event_processing.run_completion')
    def test_completion_with_failover_prefers_explicit_provider(self, mock_run_completion):
        """Preferred provider should be attempted before standard ordering."""
        failover_configs = [
            ("default", "model-default", {}),
            ("preferred", "model-preferred", {}),
        ]
        messages = [{"role": "user", "content": "Hello"}]
        tools = []

        mock_response = Mock()
        mock_response.model_extra = {"usage": None}
        mock_run_completion.side_effect = [Exception("fail"), mock_response]

        response, token_usage = _completion_with_failover(
            messages,
            tools,
            failover_configs=failover_configs,
            agent_id=str(self.agent.id),
            preferred_config=("preferred", "model-preferred"),
        )

        self.assertEqual(mock_run_completion.call_count, 2)
        first_call = mock_run_completion.call_args_list[0]
        self.assertEqual(first_call.kwargs["model"], "model-preferred")
        self.assertIs(response, mock_response)
        self.assertEqual(token_usage["provider"], "default")
        self.assertEqual(token_usage["model"], "model-default")

    @patch('api.agent.core.event_processing.run_completion')
    def test_completion_with_failover_prefers_matching_model_identifier(self, mock_run_completion):
        """When the preferred identifier matches a model, it should be tried first."""
        failover_configs = [
            ("default", "model-default", {}),
            ("preferred", "model-preferred", {}),
        ]
        messages = [{"role": "user", "content": "Hello"}]
        tools = []

        mock_response = Mock()
        mock_response.model_extra = {"usage": None}
        mock_run_completion.return_value = mock_response

        _, token_usage = _completion_with_failover(
            messages,
            tools,
            failover_configs=failover_configs,
            agent_id=str(self.agent.id),
            preferred_config=("preferred", "model-preferred"),
        )

        first_call = mock_run_completion.call_args_list[0]
        self.assertEqual(first_call.kwargs["model"], "model-preferred")
        self.assertEqual(token_usage["provider"], "preferred")
        self.assertEqual(token_usage["model"], "model-preferred")

    def test_get_recent_preferred_config_uses_recent_completion(self):
        """Helper should return (provider, model) for a fresh completion."""
        PersistentAgentCompletion.objects.create(
            agent=self.agent,
            llm_model="model-preferred",
            llm_provider="preferred",
        )

        preferred = _get_recent_preferred_config(self.agent, run_sequence_number=3)
        self.assertEqual(preferred, ("preferred", "model-preferred"))

    def test_get_recent_preferred_config_requires_both_fields(self):
        """Helper should return None when it cannot determine both provider and model."""
        PersistentAgentCompletion.objects.create(
            agent=self.agent,
            llm_provider="preferred",
        )

        preferred = _get_recent_preferred_config(self.agent, run_sequence_number=3)
        self.assertIsNone(preferred)

    def test_get_recent_preferred_config_ignores_stale_completion(self):
        """Helper should ignore cached providers older than the freshness window."""
        completion = PersistentAgentCompletion.objects.create(
            agent=self.agent,
            llm_model="model-preferred",
            llm_provider="preferred",
        )
        PersistentAgentCompletion.objects.filter(id=completion.id).update(
            created_at=timezone.now() - timedelta(hours=2),
        )

        preferred = _get_recent_preferred_config(self.agent, run_sequence_number=3)
        self.assertIsNone(preferred)

    def test_get_recent_preferred_config_skips_on_second_run(self):
        """Second run should never use preferred provider, even if fresh."""
        PersistentAgentCompletion.objects.create(
            agent=self.agent,
            llm_model="model-preferred",
            llm_provider="preferred",
        )

        preferred = _get_recent_preferred_config(self.agent, run_sequence_number=2)
        self.assertIsNone(preferred)

    @patch('api.agent.core.event_processing.settings.MAX_PREFERRED_PROVIDER_STREAK', 3)
    def test_get_recent_preferred_config_skips_when_streak_limit_hit(self):
        """Helper should stop preferring a provider once the streak limit is reached."""
        for _ in range(3):
            PersistentAgentCompletion.objects.create(
                agent=self.agent,
                llm_model="model-preferred",
                llm_provider="preferred",
            )

        preferred = _get_recent_preferred_config(self.agent, run_sequence_number=3)
        self.assertIsNone(preferred)

    @patch('api.agent.core.event_processing.settings.MAX_PREFERRED_PROVIDER_STREAK', 3)
    def test_get_recent_preferred_config_allows_under_limit(self):
        """Helper should still return the preference when streak is below the limit."""
        for _ in range(2):
            PersistentAgentCompletion.objects.create(
                agent=self.agent,
                llm_model="model-preferred",
                llm_provider="preferred",
            )

        preferred = _get_recent_preferred_config(self.agent, run_sequence_number=3)
        self.assertEqual(preferred, ("preferred", "model-preferred"))
