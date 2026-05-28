from django.test import SimpleTestCase, tag

import api.evals.loader  # noqa: F401 - registers scenarios and suites
from api.evals.registry import ScenarioRegistry
from api.evals.scenarios.message_quality import (
    MESSAGE_QUALITY_CASES,
    MESSAGE_QUALITY_SCENARIO_SLUGS,
    MESSAGE_QUALITY_SUITE_SLUG,
    MessageQualityScenario,
)
from api.evals.suites import SuiteRegistry


@tag("eval_sim")
class MessageQualityScenarioTests(SimpleTestCase):
    def test_message_quality_suite_contains_all_generated_scenarios(self):
        suite = SuiteRegistry.get(MESSAGE_QUALITY_SUITE_SLUG)

        self.assertIsNotNone(suite)
        self.assertEqual(tuple(suite.scenario_slugs), MESSAGE_QUALITY_SCENARIO_SLUGS)
        self.assertEqual(len(suite.scenario_slugs), 10)

    def test_generated_cases_cover_email_and_chat_for_each_real_world_domain(self):
        channels_by_brief = {}
        for case in MESSAGE_QUALITY_CASES:
            channels_by_brief.setdefault(case.brief, set()).add(case.channel)
            self.assertTrue(case.source_example_ids)

        self.assertEqual(len(channels_by_brief), 5)
        self.assertTrue(all(channels == {"email", "chat"} for channels in channels_by_brief.values()))

    def test_generated_scenarios_have_message_quality_metadata(self):
        registered = ScenarioRegistry.list_all()

        for slug in MESSAGE_QUALITY_SCENARIO_SLUGS:
            scenario = registered[slug]
            metadata = scenario.get_metadata()
            self.assertEqual(metadata.category, "message_quality")
            self.assertEqual(metadata.cost_class, "high")
            self.assertIn("llm_judge", metadata.tags)
            self.assertIn("response_quality", metadata.tags)

    def test_email_visual_formatting_quality_is_deferred_to_judge(self):
        case = next(case for case in MESSAGE_QUALITY_CASES if case.channel == "email")
        scenario = MessageQualityScenario()
        body = (
            "<h2>Summary</h2>"
            "<p>This plain email has enough structure for delivery inspection, "
            "but the judge owns whether it is visually polished.</p>"
        )

        failures = scenario._formatting_failures(
            case,
            {
                "to_address": case.recipient,
                "subject": case.subject,
                "will_continue_work": False,
            },
            body,
        )

        self.assertEqual(failures, [])

    def test_chat_visual_formatting_quality_is_deferred_to_judge(self):
        case = next(case for case in MESSAGE_QUALITY_CASES if case.channel == "chat")
        scenario = MessageQualityScenario()
        body = "Here is the report in plain prose. The judge decides whether that is rich enough."

        failures = scenario._formatting_failures(
            case,
            {"body": body, "will_continue_work": False},
            body,
        )

        self.assertEqual(failures, [])

    def test_delivery_basics_reject_empty_message_body(self):
        case = next(case for case in MESSAGE_QUALITY_CASES if case.channel == "chat")
        scenario = MessageQualityScenario()

        failures = scenario._formatting_failures(
            case,
            {"body": "", "will_continue_work": False},
            "",
        )

        self.assertEqual(failures, ["Message body was empty."])

    def test_chat_delivery_is_not_mocked_so_auditor_can_show_message(self):
        case = next(case for case in MESSAGE_QUALITY_CASES if case.channel == "chat")
        scenario = MessageQualityScenario()

        self.assertIsNone(scenario._mock_config(case))

    def test_email_delivery_stays_mocked_to_avoid_external_send(self):
        case = next(case for case in MESSAGE_QUALITY_CASES if case.channel == "email")
        scenario = MessageQualityScenario()

        self.assertEqual(set(scenario._mock_config(case)), {"send_email"})

    def test_mock_email_message_ids_are_not_treated_as_persisted_messages(self):
        scenario = MessageQualityScenario()
        call = type(
            "Call",
            (),
            {
                "tool_name": "send_chat_message",
                "result": '{"status":"ok","message_id":"eval-message_quality_email_price_monitor"}',
            },
        )

        self.assertIsNone(scenario._sent_message_for_call(call))
