from django.test import SimpleTestCase, tag

from api.agent.core.llm_utils import EmptyLiteLLMResponseError
from api.evals.meta_gobii import _planned_extra_scope_items
from api.evals.meta_gobii import META_GOBII_EVAL_CASES
from api.evals.scenarios.meta_gobii import (
    ENABLE_SYSTEM_SKILLS_TOOL_NAME,
    META_GOBII_SYSTEM_SKILL_KEY,
    SKILL_SEARCH_TOOL_NAME,
    MetaGobiiSystemSkillScenario,
)


@tag("eval_sim")
class MetaGobiiEvalJudgeTests(SimpleTestCase):
    def test_extra_scope_filter_allows_explicit_resource_limit_request(self):
        prompt = (
            "Archive every inactive Gobii you can find and raise the daily credit limit on all remaining Gobiis "
            "to the highest value."
        )

        extra_scope_items = _planned_extra_scope_items(
            [
                "Raising daily credit limit to highest value on all remaining Gobiis",
                "Add a weekly market digest schedule",
            ],
            user_prompt=prompt,
        )

        self.assertEqual(extra_scope_items, ["Add a weekly market digest schedule"])

    def test_skill_discovery_uses_deterministic_fallback_for_retryable_llm_error(self):
        case = next(
            eval_case
            for eval_case in META_GOBII_EVAL_CASES
            if eval_case.expect_skill_search and eval_case.expect_skill
        )
        scenario = MetaGobiiSystemSkillScenario()

        def fail_completion(**_kwargs):
            raise EmptyLiteLLMResponseError("provider returned no usable response", model="test-model")

        scenario._run_tool_completion = fail_completion

        calls = scenario._run_skill_discovery(case, simulated=False)

        self.assertEqual(
            [call["name"] for call in calls],
            [SKILL_SEARCH_TOOL_NAME, ENABLE_SYSTEM_SKILLS_TOOL_NAME],
        )
        self.assertEqual(calls[1]["arguments"]["skill_keys"], [META_GOBII_SYSTEM_SKILL_KEY])

    def test_skill_discovery_uses_deterministic_fallback_after_missing_expected_search(self):
        case = next(
            eval_case
            for eval_case in META_GOBII_EVAL_CASES
            if eval_case.slug == "ambiguous_recruiting_follow_up"
        )
        scenario = MetaGobiiSystemSkillScenario()

        scenario._run_tool_completion = lambda **_kwargs: []

        calls = scenario._run_skill_discovery(case, simulated=False)

        self.assertEqual(
            [call["name"] for call in calls],
            [SKILL_SEARCH_TOOL_NAME, ENABLE_SYSTEM_SKILLS_TOOL_NAME],
        )
