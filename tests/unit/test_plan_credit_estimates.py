import json
import uuid
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from api.agent.tasks.plan_credit_estimates import estimate_plan_credit_usage_task
from api.agent.tools.plan import execute_update_plan
from api.models import (
    BrowserUseAgent,
    PersistentAgent,
    PersistentAgentKanbanEvent,
    PersistentAgentPlanCreditEstimate,
    PersistentAgentStep,
    TaskCredit,
)
from api.services.plan_credit_estimates import (
    ESTIMATE_TOOL_NAME,
    create_pending_plan_credit_estimate,
    determine_frequency,
    extract_estimate_arguments,
    normalize_estimate_payload,
    serialize_estimate_for_agent,
)
from console.agent_chat.timeline import serialize_plan_snapshot
from tests.utils.llm_seed import get_intelligence_tier


@tag("batch_agent_chat")
class PlanCreditEstimateTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.user = user_model.objects.create_user(
            username="plan-estimate-owner",
            email="plan-estimate-owner@example.com",
            password="password123",
        )
        cls.browser_agent = BrowserUseAgent.objects.create(user=cls.user, name="Plan Estimate Browser")
        cls.agent = PersistentAgent.objects.create(
            user=cls.user,
            name="Plan Estimate Agent",
            charter="Estimate visible plan costs",
            browser_use_agent=cls.browser_agent,
        )

    def _create_event(self, cursor_value: int = 1) -> PersistentAgentKanbanEvent:
        return PersistentAgentKanbanEvent.objects.create(
            agent=self.agent,
            cursor_value=cursor_value,
            cursor_identifier=uuid.uuid4(),
            display_text="Plan updated",
            primary_action=PersistentAgentKanbanEvent.Action.UPDATED,
            todo_count=1,
            doing_count=0,
            done_count=0,
        )

    def test_update_plan_creates_pending_estimate_only_at_plan_start(self):
        with patch("console.agent_chat.signals.enqueue_plan_credit_estimate") as enqueue:
            result = execute_update_plan(
                self.agent,
                {
                    "plan": [{"step": "Research pricing", "status": "todo"}],
                    "will_continue_work": True,
                },
            )
            self.assertEqual(result["status"], "ok")
            self.assertEqual(enqueue.call_count, 1)

            result = execute_update_plan(
                self.agent,
                {
                    "plan": [{"step": "Research pricing", "status": "doing"}],
                    "will_continue_work": True,
                },
            )
            self.assertEqual(result["status"], "ok")
            self.assertEqual(enqueue.call_count, 1)

        estimates = PersistentAgentPlanCreditEstimate.objects.filter(agent=self.agent)
        self.assertEqual(estimates.count(), 1)
        self.assertEqual(
            estimates.filter(status=PersistentAgentPlanCreditEstimate.Status.PENDING).count(),
            1,
        )

    def test_pending_estimate_serializes_into_current_plan(self):
        with patch("console.agent_chat.signals.enqueue_plan_credit_estimate"):
            execute_update_plan(
                self.agent,
                {
                    "plan": [{"step": "Research pricing", "status": "todo"}],
                    "will_continue_work": True,
                },
            )

        payload = serialize_plan_snapshot(self.agent)

        self.assertEqual(payload["estimate"]["status"], "pending")
        self.assertEqual(payload["estimate"]["frequency"], "none")

    def test_start_estimate_serializes_across_later_plan_events(self):
        with patch("console.agent_chat.signals.enqueue_plan_credit_estimate"):
            execute_update_plan(
                self.agent,
                {
                    "plan": [{"step": "Research pricing", "status": "todo"}],
                    "will_continue_work": True,
                },
            )
            estimate = PersistentAgentPlanCreditEstimate.objects.get(agent=self.agent)
            estimate.status = PersistentAgentPlanCreditEstimate.Status.COMPLETE
            estimate.base_estimate = Decimal("2.000")
            estimate.save(update_fields=["status", "base_estimate", "updated_at"])
            execute_update_plan(
                self.agent,
                {
                    "plan": [{"step": "Research pricing", "status": "doing"}],
                    "will_continue_work": True,
                },
            )

        payload = serialize_plan_snapshot(self.agent)

        self.assertEqual(payload["estimate"]["displayEstimate"], 2.0)

    def test_llm_tool_output_parses_and_validates(self):
        raw_arguments = {
            "frequency": "daily",
            "base_estimate": 2.4,
            "step_estimates": [{"step": "Research", "base_estimate": 2.4}],
            "tool_breakdown": [{"tool_name": "sqlite_batch", "estimated_calls": 4, "base_credit_cost": 0.25}],
            "assumptions": ["Conservative data-work estimate."],
        }
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        tool_calls=[
                            SimpleNamespace(
                                function=SimpleNamespace(
                                    name=ESTIMATE_TOOL_NAME,
                                    arguments=json.dumps(raw_arguments),
                                )
                            )
                        ],
                        content=None,
                    )
                )
            ]
        )

        parsed = extract_estimate_arguments(response)
        normalized = normalize_estimate_payload(parsed, "none")

        self.assertEqual(normalized["frequency"], "daily")
        self.assertEqual(normalized["base_estimate"], Decimal("2.400"))

    def test_invalid_llm_estimate_rejected(self):
        with self.assertRaises(ValueError):
            normalize_estimate_payload(
                {
                    "frequency": "daily",
                    "base_estimate": -1,
                    "step_estimates": [],
                    "tool_breakdown": [],
                    "assumptions": [],
                },
                "none",
            )

    def test_task_uses_heuristic_fallback_when_llm_fails(self):
        event = self._create_event()
        estimate = create_pending_plan_credit_estimate(
            self.agent,
            event,
            {
                "todoTitles": ["Research competitors and email summary"],
                "doingTitles": [],
                "doneTitles": [],
                "files": [],
                "messages": [],
            },
        )

        with (
            patch("api.agent.tasks.plan_credit_estimates._generate_llm_estimate", side_effect=ValueError("bad llm")),
            patch("api.agent.tasks.plan_credit_estimates._broadcast_if_current") as broadcast,
        ):
            estimate_plan_credit_usage_task.run(str(estimate.id))

        estimate.refresh_from_db()
        self.assertEqual(estimate.status, PersistentAgentPlanCreditEstimate.Status.COMPLETE)
        self.assertIsNotNone(estimate.base_estimate)
        self.assertIn("bad llm", estimate.error_message)
        self.assertTrue(estimate.assumptions)
        broadcast.assert_called_once()

    def test_tier_multiplier_applied_only_during_serialization(self):
        self.agent.preferred_llm_tier = get_intelligence_tier("premium")
        self.agent.save(update_fields=["preferred_llm_tier", "updated_at"])
        event = self._create_event()
        estimate = PersistentAgentPlanCreditEstimate.objects.create(
            agent=self.agent,
            kanban_event=event,
            status=PersistentAgentPlanCreditEstimate.Status.COMPLETE,
            frequency=PersistentAgentPlanCreditEstimate.Frequency.DAILY,
            base_estimate=Decimal("3.000"),
        )

        payload = serialize_estimate_for_agent(self.agent, estimate)
        estimate.refresh_from_db()

        self.assertEqual(estimate.base_estimate, Decimal("3.000"))
        self.assertEqual(payload["baseEstimate"], 3.0)
        self.assertEqual(payload["displayEstimate"], 6.0)
        self.assertEqual(payload["tierMultiplier"], 2.0)

    def test_start_estimate_can_complete_after_later_plan_update(self):
        old_event = self._create_event(cursor_value=1)
        estimate = create_pending_plan_credit_estimate(
            self.agent,
            old_event,
            {"todoTitles": ["Old plan"], "doingTitles": [], "doneTitles": [], "files": [], "messages": []},
        )
        self._create_event(cursor_value=2)
        llm_payload = {
            "frequency": "none",
            "base_estimate": Decimal("4.000"),
            "step_estimates": [],
            "tool_breakdown": [],
            "assumptions": [],
        }

        with (
            patch("api.agent.tasks.plan_credit_estimates._generate_llm_estimate", return_value=(llm_payload, "model", "provider")),
            patch("api.agent.tasks.plan_credit_estimates._broadcast_if_current") as broadcast,
        ):
            estimate_plan_credit_usage_task.run(str(estimate.id))

        estimate.refresh_from_db()
        self.assertEqual(estimate.status, PersistentAgentPlanCreditEstimate.Status.COMPLETE)
        self.assertEqual(estimate.base_estimate, Decimal("4.000"))
        broadcast.assert_called_once()

    def test_completed_plan_serializes_actual_credit_usage(self):
        TaskCredit.objects.create(
            user=self.user,
            credits=Decimal("10.000"),
            credits_used=Decimal("0.000"),
            granted_date=timezone.now(),
            expiration_date=timezone.now() + timedelta(days=1),
            additional_task=True,
        )
        with patch("console.agent_chat.signals.enqueue_plan_credit_estimate"):
            execute_update_plan(
                self.agent,
                {
                    "plan": [{"step": "Research pricing", "status": "todo"}],
                    "will_continue_work": True,
                },
            )
            PersistentAgentStep.objects.create(
                agent=self.agent,
                description="charged work",
                credits_cost=Decimal("1.250"),
            )
            execute_update_plan(
                self.agent,
                {
                    "plan": [{"step": "Research pricing", "status": "done"}],
                    "will_continue_work": False,
                },
            )

        estimate = PersistentAgentPlanCreditEstimate.objects.get(agent=self.agent)
        self.assertEqual(estimate.actual_credits, Decimal("1.250"))

        payload = serialize_plan_snapshot(self.agent)
        self.assertEqual(payload["estimate"]["actualCredits"], 1.25)

    def test_frequency_normalization(self):
        self.assertEqual(determine_frequency(None), "none")
        self.assertEqual(determine_frequency("0 * * * *"), "hourly")
        self.assertEqual(determine_frequency("@every 4h"), "hourly")
        self.assertEqual(determine_frequency("0 9 * * *"), "daily")
        self.assertEqual(determine_frequency("@weekly"), "weekly")
        self.assertEqual(determine_frequency("0 9 1 * *"), "monthly_or_other")
