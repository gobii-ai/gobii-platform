from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, tag

from api.agent.tools.planning import execute_end_planning
from api.agent.tools.schedule_updater import execute_update_schedule
from api.models import (
    BrowserUseAgent,
    HistoricalAgentCostSample,
    IntelligenceTier,
    PersistentAgent,
    PersistentAgentCreditForecast,
)
from api.services.agent_credit_forecasts import (
    EmbeddingResult,
    SimilarAgentSample,
    estimate_agent_credit_forecast,
    estimate_schedule_runs_per_day,
    persist_agent_credit_forecast,
    serialize_agent_credit_forecast,
)


@tag("agent_credit_forecast_batch")
class AgentCreditForecastEstimatorTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="agent_forecast_user")
        self.tier = IntelligenceTier.objects.create(
            key="forecast_premium",
            display_name="Forecast Premium",
            rank=91,
            credit_multiplier=Decimal("2.00"),
        )
        self.browser_agent = BrowserUseAgent.objects.create(user=self.user, name="Forecast Browser")
        self.agent = PersistentAgent.objects.create(
            name="Forecast Agent",
            user=self.user,
            browser_use_agent=self.browser_agent,
            preferred_llm_tier=self.tier,
            charter="Watch the inbox and summarize vendor issues.",
            planning_state=PersistentAgent.PlanningState.COMPLETED,
            planning_plan="Review vendor email daily and send a concise summary.",
        )

    def _sample(self, suffix: str, *, normalized_setup: str, normalized_run: str) -> HistoricalAgentCostSample:
        return HistoricalAgentCostSample.objects.create(
            source_sample_id=f"sample-{suffix}",
            source_agent_id=self.agent.id,
            tier_credit_multiplier=Decimal("1.00"),
            normalized_setup_credits=Decimal(normalized_setup),
            normalized_first_run_credits=Decimal(normalized_run),
            normalized_daily_credits=Decimal(normalized_run),
            normalized_monthly_credits=Decimal(normalized_run) * Decimal("30"),
            sample_confidence=HistoricalAgentCostSample.Confidence.HIGH,
            embedding_dimension=2,
            embedding_text="historical agent",
        )

    @patch("api.services.agent_credit_forecasts.TaskCreditService.calculate_available_tasks_for_owner")
    @patch("api.services.agent_credit_forecasts.find_similar_agent_samples")
    @patch("api.services.agent_credit_forecasts.generate_embedding")
    def test_no_data_fallback_persists_empty_forecast(self, mock_embedding, mock_find, mock_available):
        mock_embedding.return_value = EmbeddingResult(vector=[0.1, 0.2], model="test-embedding")
        mock_find.return_value = []
        mock_available.return_value = Decimal("1000")

        forecast = persist_agent_credit_forecast(self.agent)

        self.assertEqual(forecast.confidence, PersistentAgentCreditForecast.Confidence.NONE)
        self.assertEqual(forecast.sample_count, 0)
        self.assertIsNone(forecast.setup_credits)
        self.assertEqual(forecast.warning_level, PersistentAgentCreditForecast.WarningLevel.NONE)

    @patch("api.services.agent_credit_forecasts.TaskCreditService.calculate_available_tasks_for_owner")
    @patch("api.services.agent_credit_forecasts.find_similar_agent_samples")
    @patch("api.services.agent_credit_forecasts.generate_embedding")
    def test_tier_multiplier_applies_to_normalized_p80_estimates(self, mock_embedding, mock_find, mock_available):
        mock_embedding.return_value = EmbeddingResult(vector=[0.1, 0.2], model="test-embedding")
        mock_available.return_value = Decimal("1000")
        samples = [
            SimilarAgentSample(self._sample("a", normalized_setup="10", normalized_run="4"), distance=0.05),
            SimilarAgentSample(self._sample("b", normalized_setup="20", normalized_run="8"), distance=0.05),
            SimilarAgentSample(self._sample("c", normalized_setup="30", normalized_run="12"), distance=0.05),
        ]
        mock_find.return_value = samples

        forecast = estimate_agent_credit_forecast(self.agent)

        self.assertEqual(forecast.setup_credits, Decimal("60"))
        self.assertEqual(forecast.per_run_credits, Decimal("24"))
        self.assertEqual(forecast.daily_credits, Decimal("0"))
        self.assertEqual(forecast.monthly_credits, Decimal("0"))
        self.assertEqual(forecast.confidence, PersistentAgentCreditForecast.Confidence.MEDIUM)

    @patch("api.services.agent_credit_forecasts.TaskCreditService.calculate_available_tasks_for_owner")
    @patch("api.services.agent_credit_forecasts.find_similar_agent_samples")
    @patch("api.services.agent_credit_forecasts.generate_embedding")
    def test_schedule_frequency_converts_per_run_into_daily_and_monthly(self, mock_embedding, mock_find, mock_available):
        self.agent.schedule = "@every 12h"
        self.agent.save(update_fields=["schedule"])
        mock_embedding.return_value = EmbeddingResult(vector=[0.1, 0.2], model="test-embedding")
        mock_available.return_value = Decimal("1000")
        mock_find.return_value = [
            SimilarAgentSample(self._sample("schedule", normalized_setup="5", normalized_run="10"), distance=0.01)
        ]

        forecast = estimate_agent_credit_forecast(self.agent)

        self.assertEqual(estimate_schedule_runs_per_day("@every 12h"), Decimal("2"))
        self.assertEqual(forecast.per_run_credits, Decimal("20"))
        self.assertEqual(forecast.daily_credits, Decimal("40"))
        self.assertEqual(forecast.monthly_credits, Decimal("1200"))

    @patch("api.services.agent_credit_forecasts.TaskCreditService.calculate_available_tasks_for_owner")
    @patch("api.services.agent_credit_forecasts.find_similar_agent_samples")
    @patch("api.services.agent_credit_forecasts.generate_embedding")
    def test_affordability_warning_thresholds(self, mock_embedding, mock_find, mock_available):
        self.agent.schedule = "@daily"
        self.agent.save(update_fields=["schedule"])
        mock_embedding.return_value = EmbeddingResult(vector=[0.1, 0.2], model="test-embedding")
        mock_find.return_value = [
            SimilarAgentSample(self._sample("warning", normalized_setup="20", normalized_run="20"), distance=0.01)
        ]

        mock_available.return_value = Decimal("2000")
        self.assertEqual(estimate_agent_credit_forecast(self.agent).warning_level, "medium")

        mock_available.return_value = Decimal("10")
        high = estimate_agent_credit_forecast(self.agent)
        self.assertEqual(high.warning_level, "high")
        self.assertIn("low remaining credits", high.warning_reasons)

        mock_available.return_value = Decimal("5000")
        self.assertEqual(estimate_agent_credit_forecast(self.agent).warning_level, "none")


