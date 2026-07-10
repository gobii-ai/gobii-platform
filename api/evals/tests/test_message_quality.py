from django.test import SimpleTestCase, tag

import api.evals.loader  # noqa: F401 - registers scenarios and suites
from api.evals.registry import ScenarioRegistry
from api.evals.scenarios.message_quality import (
    MESSAGE_QUALITY_CASES,
    MESSAGE_QUALITY_SCENARIO_SLUGS,
    MESSAGE_QUALITY_SUITE_SLUG,
    REPORT_MESSAGE_QUALITY_CASES,
    SIMPLE_EMAIL_QUALITY_CASES,
    MessageQualityScenario,
)
from api.evals.suites import SuiteRegistry


@tag("eval_sim")
class MessageQualityScenarioTests(SimpleTestCase):
    def test_message_quality_suite_contains_all_generated_scenarios(self):
        suite = SuiteRegistry.get(MESSAGE_QUALITY_SUITE_SLUG)

        self.assertIsNotNone(suite)
        self.assertEqual(tuple(suite.scenario_slugs), MESSAGE_QUALITY_SCENARIO_SLUGS)
        self.assertEqual(len(suite.scenario_slugs), 12)

    def test_generated_cases_cover_email_and_chat_for_each_real_world_domain(self):
        channels_by_brief = {}
        for case in REPORT_MESSAGE_QUALITY_CASES:
            channels_by_brief.setdefault(case.brief, set()).add(case.channel)
            self.assertTrue(case.source_example_ids)

        self.assertEqual(len(channels_by_brief), 5)
        self.assertTrue(all(channels == {"email", "chat"} for channels in channels_by_brief.values()))

    def test_simple_email_cases_are_restrained_outreach_counterexamples(self):
        self.assertEqual(len(SIMPLE_EMAIL_QUALITY_CASES), 2)

        for case in SIMPLE_EMAIL_QUALITY_CASES:
            self.assertEqual(case.channel, "email")
            self.assertEqual(case.quality_target, "simple_email")
            self.assertIn("cold_outreach", case.slug)

    def test_generated_scenarios_have_message_quality_metadata(self):
        registered = ScenarioRegistry.list_all()

        for slug in MESSAGE_QUALITY_SCENARIO_SLUGS:
            scenario = registered[slug]
            metadata = scenario.get_metadata()
            self.assertEqual(metadata.category, "message_quality")
            self.assertEqual(metadata.cost_class, "high")
            self.assertIn("llm_judge", metadata.tags)
            self.assertIn("response_quality", metadata.tags)

    def test_simple_email_prompt_does_not_specify_formatting_style(self):
        case = SIMPLE_EMAIL_QUALITY_CASES[0]
        scenario = MessageQualityScenario()
        prompt = scenario._prompt(case)

        self.assertIn("Send a cold outreach email", prompt)
        self.assertNotIn("rich", prompt.lower())
        self.assertNotIn("emoji", prompt.lower())
        self.assertNotIn("color", prompt.lower())
        self.assertNotIn("table", prompt.lower())

    def test_simple_email_judge_rejects_overformatted_report_style(self):
        case = SIMPLE_EMAIL_QUALITY_CASES[0]
        question = MessageQualityScenario._judge_question(case)

        self.assertIn("restrained", question)
        self.assertIn("Fail if it looks like a report", question)
        self.assertIn("tables", question)
        self.assertIn("emoji section labels", question)

    def test_rich_email_judge_rewards_status_encoding_without_mandating_emoji(self):
        case = next(case for case in REPORT_MESSAGE_QUALITY_CASES if case.channel == "email")
        question = MessageQualityScenario._judge_question(case)

        self.assertIn("visually distinct", question)
        self.assertIn("status/value encoding", question)
        self.assertIn("Prefer tasteful emoji", question)
        self.assertIn("colored rounded inline spans as badges", question)
        self.assertIn("emoji section labels as icons or status markers", question)
        self.assertIn("factual completeness and readability strict", question)
        self.assertNotIn("emoji used tastefully", question)
        self.assertIn("preserve the supplied facts", question)
        self.assertIn("wrong audience", question)

    def test_chat_judge_does_not_require_recommendation(self):
        case = next(case for case in REPORT_MESSAGE_QUALITY_CASES if case.channel == "chat")
        question = MessageQualityScenario._judge_question(case)

        self.assertIn("status labels", question)
        self.assertIn("material factual contradictions", question)
        self.assertNotIn("recommendation", question)

    def test_short_judge_reasoning_is_retried(self):
        self.assertTrue(MessageQualityScenario._judge_reasoning_is_unusable("hello?!"))
        self.assertFalse(
            MessageQualityScenario._judge_reasoning_is_unusable(
                "The message has section headings, status labels, bullets, and enough spacing."
            )
        )

    def test_email_delivery_normalization_precedes_formatting_checks(self):
        case = next(
            case
            for case in MESSAGE_QUALITY_CASES
            if case.subject == "Daily Meme & Viral Trends Summary"
        )
        scenario = MessageQualityScenario()
        body = (
            "<html><body style='font-family: Arial'><h2>Summary</h2>"
            "<p>This plain email has enough structure for delivery inspection, "
            "but the judge owns whether it is visually polished.</p></body></html>"
        )

        failures = scenario._formatting_failures(
            case,
            {
                "to_address": case.recipient,
                "subject": "Daily Meme &amp; Viral Trends Summary",
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

    def test_delivery_basics_accept_terminal_tool_result_when_continue_flag_omitted(self):
        case = next(case for case in MESSAGE_QUALITY_CASES if case.channel == "chat")
        scenario = MessageQualityScenario()
        call = type(
            "Call",
            (),
            {
                "result": '{"status":"ok","message_id":"00000000-0000-0000-0000-000000000000","auto_sleep_ok":true}',
            },
        )

        failures = scenario._formatting_failures(
            case,
            {"body": "## Report\n\n- Complete"},
            "## Report\n\n- Complete",
            send_call=call,
        )

        self.assertEqual(failures, [])

    def test_chat_delivery_is_not_mocked_so_auditor_can_show_message(self):
        case = next(case for case in MESSAGE_QUALITY_CASES if case.channel == "chat")
        scenario = MessageQualityScenario()

        self.assertIsNone(scenario._mock_config(case))

    def test_email_delivery_stays_mocked_to_avoid_external_send(self):
        case = next(case for case in MESSAGE_QUALITY_CASES if case.channel == "email")
        scenario = MessageQualityScenario()

        self.assertEqual(set(scenario._mock_config(case)), {"send_email"})

    def test_email_cases_allow_web_chat_confirmation_as_secondary_delivery(self):
        case = next(case for case in MESSAGE_QUALITY_CASES if case.channel == "email")
        calls = [
            type("Call", (), {"tool_name": "send_email"})(),
            type("Call", (), {"tool_name": "send_chat_message"})(),
        ]

        self.assertEqual(MessageQualityScenario._unexpected_message_calls(case, calls), [])
        self.assertEqual(
            [call.tool_name for call in MessageQualityScenario._allowed_confirmation_calls(case, calls)],
            ["send_chat_message"],
        )
        self.assertIn("send_chat_message", MessageQualityScenario._allowed_tool_names(case))

    def test_email_cases_allow_sqlite_contact_lookup_preamble(self):
        case = next(case for case in MESSAGE_QUALITY_CASES if case.channel == "email")

        self.assertIn("sqlite_batch", MessageQualityScenario._allowed_tool_names(case))

    def test_email_cases_still_reject_other_secondary_delivery_channels(self):
        case = next(case for case in MESSAGE_QUALITY_CASES if case.channel == "email")
        calls = [
            type("Call", (), {"tool_name": "send_email"})(),
            type("Call", (), {"tool_name": "send_sms"})(),
        ]

        unexpected = MessageQualityScenario._unexpected_message_calls(case, calls)

        self.assertEqual([call.tool_name for call in unexpected], ["send_sms"])

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

    def test_delivery_success_requires_complete_result_and_persisted_message(self):
        delivered = object()
        complete = type(
            "Call",
            (),
            {"status": "complete", "result": '{"status":"ok"}'},
        )()
        failed = type(
            "Call",
            (),
            {"status": "complete", "result": '{"status":"error"}'},
        )()

        self.assertTrue(MessageQualityScenario._delivery_succeeded(complete, delivered))
        self.assertFalse(MessageQualityScenario._delivery_succeeded(failed, delivered))
        self.assertFalse(MessageQualityScenario._delivery_succeeded(complete, None))
