"""
Unit tests for event processing LLM selection and token estimation.
"""
import os
from unittest import mock
from unittest.mock import Mock, patch

from django.test import TestCase
from django.utils import timezone
from api.agent.core.event_processing import (
    _estimate_message_tokens,
    _estimate_agent_context_tokens,
    _completion_with_failover,
)
from api.models import PersistentAgent


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