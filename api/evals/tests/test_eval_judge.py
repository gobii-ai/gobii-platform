import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, tag

from api.evals.execution import ScenarioExecutionTools


def _response(*, arguments=None, content=None):
    tool_calls = []
    if arguments is not None:
        tool_calls.append(SimpleNamespace(function=SimpleNamespace(name="submit_judgment", arguments=arguments)))
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=tool_calls, content=content))])


@tag("eval_sim")
class EvalJudgeFallbackTests(SimpleTestCase):
    @patch("api.evals.execution._JUDGE_RETRY_DELAYS_SECONDS", ())
    @patch("api.evals.execution.run_completion")
    def test_malformed_structured_arguments_fall_back_to_json_text(self, run_completion):
        run_completion.side_effect = [
            _response(arguments='{"choice":"Yes","reasoning":"unterminated'),
            _response(content=json.dumps({"choice": "Yes", "reasoning": "The contract is satisfied."})),
        ]

        choice, reasoning = ScenarioExecutionTools()._run_judge_completion(
            model="judge-model",
            prompt=[{"role": "system", "content": "judge"}, {"role": "user", "content": "context"}],
            tool_definition={"type": "function"},
            tool_choice={"type": "function"},
            params={"temperature": 0},
            options=["Yes", "No"],
        )

        self.assertEqual(choice, "Yes")
        self.assertIn("structured-output failure", reasoning)
        self.assertEqual(run_completion.call_count, 2)
