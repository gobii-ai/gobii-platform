from django.test import SimpleTestCase, tag

import api.evals.loader  # noqa: F401 - registers scenarios and suites
from api.agent.tools.eval_synthetic_tools import EVAL_SYNTHETIC_TOOL_DEFINITIONS
from api.evals.registry import ScenarioRegistry
from api.evals.scenarios.reddit_content_quality import (
    REDDIT_CONTENT_QUALITY_CASES,
    REDDIT_CONTENT_QUALITY_SCENARIO_SLUGS,
    REDDIT_CONTENT_QUALITY_SUITE_SLUG,
    REDDIT_CREATE_POST_TOOL,
    REDDIT_REPLY_TOOL,
    RedditContentQualityScenario,
)
from api.evals.suites import SuiteRegistry


@tag("eval_sim")
class RedditContentQualityScenarioTests(SimpleTestCase):
    def test_suite_contains_comment_and_post_cases(self):
        suite = SuiteRegistry.get(REDDIT_CONTENT_QUALITY_SUITE_SLUG)

        self.assertIsNotNone(suite)
        self.assertEqual(tuple(suite.scenario_slugs), REDDIT_CONTENT_QUALITY_SCENARIO_SLUGS)
        self.assertEqual(len(suite.scenario_slugs), 3)
        self.assertEqual({case.action for case in REDDIT_CONTENT_QUALITY_CASES}, {"comment", "post"})

    def test_scenarios_use_real_harness_metadata(self):
        registered = ScenarioRegistry.list_all()

        for case in REDDIT_CONTENT_QUALITY_CASES:
            metadata = registered[case.slug].get_metadata()
            self.assertEqual(metadata.category, "reddit_content_quality")
            self.assertEqual(metadata.cost_class, "high")
            self.assertIn("llm_judge", metadata.tags)
            self.assertIn(case.expected_tool, metadata.tags)

    def test_prompts_do_not_leak_quality_rubric(self):
        forbidden_terms = (
            "invented firsthand",
            "generic praise",
            "canned social-media voice",
            "engagement bait",
            "forced promotion",
        )

        for case in REDDIT_CONTENT_QUALITY_CASES:
            prompt = RedditContentQualityScenario._prompt(case).lower()
            for term in forbidden_terms:
                self.assertNotIn(term, prompt)
            self.assertIn("publish" if case.action == "post" else "post the reply", prompt)

    def test_judge_targets_fabricated_experience_and_social_slop(self):
        for case in REDDIT_CONTENT_QUALITY_CASES:
            question = RedditContentQualityScenario._judge_question(case).lower()
            self.assertIn("invented firsthand experience", question)
            self.assertIn("generic praise or restatement", question)
            self.assertIn("engagement bait", question)
            self.assertIn(case.judge_focus.lower(), question)

    def test_reddit_synthetic_tools_capture_final_copy(self):
        reply = EVAL_SYNTHETIC_TOOL_DEFINITIONS[REDDIT_REPLY_TOOL]
        create_post = EVAL_SYNTHETIC_TOOL_DEFINITIONS[REDDIT_CREATE_POST_TOOL]

        self.assertEqual(reply["parameters"]["required"], ["itemUrl", "body"])
        self.assertEqual(create_post["parameters"]["required"], ["subreddit", "title", "body"])
        self.assertIn("published as written", reply["description"])
        self.assertIn("published as written", create_post["description"])

    def test_comment_basics_require_body_and_exact_target(self):
        case = next(case for case in REDDIT_CONTENT_QUALITY_CASES if case.action == "comment")

        failures = RedditContentQualityScenario._content_basic_failures(
            case,
            {"itemUrl": "https://www.reddit.com/r/wrong/comments/123", "body": ""},
        )

        self.assertIn("Reddit body was empty.", failures)
        self.assertIn("Reply targeted the wrong Reddit item.", failures)

    def test_post_basics_allow_style_but_reject_placeholders(self):
        case = next(case for case in REDDIT_CONTENT_QUALITY_CASES if case.action == "post")
        valid_params = {
            "subreddit": "r/selfhosted",
            "title": "Alpha feedback: schema and migration docs",
            "body": "I maintain LedgerLeaf — feedback welcome 🙂",
        }

        self.assertEqual(RedditContentQualityScenario._content_basic_failures(case, valid_params), [])
        failures = RedditContentQualityScenario._content_basic_failures(
            case,
            {**valid_params, "body": "Hi {{FIRST_NAME}}, take a look."},
        )

        self.assertEqual(failures, ["Reddit content contains an unresolved placeholder."])