@tag("agent_credit_forecast_batch")
class AgentCreditForecastIntegrationTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="agent_forecast_integration_user")
        self.browser_agent = BrowserUseAgent.objects.create(user=self.user, name="Forecast Integration Browser")
        self.agent = PersistentAgent.objects.create(
            name="Planning Forecast Agent",
            user=self.user,
            browser_use_agent=self.browser_agent,
            charter="Plan first.",
            planning_state=PersistentAgent.PlanningState.PLANNING,
        )

    @patch("console.agent_chat.signals.emit_agent_usage_update")
    @patch("console.agent_chat.signals.emit_agent_planning_state_update")
    @patch("api.services.agent_credit_forecasts.TaskCreditService.calculate_available_tasks_for_owner")
    @patch("api.services.agent_credit_forecasts.find_similar_agent_samples")
    @patch("api.services.agent_credit_forecasts.generate_embedding")
    def test_end_planning_persists_forecast_and_returns_payload(
        self,
        mock_embedding,
        mock_find,
        mock_available,
        _mock_planning_emit,
        _mock_usage_emit,
    ):
        sample = HistoricalAgentCostSample.objects.create(
            source_sample_id="end-planning-sample",
            source_agent_id=self.agent.id,
            tier_credit_multiplier=Decimal("1.00"),
            normalized_setup_credits=Decimal("8"),
            normalized_first_run_credits=Decimal("3"),
            normalized_daily_credits=Decimal("3"),
            normalized_monthly_credits=Decimal("90"),
            sample_confidence=HistoricalAgentCostSample.Confidence.HIGH,
            embedding_dimension=2,
            embedding_text="historical planning agent",
        )
        mock_embedding.return_value = EmbeddingResult(vector=[0.1, 0.2], model="test-embedding")
        mock_find.return_value = [SimilarAgentSample(sample, distance=0.01)]
        mock_available.return_value = Decimal("1000")

        result = execute_end_planning(
            self.agent,
            {"full_plan": "Run the requested vendor digest.", "schedule": "@daily"},
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["credit_forecast"]["setupCredits"], 8)
        self.assertEqual(result["credit_forecast"]["dailyCredits"], 3)
        self.assertEqual(result["schedule"], "@daily")
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.planning_state, PersistentAgent.PlanningState.COMPLETED)
        self.assertEqual(self.agent.schedule, "@daily")
        self.assertEqual(serialize_agent_credit_forecast(self.agent)["perRunCredits"], 3)

    @patch("api.services.agent_credit_forecasts.persist_agent_credit_forecast")
    def test_schedule_updates_after_planning_do_not_recompute_forecast(self, mock_persist):
        self.agent.planning_state = PersistentAgent.PlanningState.COMPLETED
        self.agent.schedule = "@daily"
        self.agent.save(update_fields=["planning_state", "schedule"])

        result = execute_update_schedule(self.agent, {"new_schedule": "@weekly"})

        self.assertEqual(result["status"], "ok")
        self.assertNotIn("credit_forecast", result)
        mock_persist.assert_not_called()

    @patch("api.management.commands.seed_agent_credit_forecast_samples.set_historical_sample_embedding")
    @patch("api.management.commands.seed_agent_credit_forecast_samples.generate_embedding")
    @patch("api.management.commands.seed_agent_credit_forecast_samples._fetch_source_rows")
    def test_seed_command_imports_mocked_source_rows(self, mock_fetch, mock_embedding, mock_set_embedding):
        mock_fetch.return_value = [
            {
                "source_agent_id": self.agent.id,
                "agent_name": "Historical Agent",
                "charter_text": "Summarize orders.",
                "planning_plan": "Summarize order email every morning.",
                "schedule": "@daily",
                "org_owned": False,
                "tier_key": "standard",
                "tier_credit_multiplier": Decimal("1.00"),
                "created_at_source": self.agent.created_at,
                "planning_completed_at_source": self.agent.created_at,
                "last_observed_at_source": self.agent.created_at,
                "enabled_tools": ["gmail.search"],
                "charged_step_count": 6,
                "tool_call_count": 2,
                "setup_credits": Decimal("4"),
                "first_run_credits": Decimal("7"),
                "observed_total_credits": Decimal("30"),
                "observation_days": Decimal("3"),
            }
        ]
        mock_embedding.return_value = EmbeddingResult(vector=[0.1, 0.2], model="test-embedding")

        call_command(
            "seed_agent_credit_forecast_samples",
            "--source-database-url",
            "postgresql://readonly.example/db",
            "--limit",
            "1",
            "--generate-embeddings",
            verbosity=0,
        )

        sample = HistoricalAgentCostSample.objects.get(source_sample_id=f"agent:{self.agent.id}")
        self.assertEqual(sample.first_run_credits, Decimal("7"))
        self.assertEqual(sample.daily_credits, Decimal("10"))
        self.assertEqual(sample.monthly_credits, Decimal("300"))
        mock_set_embedding.assert_called_once()
