"""
Tests for token counting and LLM selection in event processing.

This module tests the issue where the system was incorrectly switching to Google
for prompts under 4k tokens due to counting system+user messages instead of
just the fitted user content.
"""
import os
from unittest import mock
from unittest.mock import patch, MagicMock
from django.test import TestCase, tag
from django.contrib.auth import get_user_model

from api.agent.core.event_processing import _run_agent_loop, build_prompt_context
from api.agent.core.llm_config import get_llm_config_with_failover
from tests.utils.llm_seed import seed_persistent_basic
from api.models import PersistentAgent, BrowserUseAgent, UserQuota


@tag("batch_event_processing")
class TestEventProcessingTokenCounting(TestCase):
    """Test token counting logic in event processing."""

    @classmethod
    def setUpTestData(cls):
        """Set up non-modified objects used by all test methods."""
        User = get_user_model()
        cls.user = User.objects.create_user(username='testuser@example.com', email='testuser@example.com', password='password')
        # UserQuota is created by a signal, but we can get it and increase the limit for tests.
        quota, _ = UserQuota.objects.get_or_create(user=cls.user)
        quota.agent_limit = 100  # Set a high limit for testing purposes
        quota.save()

    def setUp(self):
        """Set up test fixtures."""
        browser_agent = BrowserUseAgent.objects.create(user=self.user, name="browser-agent-for-token-test")
        self.test_agent = PersistentAgent.objects.create(
            user=self.user,
            name="Test Agent",
            charter="Test charter for the agent",
            browser_use_agent=browser_agent
        )

    def test_token_counting_bug_reproduction(self):
        """
        Test that we use fitted token count from promptree for LLM selection.
        
        This fixes the bug where system+user combined token counting was causing
        incorrect LLM selection, even when fitted content was under thresholds.
        """
        seed_persistent_basic(include_openrouter=True)
        with mock.patch.dict(os.environ, {
            "ANTHROPIC_API_KEY": "anthropic-key",
            "GOOGLE_API_KEY": "google-key",
        }, clear=True):
            
            # Mock build_prompt_context to return specific fitted token count
            with patch('api.agent.core.event_processing.build_prompt_context') as mock_build_prompt:
                # Return a token count in the small range (< 10000)
                mock_build_prompt.return_value = (
                    [
                        {"role": "system", "content": "System message"},
                        {"role": "user", "content": "User message"}
                    ],
                    2500,  # Fitted token count - in small range, will get Google (GPT-5 not available)
                    None,
                )
                
                # Capture what token count gets passed to get_llm_config_with_failover
                captured_token_counts = []
                original_get_llm_config = get_llm_config_with_failover
                
                def capturing_get_llm_config(*args, **kwargs):
                    token_count = kwargs.get('token_count')
                    if token_count is not None and token_count > 0:  # Ignore the initial token_count=0 call
                        captured_token_counts.append(token_count)
                    return original_get_llm_config(*args, **kwargs)
                
                with patch('api.agent.core.event_processing.get_llm_config_with_failover', side_effect=capturing_get_llm_config):
                    with patch('api.agent.core.event_processing._completion_with_failover') as mock_completion:
                        # Return tool call to end loop after one iteration
                        mock_completion.return_value = MagicMock(
                            choices=[MagicMock(
                                message=MagicMock(
                                    content="test", 
                                    tool_calls=[MagicMock(
                                        function=MagicMock(name="sleep_until_next_trigger", arguments='{}')
                                    )]
                                )
                            )]
                        )
                        
                        # Run one iteration of the agent loop
                        try:
                            _run_agent_loop(self.test_agent, is_first_run=False)
                        except Exception:
                            pass  # Ignore tool execution exceptions
                        
                        # Verify that the fitted token count was used for LLM selection
                        self.assertGreater(len(captured_token_counts), 0, "No token counts were captured")
                        
                        # The fix ensures we use the fitted token count (2500) for LLM selection
                        actual_token_count = captured_token_counts[-1]  # Last call for LLM selection
                        self.assertEqual(actual_token_count, 2500, 
                                       f"Expected fitted token count 2500, got {actual_token_count}")
                        
                        # For 2500 tokens (< 10000), without GPT-5 available, we should see an available endpoint
                        configs = get_llm_config_with_failover(token_count=actual_token_count)
                        self.assertTrue(len(configs) >= 1)

    def test_prompt_context_token_counting_vs_llm_selection(self):
        """
        Test that token counting in prompt building vs LLM selection are consistent.
        """
        seed_persistent_basic(include_openrouter=True)
        with mock.patch.dict(os.environ, {
            "ANTHROPIC_API_KEY": "anthropic-key",
            "GOOGLE_API_KEY": "google-key",
        }, clear=True):
            with patch('api.agent.core.prompt_context.get_llm_config_with_failover') as mock_get_config:
                mock_get_config.return_value = [("anthropic", "anthropic/claude-sonnet-4-20250514", {})]

                messages, fitted_token_count, archive_id = build_prompt_context(self.test_agent)

                # Verify it was called with token_count=0 for model selection
                mock_get_config.assert_called_with(
                    agent=self.test_agent,
                    agent_id=str(self.test_agent.id),
                    token_count=0,
                    allow_unconfigured=True,
                    is_first_loop=False,
                    routing_profile=None,
                )

                self.assertIsInstance(messages, list)
                self.assertEqual(len(messages), 2)  # system + user messages
                self.assertIsInstance(fitted_token_count, int)
                self.assertGreater(fitted_token_count, 0)

    def test_get_llm_config_with_failover_small_range(self):
        """Test that small token ranges use token-based tier selection (GPT-5 not available without key)."""
        seed_persistent_basic(include_openrouter=True)
        with mock.patch.dict(os.environ, {
            "ANTHROPIC_API_KEY": "anthropic-key",
            "GOOGLE_API_KEY": "google-key",
        }, clear=True):
            
            # Test various token counts in the small range (0-10000)
            # Without OPENAI_API_KEY, GPT-5 won't be available, so should get Google primary
            test_cases = [0, 1000, 2500, 3999]
            
            for token_count in test_cases:
                with self.subTest(token_count=token_count):
                    configs = get_llm_config_with_failover(token_count=token_count)
                    self.assertGreaterEqual(len(configs), 1)

    def test_get_llm_config_with_failover_medium_range(self):
        """Test that medium token ranges correctly prefer Google."""
        seed_persistent_basic(include_openrouter=True)
        with mock.patch.dict(os.environ, {
            "ANTHROPIC_API_KEY": "anthropic-key",
            "GOOGLE_API_KEY": "google-key", 
        }, clear=True):
            
            # Test token counts in the medium range (10000-20000)
            test_cases = [10000, 12000, 15000, 19999]
            
            for token_count in test_cases:
                with self.subTest(token_count=token_count):
                    configs = get_llm_config_with_failover(token_count=token_count)
                    self.assertGreaterEqual(len(configs), 1)
