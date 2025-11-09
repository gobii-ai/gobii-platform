"""
Unit tests for event processing LLM selection and token estimation.
"""
import os
from datetime import timedelta
from unittest import mock
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone
from api.agent.core.event_processing import (
    _estimate_message_tokens,
    _estimate_agent_context_tokens,
    _completion_with_failover,
    _get_recent_preferred_provider,
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

        _completion_with_failover(
            messages,
            tools,
            failover_configs=failover_configs,
            agent_id=str(self.agent.id),
            preferred_provider="preferred",
        )

        self.assertEqual(mock_run_completion.call_count, 2)
        first_call = mock_run_completion.call_args_list[0]
        self.assertEqual(first_call.kwargs["model"], "model-preferred")

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

        _completion_with_failover(
            messages,
            tools,
            failover_configs=failover_configs,
            agent_id=str(self.agent.id),
            preferred_provider="model-preferred",
        )

        first_call = mock_run_completion.call_args_list[0]
        self.assertEqual(first_call.kwargs["model"], "model-preferred")

    def test_get_recent_preferred_provider_uses_recent_completion(self):
        """Helper should return provider for a completion recorded within the allowed window."""
        PersistentAgentCompletion.objects.create(
            agent=self.agent,
            llm_model="model-preferred",
            llm_provider="preferred",
        )
        failover_configs = [
            ("default", "model-default", {}),
            ("preferred", "model-preferred", {}),
        ]

        provider = _get_recent_preferred_provider(self.agent, failover_configs)
        self.assertEqual(provider, "preferred")

    def test_get_recent_preferred_provider_ignores_stale_completion(self):
        """Helper should ignore cached providers older than the freshness window."""
        completion = PersistentAgentCompletion.objects.create(
            agent=self.agent,
            llm_model="model-preferred",
            llm_provider="preferred",
        )
        PersistentAgentCompletion.objects.filter(id=completion.id).update(
            created_at=timezone.now() - timedelta(hours=2),
        )
        failover_configs = [
            ("default", "model-default", {}),
            ("preferred", "model-preferred", {}),
        ]

        provider = _get_recent_preferred_provider(self.agent, failover_configs)
        self.assertIsNone(provider)
