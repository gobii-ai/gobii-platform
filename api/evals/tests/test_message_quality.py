from django.test import SimpleTestCase, tag

import api.evals.loader  # noqa: F401 - registers scenarios and suites
from api.evals.registry import ScenarioRegistry
from api.evals.scenarios.message_quality import (
    FAILED_EMAIL_DELIVERY_RECOVERY_SLUG,
    FailedEmailDeliveryRecoveryScenario,
    MESSAGE_QUALITY_CASES,
    MESSAGE_QUALITY_SCENARIO_SLUGS,
    MESSAGE_QUALITY_SUITE_SLUG,
    OWNER_UPDATE_QUALITY_CASES,
    PORTFOLIO_REPORT_QUALITY_CASES,
    REPLY_CHANNEL_CONTINUITY_SLUG,
    REPORT_MESSAGE_QUALITY_CASES,
    SIMPLE_EMAIL_QUALITY_CASES,
    UNAVAILABLE_WEB_CHANNEL_CONTINUITY_SLUG,
    HUMAN_MESSAGE_QUALITY_CASES,
    MessageQualityScenario,
)
from api.evals.suites import SuiteRegistry


@tag("eval_sim")
class MessageQualityScenarioTests(SimpleTestCase):
    def test_message_quality_suite_contains_all_generated_scenarios(self):
        suite = SuiteRegistry.get(MESSAGE_QUALITY_SUITE_SLUG)

        self.assertIsNotNone(suite)
        self.assertEqual(tuple(suite.scenario_slugs), MESSAGE_QUALITY_SCENARIO_SLUGS)
        self.assertEqual(len(suite.scenario_slugs), 19)
        self.assertIn(REPLY_CHANNEL_CONTINUITY_SLUG, suite.scenario_slugs)
        self.assertIn(UNAVAILABLE_WEB_CHANNEL_CONTINUITY_SLUG, suite.scenario_slugs)
        self.assertIn(FAILED_EMAIL_DELIVERY_RECOVERY_SLUG, suite.scenario_slugs)

    def test_generated_cases_cover_email_and_chat_for_each_real_world_domain(self):
        channels_by_brief = {}
        for case in REPORT_MESSAGE_QUALITY_CASES:
            channels_by_brief.setdefault(case.brief, set()).add(case.channel)
            self.assertTrue(case.source_example_ids)

        self.assertEqual(len(channels_by_brief), 5)
        self.assertTrue(all(channels == {"email", "chat"} for channels in channels_by_brief.values()))

    def test_generated_scenarios_have_message_quality_metadata(self):
        registered = ScenarioRegistry.list_all()

        for slug in (case.slug for case in MESSAGE_QUALITY_CASES):
            scenario = registered[slug]
            metadata = scenario.get_metadata()
            self.assertEqual(metadata.category, "message_quality")
            self.assertEqual(metadata.cost_class, "high")
            self.assertIn("llm_judge", metadata.tags)
            self.assertIn("response_quality", metadata.tags)

        reply_channel_metadata = registered[REPLY_CHANNEL_CONTINUITY_SLUG].get_metadata()
        self.assertEqual(reply_channel_metadata.category, "message_quality")
        self.assertEqual(reply_channel_metadata.cost_class, "low")
        self.assertIn("reply_channel", reply_channel_metadata.tags)

        unavailable_metadata = registered[UNAVAILABLE_WEB_CHANNEL_CONTINUITY_SLUG].get_metadata()
        self.assertEqual(unavailable_metadata.cost_class, "low")
        self.assertIn("reply_channel", unavailable_metadata.tags)
        self.assertIn("tool_failure", unavailable_metadata.tags)

        failure_metadata = registered[FAILED_EMAIL_DELIVERY_RECOVERY_SLUG].get_metadata()
        self.assertEqual(failure_metadata.category, "message_quality")
        self.assertEqual(failure_metadata.cost_class, "low")
        self.assertIn("tool_failure", failure_metadata.tags)

    def test_failed_delivery_notice_requires_clear_non_delivery_language(self):
        self.assertTrue(
            FailedEmailDeliveryRecoveryScenario._notice_reports_failure(
                "The email got rejected by the provider. Nothing was delivered."
            )
        )
        self.assertFalse(
            FailedEmailDeliveryRecoveryScenario._notice_reports_failure(
                "The email was sent successfully."
            )
        )

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
        self.assertIn("naturally written", question)

    def test_human_message_eval_relies_on_base_behavior(self):
        case = HUMAN_MESSAGE_QUALITY_CASES[0]
        prompt = MessageQualityScenario()._prompt(case).lower()
        question = MessageQualityScenario._judge_question(case)

        self.assertNotIn("human", prompt)
        self.assertNotIn("natural", prompt)
        self.assertNotIn("dash", prompt)
        self.assertIn("natural message", question)
        self.assertIn("assistant cadence", question)

    def test_human_reply_eval_does_not_name_the_slop_patterns(self):
        case = next(case for case in HUMAN_MESSAGE_QUALITY_CASES if case.quality_target == "human_reply")
        prompt = MessageQualityScenario()._prompt(case).lower()
        question = MessageQualityScenario._judge_question(case)

        self.assertNotIn("exactly the kind", prompt)
        self.assertNotIn("formulaic", prompt)
        self.assertNotIn("evaluating", prompt)
        self.assertIn("evaluating or praising", question)
        self.assertIn("such as 'even if'", question)
        self.assertIn("exactly the kind of evidence", question)

    def test_owner_update_eval_does_not_name_the_desired_format(self):
        case = OWNER_UPDATE_QUALITY_CASES[0]
        prompt = MessageQualityScenario()._prompt(case).lower()
        question = MessageQualityScenario._judge_question(case)

        self.assertNotIn("report", prompt)
        self.assertNotIn("dashboard", prompt)
        self.assertNotIn("markdown", prompt)
        self.assertNotIn("table", prompt)
        self.assertIn("owner of the work", question)
        self.assertIn("easy to scan", question)

    def test_portfolio_eval_is_natural_and_does_not_prescribe_workflow_or_format(self):
        case = PORTFOLIO_REPORT_QUALITY_CASES[0]
        prompt = MessageQualityScenario()._prompt(case).lower()

        self.assertIn("portfolio and founder update", prompt)
        for prescription in ("sqlite", "markdown", "heading", "bullet", "table", "emoji"):
            with self.subTest(prescription=prescription):
                self.assertNotIn(prescription, prompt)

    def test_portfolio_eval_detects_an_omitted_canonical_entity(self):
        case = PORTFOLIO_REPORT_QUALITY_CASES[0]
        body = (
            "## Portfolio update\n\n"
            "Juniper Vale is led by Maya Solis. Copperline Health was founded by Theo Grant and Nina Park."
        )

        failures = MessageQualityScenario()._formatting_failures(
            case,
            {"body": body, "will_continue_work": False},
            body,
        )

        self.assertIn("Message omitted required entities: Harborlight Robotics.", failures)

    def test_portfolio_and_generic_chat_judges_reward_flexible_hierarchy(self):
        portfolio_question = MessageQualityScenario._judge_question(PORTFOLIO_REPORT_QUALITY_CASES[0])
        generic_case = next(case for case in REPORT_MESSAGE_QUALITY_CASES if case.channel == "chat")
        generic_question = MessageQualityScenario._judge_question(generic_case)

        for question in (portfolio_question, generic_question):
            with self.subTest(question=question):
                self.assertIn("proportionate hierarchy", question)
                self.assertIn("repetitive flat catalog wall", question)
        self.assertIn("Do not require emoji, a table", generic_question)

    def test_rich_email_judge_rewards_status_encoding_without_mandating_emoji(self):
        case = next(case for case in REPORT_MESSAGE_QUALITY_CASES if case.channel == "email")
        question = MessageQualityScenario._judge_question(case)

        self.assertIn("visually distinct", question)
        self.assertIn("status/value encoding", question)
        self.assertIn("Prefer tasteful emoji", question)
        self.assertNotIn("emoji used tastefully", question)

    def test_chat_judge_does_not_require_recommendation(self):
        case = next(case for case in REPORT_MESSAGE_QUALITY_CASES if case.channel == "chat")
        question = MessageQualityScenario._judge_question(case)

        self.assertIn("status labels", question)
        self.assertIn("options, not requirements", question)
        self.assertNotIn("recommendation", question)

    def test_short_judge_reasoning_is_retried(self):
        self.assertTrue(MessageQualityScenario._judge_reasoning_is_unusable("hello?!"))
        self.assertFalse(
            MessageQualityScenario._judge_reasoning_is_unusable(
                "The message has section headings, status labels, bullets, and enough spacing."
            )
        )

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

    def test_delivery_basics_reject_ai_dash_tells(self):
        case = HUMAN_MESSAGE_QUALITY_CASES[0]
        for body in (
            "The copy finished—Morgan is checking two invoices now.",
            "The copy finished - Morgan is checking two invoices now.",
        ):
            with self.subTest(body=body):
                failures = MessageQualityScenario()._formatting_failures(
                    case,
                    {"body": body, "will_continue_work": False},
                    body,
                )

                self.assertIn("Recipient-facing prose used prohibited dash punctuation", failures[0])

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
