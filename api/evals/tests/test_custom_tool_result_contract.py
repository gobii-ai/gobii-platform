import json
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, tag
from waffle.models import Flag

from api.evals.scenarios.custom_tool_result_contract import CustomToolResultContractScenario
from api.models import BrowserUseAgent, PersistentAgent


User = get_user_model()


@tag("eval_sim")
class CustomToolResultContractEvaluatorTests(SimpleTestCase):
    def test_rejected_create_retry_does_not_count_as_repeated_success(self):
        rejected = SimpleNamespace(
            status="error",
            result=json.dumps({"status": "error", "retryable": False}),
        )
        created = SimpleNamespace(
            status="complete",
            result=json.dumps({"status": "ok", "created": True}),
        )

        self.assertEqual(
            CustomToolResultContractScenario._successful_create_calls([rejected, created]),
            [created],
        )

    def test_multiple_successful_creates_remain_visible_to_evaluator(self):
        calls = [
            SimpleNamespace(status="complete", result=json.dumps({"status": "ok"})),
            SimpleNamespace(status="complete", result=json.dumps({"status": "success"})),
        ]

        self.assertEqual(CustomToolResultContractScenario._successful_create_calls(calls), calls)


@tag("eval_sim")
class CustomToolResultContractSetupTests(TestCase):
    def test_prepare_agent_enables_sandbox_tool_visibility(self):
        user = User.objects.create_user(
            username="custom-tool-eval@example.test",
            email="custom-tool-eval@example.test",
        )
        browser_agent = BrowserUseAgent.objects.create(
            user=user,
            name="Custom tool eval browser",
        )
        agent = PersistentAgent.objects.create(
            user=user,
            name="Custom tool eval",
            charter="Initial eval charter.",
            browser_use_agent=browser_agent,
        )
        scenario = CustomToolResultContractScenario()

        scenario._prepare_agent(agent.id)

        agent.refresh_from_db()
        self.assertEqual(agent.planning_state, PersistentAgent.PlanningState.SKIPPED)
        self.assertTrue(
            Flag.objects.get(name="sandbox_compute").users.filter(id=user.id).exists()
        )
