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
    BrowserUseAgentTask,
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

    @patch("api.agent.core.event_processing.litellm.completion")
    @patch("api.agent.core.event_processing.litellm.get_model_info")
    def test_pricing_hint_uses_custom_provider(self, mock_get_model_info, mock_completion):
        """Ensure pricing hints (e.g., vertex_ai) drive LiteLLM pricing lookup."""

        def _model_info_side_effect(model=None, custom_llm_provider=None, **_kwargs):
            if custom_llm_provider == "vertex_ai":
                return {
                    "input_cost_per_token": 0.000001,
                    "cache_read_input_token_cost": 0.0,
                    "output_cost_per_token": 0.000002,
                }
            raise ValueError("unexpected provider")

        mock_get_model_info.side_effect = _model_info_side_effect

        usage_details = MagicMock(cached_tokens=None)
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message = MagicMock(content="Hinted cost")
        mock_response.model_extra = {
            "usage": MagicMock(
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
                prompt_tokens_details=usage_details,
            )
        }
        mock_completion.return_value = mock_response

        _response, token_usage = _completion_with_failover(
            messages=[{"role": "user", "content": "Hello"}],
            tools=[],
            failover_configs=[("endpoint-1", "gemini-1.5-pro", {"pricing_provider_hint": "vertex_ai"})],
            agent_id=str(self.agent.id),
        )

        self.assertEqual(token_usage["total_cost"], Decimal("0.000020"))
        self.assertEqual(token_usage.get("pricing_provider_hint"), "vertex_ai")
        called_with_hint = any(
            kwargs.get("custom_llm_provider") == "vertex_ai"
            for _args, kwargs in mock_get_model_info.call_args_list
        )
        self.assertTrue(called_with_hint)

    def test_browser_task_cost_fields_persist(self):
        """Browser-use tasks should store the cost breakdown returned by the agent run."""
        from api.tasks.browser_agent_tasks import _process_browser_use_task_core

        task = BrowserUseAgentTask.objects.create(
            agent=self.browser_agent,
            user=self.user,
            prompt="Track costs",
        )

        token_usage = {
            "prompt_tokens": 120,
            "completion_tokens": 30,
            "total_tokens": 150,
            "cached_tokens": 20,
            "model": "openai/gpt-4o",
            "provider": "openai",
            "input_cost_total": 0.001234,
            "input_cost_uncached": 0.001000,
            "input_cost_cached": 0.000234,
            "output_cost": 0.000800,
            "total_cost": 0.002034,
        }

        with patch("api.tasks.browser_agent_tasks.LIBS_AVAILABLE", True), \
             patch("api.tasks.browser_agent_tasks.Controller"), \
             patch("api.tasks.browser_agent_tasks.select_proxy_for_task", return_value=None), \
             patch("api.tasks.browser_agent_tasks.close_old_connections"), \
             patch("api.tasks.browser_agent_tasks._execute_agent_with_failover", return_value=({"done": True}, token_usage)):

            _process_browser_use_task_core(str(task.id))

        task.refresh_from_db()
        self.assertEqual(task.input_cost_total, Decimal("0.001234"))
        self.assertEqual(task.input_cost_uncached, Decimal("0.001000"))
        self.assertEqual(task.input_cost_cached, Decimal("0.000234"))
        self.assertEqual(task.output_cost, Decimal("0.000800"))
        self.assertEqual(task.total_cost, Decimal("0.002034"))

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
