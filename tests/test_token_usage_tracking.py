"""Test token usage tracking in persistent agent steps."""
import json
from unittest.mock import patch, MagicMock
from django.test import TestCase, tag
from api.models import PersistentAgent, PersistentAgentStep, PersistentAgentToolCall
from api.agent.core.event_processing import _completion_with_failover

@tag("batch_token_usage")
class TokenUsageTrackingTest(TestCase):
    """Test that token usage is properly tracked and stored."""
    
    def setUp(self):
        """Set up test data."""
        self.agent = PersistentAgent.objects.create(
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
    
    def test_step_creation_with_token_usage(self):
        """Test that steps are created with token usage fields."""
        # Create a step with token usage
        step = PersistentAgentStep.objects.create(
            agent=self.agent,
            description="Test step with token usage",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            cached_tokens=25,
            llm_model="gpt-4",
            llm_provider="openai"
        )
        
        # Reload from database
        step.refresh_from_db()
        
        # Verify fields are saved
        self.assertEqual(step.prompt_tokens, 100)
        self.assertEqual(step.completion_tokens, 50)
        self.assertEqual(step.total_tokens, 150)
        self.assertEqual(step.cached_tokens, 25)
        self.assertEqual(step.llm_model, "gpt-4")
        self.assertEqual(step.llm_provider, "openai")
    
    def test_tool_call_step_with_token_usage(self):
        """Test that tool call steps include token usage."""
        # Create a tool call step with token usage
        step = PersistentAgentStep.objects.create(
            agent=self.agent,
            description="Tool call: test_tool",
            prompt_tokens=200,
            completion_tokens=100,
            total_tokens=300,
            llm_model="claude-3-opus",
            llm_provider="anthropic"
        )
        
        # Create associated tool call
        tool_call = PersistentAgentToolCall.objects.create(
            step=step,
            tool_name="test_tool",
            tool_params={"param": "value"},
            result=json.dumps({"status": "success"})
        )
        
        # Verify token usage is associated with the step
        self.assertEqual(step.prompt_tokens, 200)
        self.assertEqual(step.completion_tokens, 100)
        self.assertEqual(step.total_tokens, 300)
        self.assertEqual(step.llm_model, "claude-3-opus")
        self.assertEqual(step.llm_provider, "anthropic")
    
    def test_aggregate_token_usage_for_agent(self):
        """Test aggregating token usage across all steps for an agent."""
        # Create multiple steps with token usage
        PersistentAgentStep.objects.create(
            agent=self.agent,
            description="Step 1",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150
        )
        
        PersistentAgentStep.objects.create(
            agent=self.agent,
            description="Step 2",
            prompt_tokens=200,
            completion_tokens=100,
            total_tokens=300
        )
        
        PersistentAgentStep.objects.create(
            agent=self.agent,
            description="Step 3 (no tokens)",  # Step without token usage
        )
        
        # Aggregate token usage
        from django.db.models import Sum
        totals = PersistentAgentStep.objects.filter(
            agent=self.agent
        ).aggregate(
            total_prompt_tokens=Sum('prompt_tokens'),
            total_completion_tokens=Sum('completion_tokens'),
            total_all_tokens=Sum('total_tokens')
        )
        
        # Verify aggregation
        self.assertEqual(totals['total_prompt_tokens'], 300)
        self.assertEqual(totals['total_completion_tokens'], 150)
        self.assertEqual(totals['total_all_tokens'], 450)


if __name__ == '__main__':
    import django
    django.setup()
    import unittest
    unittest.main()