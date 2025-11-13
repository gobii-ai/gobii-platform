"""Test token usage tracking in persistent agent steps."""
import json
from decimal import Decimal
from unittest.mock import patch, MagicMock
from django.test import TestCase, tag
from django.contrib.auth import get_user_model
from api.models import (
    PersistentAgent,
    PersistentAgentCompletion,
    PersistentAgentStep,
    PersistentAgentToolCall,
    BrowserUseAgent,
)
from api.agent.core.event_processing import _completion_with_failover

User = get_user_model()

@tag("batch_token_usage")
class TokenUsageTrackingTest(TestCase):
    """Test that token usage is properly tracked and stored."""
    
    def setUp(self):
        """Set up test data."""
        # Create a test user
        self.user = User.objects.create_user(
            username="testuser",
            email="test@example.com",
            password="testpass123"
        )
        
        # Create a BrowserUseAgent
        self.browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="Test Browser Agent"
        )
        
        # Create the PersistentAgent with required fields
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            browser_use_agent=self.browser_agent,
            name="Test Agent",
            charter="Test charter"
        )
    
    def test_completion_with_failover_returns_token_usage(self):
        """Test that _completion_with_failover returns token usage data."""
        # Mock the litellm completion response
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message = MagicMock(content="Test response")
        mock_response.model_extra = {
            "usage": MagicMock(
                prompt_tokens=100,
                completion_tokens=50,
                total_tokens=150,
                prompt_tokens_details=MagicMock(cached_tokens=25)
            )
        }
        
        with patch('api.agent.core.event_processing.litellm.completion') as mock_completion:
            mock_completion.return_value = mock_response
            
            # Call the function
            response, token_usage = _completion_with_failover(
                messages=[{"role": "user", "content": "Test"}],
                tools=[],
                failover_configs=[("test_provider", "test_model", {})],
                agent_id=str(self.agent.id)
            )
            
            # Verify token usage is returned
            self.assertIsNotNone(token_usage)
            self.assertEqual(token_usage["prompt_tokens"], 100)
            self.assertEqual(token_usage["completion_tokens"], 50)
            self.assertEqual(token_usage["total_tokens"], 150)
            self.assertEqual(token_usage["cached_tokens"], 25)
            self.assertEqual(token_usage["model"], "test_model")
            self.assertEqual(token_usage["provider"], "test_provider")
    
    def test_completion_model_persists_token_usage(self):
        """Ensure PersistentAgentCompletion stores token fields."""
        completion = PersistentAgentCompletion.objects.create(
            agent=self.agent,
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            cached_tokens=25,
            llm_model="gpt-4",
            llm_provider="openai",
            billed=True,
            input_cost_total=Decimal("0.000175"),
            input_cost_uncached=Decimal("0.000150"),
            input_cost_cached=Decimal("0.000025"),
            output_cost=Decimal("0.000200"),
            total_cost=Decimal("0.000375"),
        )
        completion.refresh_from_db()
        self.assertEqual(completion.prompt_tokens, 100)
        self.assertEqual(completion.completion_tokens, 50)
        self.assertEqual(completion.total_tokens, 150)
        self.assertEqual(completion.cached_tokens, 25)
        self.assertEqual(completion.llm_model, "gpt-4")
        self.assertEqual(completion.llm_provider, "openai")
        self.assertEqual(completion.input_cost_total, Decimal("0.000175"))
        self.assertEqual(completion.input_cost_uncached, Decimal("0.000150"))
        self.assertEqual(completion.input_cost_cached, Decimal("0.000025"))
        self.assertEqual(completion.output_cost, Decimal("0.000200"))
        self.assertEqual(completion.total_cost, Decimal("0.000375"))

    def test_step_links_to_completion(self):
        """Steps should reference a single completion record."""
        completion = PersistentAgentCompletion.objects.create(
            agent=self.agent,
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            llm_model="gpt-4o",
            llm_provider="openai",
            billed=True,
        )
        step = PersistentAgentStep.objects.create(
            agent=self.agent,
            description="Reasoning step",
            completion=completion,
        )
        step.refresh_from_db()
        self.assertEqual(step.completion_id, completion.id)
        self.assertEqual(completion.steps.count(), 1)

    def test_tool_call_step_links_completion(self):
        """Tool call metadata should still be accessible via the completion."""
        completion = PersistentAgentCompletion.objects.create(
            agent=self.agent,
            prompt_tokens=200,
            completion_tokens=100,
            total_tokens=300,
            llm_model="claude-3-opus",
            llm_provider="anthropic",
            billed=True,
        )
        step = PersistentAgentStep.objects.create(
            agent=self.agent,
            description="Tool call: test_tool",
            completion=completion,
        )
        PersistentAgentToolCall.objects.create(
            step=step,
            tool_name="test_tool",
            tool_params={"param": "value"},
            result=json.dumps({"status": "success"}),
        )
        self.assertEqual(step.completion.llm_model, "claude-3-opus")
        self.assertEqual(step.completion.total_tokens, 300)

    @patch("api.agent.core.event_processing.litellm.get_model_info")
    def test_cost_fields_populated_from_litellm(self, mock_get_model_info):
        """_completion_with_failover should include cost breakdown when pricing exists."""
        mock_get_model_info.return_value = {
            "input_cost_per_token": 0.000002,
            "cache_read_input_token_cost": 0.000001,
            "output_cost_per_token": 0.000004,
        }

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message = MagicMock(content="Cost test")
        usage_details = MagicMock(cached_tokens=25)
        mock_response.model_extra = {
            "usage": MagicMock(
                prompt_tokens=100,
                completion_tokens=50,
                total_tokens=150,
                prompt_tokens_details=usage_details,
            )
        }

        with patch("api.agent.core.event_processing.litellm.completion") as mock_completion:
            mock_completion.return_value = mock_response
            response, token_usage = _completion_with_failover(
                messages=[{"role": "user", "content": "Cost please"}],
                tools=[],
                failover_configs=[("openai", "openai/gpt-4o", {})],
                agent_id=str(self.agent.id),
            )

        self.assertIsNotNone(response)
        self.assertEqual(token_usage["input_cost_total"], Decimal("0.000175"))
        self.assertEqual(token_usage["input_cost_uncached"], Decimal("0.000150"))
        self.assertEqual(token_usage["input_cost_cached"], Decimal("0.000025"))
        self.assertEqual(token_usage["output_cost"], Decimal("0.000200"))
        self.assertEqual(token_usage["total_cost"], Decimal("0.000375"))
        mock_get_model_info.assert_called()

    @patch("api.agent.core.event_processing.litellm.get_model_info")
    def test_cost_fields_handle_non_numeric_usage(self, mock_get_model_info):
        """Token usage values that aren't numeric (e.g. MagicMocks) should not crash cost calc."""
        mock_get_model_info.return_value = {
            "input_cost_per_token": 0.000002,
            "cache_read_input_token_cost": 0.000001,
            "output_cost_per_token": 0.000004,
        }

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message = MagicMock(content="Mocky")
        usage = MagicMock()
        # Leave prompt/completion tokens as MagicMocks (default) to mimic upstream tests
        mock_response.model_extra = {"usage": usage}

        with patch("api.agent.core.event_processing.litellm.completion") as mock_completion:
            mock_completion.return_value = mock_response
            response, token_usage = _completion_with_failover(
                messages=[{"role": "user", "content": "Hi"}],
                tools=[],
                failover_configs=[("openai-provider", "openai/gpt-4o-mini", {})],
                agent_id=str(self.agent.id),
            )

        self.assertIsNotNone(response)
        self.assertEqual(token_usage.get("total_cost"), Decimal("0.000000"))

    def test_aggregate_token_usage_for_agent(self):
        """Test aggregating token usage across all completions for an agent."""
        from django.db.models import Sum

        PersistentAgentCompletion.objects.create(
            agent=self.agent,
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            billed=True,
        )
        PersistentAgentCompletion.objects.create(
            agent=self.agent,
            prompt_tokens=200,
            completion_tokens=100,
            total_tokens=300,
            billed=True,
        )
        PersistentAgentCompletion.objects.create(
            agent=self.agent,
            billed=True,
        )

        totals = PersistentAgentCompletion.objects.filter(agent=self.agent).aggregate(
            total_prompt_tokens=Sum("prompt_tokens"),
            total_completion_tokens=Sum("completion_tokens"),
            total_all_tokens=Sum("total_tokens"),
        )
        self.assertEqual(totals["total_prompt_tokens"], 300)
        self.assertEqual(totals["total_completion_tokens"], 150)
        self.assertEqual(totals["total_all_tokens"], 450)


if __name__ == '__main__':
    import django
    django.setup()
    import unittest
    unittest.main()
