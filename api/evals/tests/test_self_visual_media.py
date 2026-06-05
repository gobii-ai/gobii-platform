from django.test import SimpleTestCase, tag

import api.evals.loader  # noqa: F401 - registers scenarios and suites
from api.evals.registry import ScenarioRegistry
from api.evals.scenarios.self_visual_media import (
    SELF_IMAGE_SMS_ATTACHMENT,
    SELF_VIDEO_SMS_ATTACHMENT,
    SELF_VISUAL_DESCRIPTION_NOT_IN_ORDINARY_PROMPT,
    SELF_VISUAL_MEDIA_SCENARIO_SLUGS,
    SELF_VISUAL_MEDIA_SUITE_SLUG,
)
from api.evals.suites import SuiteRegistry


@tag("eval_sim")
class SelfVisualMediaScenarioTests(SimpleTestCase):
    def test_self_visual_media_suite_contains_all_scenarios(self):
        suite = SuiteRegistry.get(SELF_VISUAL_MEDIA_SUITE_SLUG)

        self.assertIsNotNone(suite)
        self.assertEqual(tuple(suite.scenario_slugs), SELF_VISUAL_MEDIA_SCENARIO_SLUGS)

    def test_self_visual_media_scenarios_have_metadata(self):
        registered = ScenarioRegistry.list_all()

        for slug in SELF_VISUAL_MEDIA_SCENARIO_SLUGS:
            scenario = registered[slug]
            metadata = scenario.get_metadata()
            self.assertEqual(metadata.tier, "core")
            self.assertEqual(metadata.category, "self_visual_media")
            self.assertEqual(metadata.expected_runtime, "short")
            self.assertEqual(metadata.cost_class, "low")
            self.assertTrue(metadata.supports_simulation)
            self.assertIn("self_visual_media", metadata.tags)

    def test_positive_scenarios_stop_after_sms_and_allow_discovery(self):
        for slug in (SELF_IMAGE_SMS_ATTACHMENT, SELF_VIDEO_SMS_ATTACHMENT):
            scenario = ScenarioRegistry.get(slug)
            policy = scenario._eval_stop_policy()

            self.assertIn("search_tools", policy["allowed_tool_names"])
            self.assertIn("get_self_visual_identity", policy["allowed_tool_names"])
            self.assertIn("send_sms", policy["allowed_tool_names"])
            self.assertEqual(policy["stop_when_all_seen"], [{"tool_name": "send_sms", "after_finish": True}])

    def test_positive_scenarios_require_media_prompt_identity_terms(self):
        image = ScenarioRegistry.get(SELF_IMAGE_SMS_ATTACHMENT)
        video = ScenarioRegistry.get(SELF_VIDEO_SMS_ATTACHMENT)

        self.assertEqual(image.media_tool_name, "create_image")
        self.assertIn("iridescent teal hair", image.required_terms)
        self.assertEqual(video.media_tool_name, "create_video")
        self.assertIn("midnight-blue curls", video.required_terms)

    def test_negative_control_scenario_is_prompt_context_only(self):
        scenario = ScenarioRegistry.get(SELF_VISUAL_DESCRIPTION_NOT_IN_ORDINARY_PROMPT)

        self.assertEqual([task.name for task in scenario.tasks], [
            "inject_ordinary_prompt",
            "verify_visual_identity_absent",
        ])
