"""
Unit tests for event processing LLM selection and token estimation.
"""
import os
from unittest import mock
from unittest.mock import Mock, patch

from django.test import TestCase, tag
from django.utils import timezone
from api.agent.core.event_processing import (
    _estimate_message_tokens,
    _estimate_agent_context_tokens,
    _completion_with_failover,
)
from api.models import PersistentAgent


@tag("batch_event_llm")
class TestEventProcessingLLMSelection(TestCase):
    """Test LLM selection functionality in event processing."""

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
