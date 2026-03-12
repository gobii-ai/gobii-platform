"""Test token usage tracking in persistent agent steps."""
import json
from decimal import Decimal
from types import SimpleNamespace
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
    EvalRun,
)
from api.agent.core.budget import BudgetContext, set_current_context
from api.agent.core.event_processing import _completion_with_failover
from api.agent.core.compaction import llm_summarise_comms
from api.agent.core.step_compaction import llm_summarise_steps
from api.agent.core.token_usage import log_agent_completion
from api.agent.tasks.agent_avatar import _generate_visual_description_via_llm as generate_visual_desc_via_llm
from api.agent.tasks.agent_tags import _generate_via_llm as generate_tags_via_llm
from api.agent.tasks.short_description import _generate_via_llm as generate_short_desc_via_llm
from api.agent.tasks.mini_description import _generate_via_llm as generate_mini_desc_via_llm
from api.agent.tools.search_tools import _search_with_llm
from tests.utils.token_usage import make_completion_response

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

        self.eval_run = EvalRun.objects.create(
            suite_run=None,
            scenario_slug="scenario",
            scenario_version="",
            agent=self.agent,
            initiated_by=self.user,
        )

    def tearDown(self):
        set_current_context(None)
        return super().tearDown()

    @staticmethod
    def _pricing_for_provider_hint(model, custom_llm_provider=None):
        if custom_llm_provider != "provider-key":
            return None
        return {
            "input_cost_per_token": 0.000002,
            "cache_read_input_token_cost": 0.000001,
            "output_cost_per_token": 0.000004,
        }
    
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

    def test_completion_logs_response_metadata(self):
        """Completion logging should persist response identifiers and durations."""
        response = make_completion_response(provider="openrouter")
        response.id = "resp_123"
        response.request_duration_ms = 321

        log_agent_completion(
            self.agent,
            completion_type=PersistentAgentCompletion.CompletionType.TAG,
            response=response,
        )

        completion = PersistentAgentCompletion.objects.filter(agent=self.agent).latest("created_at")
        self.assertEqual(completion.response_id, "resp_123")
        self.assertEqual(completion.request_duration_ms, 321)

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

    def test_completion_type_defaults_to_orchestrator(self):
        completion = PersistentAgentCompletion.objects.create(
            agent=self.agent,
            llm_model="gpt-4",
        )
        self.assertEqual(
            completion.completion_type,
            PersistentAgentCompletion.CompletionType.ORCHESTRATOR,
        )

    @patch("api.agent.core.compaction.run_completion")
    @patch("api.agent.core.compaction.get_summarization_llm_config")
    def test_compaction_llm_completion_logged(self, mock_config, mock_run_completion):
        mock_config.return_value = ("provider-key", "model-name", {})
        mock_run_completion.return_value = make_completion_response(
            reasoning_content="Chain of thought",
            model="model-name",
        )

        with patch("api.agent.core.token_usage.litellm.get_model_info") as mock_get_model_info:
            mock_get_model_info.side_effect = self._pricing_for_provider_hint
            summary = llm_summarise_comms("", [], agent=self.agent)

        self.assertEqual(summary, "Result")
        completion = PersistentAgentCompletion.objects.filter(
            agent=self.agent,
            completion_type=PersistentAgentCompletion.CompletionType.COMPACTION,
        ).latest("created_at")
        self.assertEqual(completion.llm_model, "model-name")
        self.assertEqual(completion.llm_provider, "provider-key")
        self.assertEqual(completion.prompt_tokens, 10)
        self.assertEqual(completion.input_cost_total, Decimal("0.000018"))
        self.assertEqual(completion.total_cost, Decimal("0.000038"))
        self.assertEqual(completion.thinking_content, "Chain of thought")

    @patch("api.agent.core.step_compaction.run_completion")
    @patch("api.agent.core.step_compaction.get_summarization_llm_config")
    def test_step_compaction_llm_completion_logged(self, mock_config, mock_run_completion):
        mock_config.return_value = ("provider-key", "step-model", {})
        mock_run_completion.return_value = make_completion_response(
            content="Step summary",
            model="step-model",
        )

        with patch("api.agent.core.token_usage.litellm.get_model_info") as mock_get_model_info:
            mock_get_model_info.side_effect = self._pricing_for_provider_hint
            summary = llm_summarise_steps("", [], agent=self.agent)

        self.assertEqual(summary, "Step summary")
        completion = PersistentAgentCompletion.objects.filter(
            agent=self.agent,
            completion_type=PersistentAgentCompletion.CompletionType.STEP_COMPACTION,
        ).latest("created_at")
        self.assertEqual(completion.llm_model, "step-model")
        self.assertEqual(completion.llm_provider, "provider-key")
        self.assertEqual(completion.input_cost_total, Decimal("0.000018"))
        self.assertEqual(completion.total_cost, Decimal("0.000038"))

    @patch("api.agent.tasks.agent_tags.run_completion")
    @patch("api.agent.tasks.agent_tags.get_summarization_llm_config")
    def test_tag_generation_completion_logged(self, mock_config, mock_run_completion):
        mock_config.return_value = ("provider-key", "tag-model", {})
        mock_run_completion.return_value = make_completion_response(
            content='["Alpha","Beta"]',
            prompt_tokens=8,
            completion_tokens=2,
            cached_tokens=1,
            provider="provider-key",
            model="tag-model",
        )

        tags = generate_tags_via_llm(self.agent, self.agent.charter)

        self.assertEqual(tags, ["Alpha", "Beta"])
        completion = PersistentAgentCompletion.objects.filter(
            agent=self.agent,
            completion_type=PersistentAgentCompletion.CompletionType.TAG,
        ).latest("created_at")
        self.assertEqual(completion.llm_model, "tag-model")
        self.assertEqual(completion.total_tokens, 10)

    @patch("api.agent.tasks.short_description.run_completion")
    @patch("api.agent.tasks.short_description.get_summarization_llm_config")
    def test_short_description_completion_logged(self, mock_config, mock_run_completion):
        mock_config.return_value = ("provider-key", "short-model", {})
        mock_run_completion.return_value = make_completion_response(
            content="Short summary",
            prompt_tokens=6,
            completion_tokens=3,
            cached_tokens=1,
            provider="provider-key",
            model="short-model",
        )

        result = generate_short_desc_via_llm(self.agent, self.agent.charter)

        self.assertEqual(result, "Short summary")
        completion = PersistentAgentCompletion.objects.filter(
            agent=self.agent,
            completion_type=PersistentAgentCompletion.CompletionType.SHORT_DESCRIPTION,
        ).latest("created_at")
        self.assertEqual(completion.total_tokens, 9)
        self.assertEqual(completion.llm_provider, "provider-key")

    @patch("api.agent.tasks.mini_description.run_completion")
    @patch("api.agent.tasks.mini_description.get_summarization_llm_config")
    def test_mini_description_completion_logged(self, mock_config, mock_run_completion):
        mock_config.return_value = ("provider-key", "mini-model", {})
        mock_run_completion.return_value = make_completion_response(
            content="Mini label",
            prompt_tokens=4,
            completion_tokens=2,
            cached_tokens=0,
            provider="provider-key",
            model="mini-model",
        )

        result = generate_mini_desc_via_llm(self.agent, self.agent.charter)

        self.assertEqual(result, "Mini label")
        completion = PersistentAgentCompletion.objects.filter(
            agent=self.agent,
            completion_type=PersistentAgentCompletion.CompletionType.MINI_DESCRIPTION,
        ).latest("created_at")
        self.assertEqual(completion.prompt_tokens, 4)
        self.assertEqual(completion.completion_tokens, 2)

    @patch("api.agent.core.token_usage.litellm.get_model_info")
    def test_profile_completion_logs_cost_with_provider_hint(self, mock_get_model_info):
        mock_get_model_info.side_effect = self._pricing_for_provider_hint
        test_cases = [
            {
                "name": "tag",
                "completion_type": PersistentAgentCompletion.CompletionType.TAG,
                "config_patch_path": "api.agent.tasks.agent_tags.get_summarization_llm_config",
                "run_patch_path": "api.agent.tasks.agent_tags.run_completion",
                "generator": generate_tags_via_llm,
                "content": '["Alpha","Beta"]',
                "expected_result": ["Alpha", "Beta"],
            },
            {
                "name": "short_description",
                "completion_type": PersistentAgentCompletion.CompletionType.SHORT_DESCRIPTION,
                "config_patch_path": "api.agent.tasks.short_description.get_summarization_llm_config",
                "run_patch_path": "api.agent.tasks.short_description.run_completion",
                "generator": generate_short_desc_via_llm,
                "content": "Short summary",
                "expected_result": "Short summary",
            },
            {
                "name": "mini_description",
                "completion_type": PersistentAgentCompletion.CompletionType.MINI_DESCRIPTION,
                "config_patch_path": "api.agent.tasks.mini_description.get_summarization_llm_config",
                "run_patch_path": "api.agent.tasks.mini_description.run_completion",
                "generator": generate_mini_desc_via_llm,
                "content": "Mini label",
                "expected_result": "Mini label",
            },
            {
                "name": "visual_description",
                "completion_type": PersistentAgentCompletion.CompletionType.AVATAR_VISUAL_DESCRIPTION,
                "config_patch_path": "api.agent.tasks.agent_avatar.get_summarization_llm_config",
                "run_patch_path": "api.agent.tasks.agent_avatar.run_completion",
                "generator": generate_visual_desc_via_llm,
                "content": "A thoughtful person with steady eye contact.",
                "expected_result": "A thoughtful person with steady eye contact.",
            },
        ]

        for case in test_cases:
            with self.subTest(case=case["name"]):
                with patch(case["config_patch_path"]) as mock_config, patch(
                    case["run_patch_path"]
                ) as mock_run_completion:
                    mock_config.return_value = ("provider-key", "gpt-4o-mini", {})
                    mock_run_completion.return_value = make_completion_response(
                        content=case["content"],
                        prompt_tokens=10,
                        completion_tokens=5,
                        cached_tokens=2,
                        model="gpt-4o-mini",
                    )

                    result = case["generator"](self.agent, self.agent.charter)

                self.assertEqual(result, case["expected_result"])
                completion = PersistentAgentCompletion.objects.filter(
                    agent=self.agent,
                    completion_type=case["completion_type"],
                ).latest("created_at")
                self.assertEqual(completion.llm_provider, "provider-key")
                self.assertEqual(completion.total_cost, Decimal("0.000038"))

    def test_log_agent_completion_uses_eval_run_from_budget_context(self):
        ctx = BudgetContext(
            agent_id=str(self.agent.id),
            budget_id="budget",
            branch_id="branch",
            depth=0,
            max_steps=10,
            max_depth=5,
            eval_run_id=str(self.eval_run.id),
        )
        set_current_context(ctx)

        token_usage = {"model": "test-model", "provider": "test-provider", "prompt_tokens": 3}
        log_agent_completion(
            self.agent,
            token_usage,
            completion_type=PersistentAgentCompletion.CompletionType.TAG,
        )

        completion = PersistentAgentCompletion.objects.filter(
            agent=self.agent,
            completion_type=PersistentAgentCompletion.CompletionType.TAG,
        ).latest("created_at")
        self.assertEqual(str(completion.eval_run_id), str(self.eval_run.id))
        self.assertEqual(completion.prompt_tokens, 3)
        self.assertIsNone(completion.thinking_content)

    def test_log_agent_completion_extracts_usage_and_thinking_from_response(self):
        response = make_completion_response(
            prompt_tokens=7,
            completion_tokens=4,
            cached_tokens=1,
            reasoning_content="Reasoned path",
            model="provider/model",
            provider="provider",
        )

        log_agent_completion(
            self.agent,
            completion_type=PersistentAgentCompletion.CompletionType.OTHER,
            response=response,
        )

        completion = PersistentAgentCompletion.objects.filter(
            agent=self.agent,
            completion_type=PersistentAgentCompletion.CompletionType.OTHER,
        ).latest("created_at")
        self.assertEqual(completion.prompt_tokens, 7)
        self.assertEqual(completion.completion_tokens, 4)
        self.assertEqual(completion.cached_tokens, 1)
        self.assertEqual(completion.llm_model, "provider/model")
        self.assertEqual(completion.llm_provider, "provider")
        self.assertEqual(completion.thinking_content, "Reasoned path")

    def test_log_agent_completion_uses_provider_cost_details_for_image_response(self):
        response = SimpleNamespace(
            id="gen-1773326030-Au4NBreqdciXufByuLEJ",
            model="google/gemini-3-pro-image-preview-20251120",
            provider="Google AI Studio",
            request_duration_ms=23588,
            usage=SimpleNamespace(
                prompt_tokens=490,
                completion_tokens=1632,
                total_tokens=2122,
                cost=0,
                prompt_tokens_details=SimpleNamespace(cached_tokens=0),
            ),
            cost_details={
                "upstream_inference_cost": 0.139712,
                "upstream_inference_prompt_cost": 0.00098,
                "upstream_inference_completions_cost": 0.138732,
            },
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=None,
                        reasoning_content="Image reasoning",
                    )
                )
            ],
        )

        log_agent_completion(
            self.agent,
            completion_type=PersistentAgentCompletion.CompletionType.IMAGE_GENERATION,
            response=response,
        )

        completion = PersistentAgentCompletion.objects.filter(
            agent=self.agent,
            completion_type=PersistentAgentCompletion.CompletionType.IMAGE_GENERATION,
        ).latest("created_at")
        self.assertEqual(completion.llm_model, "google/gemini-3-pro-image-preview-20251120")
        self.assertEqual(completion.llm_provider, "Google AI Studio")
        self.assertEqual(completion.input_cost_total, Decimal("0.000980"))
        self.assertEqual(completion.input_cost_uncached, Decimal("0.000980"))
        self.assertEqual(completion.input_cost_cached, Decimal("0.000000"))
        self.assertEqual(completion.output_cost, Decimal("0.138732"))
        self.assertEqual(completion.total_cost, Decimal("0.139712"))

    def test_log_agent_completion_uses_provider_cost_details_without_usage_payload(self):
        response = SimpleNamespace(
            id="gen-no-usage",
            model="google/gemini-3-pro-image-preview-20251120",
            provider="Google AI Studio",
            request_duration_ms=1200,
            cost_details={
                "upstream_inference_cost": 0.050000,
                "upstream_inference_prompt_cost": 0.010000,
                "upstream_inference_completions_cost": 0.040000,
            },
            choices=[],
        )

        log_agent_completion(
            self.agent,
            completion_type=PersistentAgentCompletion.CompletionType.IMAGE_GENERATION,
            response=response,
        )

        completion = PersistentAgentCompletion.objects.filter(
            agent=self.agent,
            completion_type=PersistentAgentCompletion.CompletionType.IMAGE_GENERATION,
        ).latest("created_at")
        self.assertEqual(completion.llm_model, "google/gemini-3-pro-image-preview-20251120")
        self.assertEqual(completion.llm_provider, "Google AI Studio")
        self.assertIsNone(completion.prompt_tokens)
        self.assertIsNone(completion.completion_tokens)
        self.assertEqual(completion.input_cost_total, Decimal("0.010000"))
        self.assertEqual(completion.output_cost, Decimal("0.040000"))
        self.assertEqual(completion.total_cost, Decimal("0.050000"))

    @patch("api.models.PersistentAgentCompletion.objects.create", side_effect=RuntimeError("db down"))
    def test_log_agent_completion_warns_on_failure(self, mock_create):
        with self.assertLogs("api.agent.core.token_usage", level="WARNING") as captured:
            log_agent_completion(
                self.agent,
                {"model": "m", "provider": "p"},
                completion_type=PersistentAgentCompletion.CompletionType.OTHER,
            )

        self.assertTrue(any("Failed to persist completion" in msg for msg in captured.output))

    @patch("api.agent.tools.search_tools.run_completion")
    @patch("api.agent.tools.search_tools.get_llm_config_with_failover")
    def test_tool_search_completion_logged(self, mock_failover, mock_run_completion):
        mock_failover.return_value = [("provider-key", "search-model", {})]
        mock_run_completion.return_value = make_completion_response(
            content="Enabled",
            prompt_tokens=12,
            completion_tokens=6,
            cached_tokens=3,
            tool_names=["http_request"],
            model="search-model",
        )

        def _enable(agent, names):
            return {"status": "success", "enabled": names, "already_enabled": [], "evicted": [], "invalid": []}

        catalog = [{"full_name": "http_request", "description": "HTTP calls", "parameters": {}}]
        with patch("api.agent.core.token_usage.litellm.get_model_info") as mock_get_model_info:
            mock_get_model_info.side_effect = self._pricing_for_provider_hint
            result = _search_with_llm(
                agent=self.agent,
                query="Use HTTP",
                provider_name="test",
                catalog=catalog,
                enable_callback=_enable,
                empty_message="",
            )

        self.assertEqual(result["status"], "success")
        completion = PersistentAgentCompletion.objects.filter(
            agent=self.agent,
            completion_type=PersistentAgentCompletion.CompletionType.TOOL_SEARCH,
        ).latest("created_at")
        self.assertEqual(completion.llm_model, "search-model")
        self.assertEqual(completion.llm_provider, "provider-key")
        self.assertEqual(completion.input_cost_total, Decimal("0.000021"))
        self.assertEqual(completion.total_cost, Decimal("0.000045"))
        self.assertEqual(completion.total_tokens, 18)


if __name__ == '__main__':
    import django
    django.setup()
    import unittest
    unittest.main()
