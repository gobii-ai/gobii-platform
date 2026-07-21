import json
from types import SimpleNamespace

from django.test import SimpleTestCase, tag

from api.evals.scenarios.custom_tool_result_contract import CustomToolResultContractScenario


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
