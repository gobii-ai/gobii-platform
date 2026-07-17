import uuid

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, tag

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
    RECIPIENT_MESSAGE_QUALITY_CASES,
    REPORT_MESSAGE_QUALITY_CASES,
    UNAVAILABLE_WEB_CHANNEL_CONTINUITY_SLUG,
    MessageQualityScenario,
)
from api.evals.suites import SuiteRegistry
from api.models import (
    BrowserUseAgent,
    CommsAllowlistEntry,
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentStep,
    PersistentAgentToolCall,
)


User = get_user_model()


@tag("eval_sim")
class MessageQualityScenarioTests(SimpleTestCase):
    def test_message_quality_suite_contains_all_generated_scenarios(self):
        suite = SuiteRegistry.get(MESSAGE_QUALITY_SUITE_SLUG)

        self.assertIsNotNone(suite)
        self.assertEqual(tuple(suite.scenario_slugs), MESSAGE_QUALITY_SCENARIO_SLUGS)
        self.assertEqual(len(suite.scenario_slugs), 24)
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

    def test_recipient_message_evals_cover_email_chat_sms_and_rely_on_base_behavior(self):
        self.assertEqual({case.channel for case in RECIPIENT_MESSAGE_QUALITY_CASES}, {"email", "chat", "sms"})
        case = RECIPIENT_MESSAGE_QUALITY_CASES[0]
        prompt = MessageQualityScenario()._prompt(case).lower()
        question = MessageQualityScenario._judge_question(case)

        self.assertNotIn("human", prompt)
        self.assertNotIn("natural", prompt)
        self.assertNotIn("dash", prompt)
        self.assertIn("grounded, natural message", question)
        self.assertIn("padded assistant cadence", question)

    def test_recipient_message_prompts_do_not_leak_style_rubric(self):
        forbidden_terms = ("invented familiarity", "template language", "assistant cadence", "low pressure")

        for case in RECIPIENT_MESSAGE_QUALITY_CASES:
            prompt = MessageQualityScenario()._prompt(case).lower()
            for term in forbidden_terms:
                self.assertNotIn(term, prompt)

    def test_evidence_acknowledgement_eval_does_not_name_the_judge_guidance(self):
        case = next(case for case in RECIPIENT_MESSAGE_QUALITY_CASES if "evidence_acknowledgement" in case.slug)
        prompt = MessageQualityScenario()._prompt(case).lower()
        question = MessageQualityScenario._judge_question(case)

        self.assertNotIn("exactly the kind", prompt)
        self.assertNotIn("formulaic", prompt)
        self.assertNotIn("evaluative praise", prompt)
        self.assertIn("evaluative praise", question)
        self.assertIn("formulaic concessions", question)
        self.assertIn("exactly the evidence needed", question)

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

    def test_delivery_basics_allow_intentional_punctuation(self):
        case = RECIPIENT_MESSAGE_QUALITY_CASES[0]
        body = "The copy finished—Morgan is checking two invoices now."

        failures = MessageQualityScenario()._formatting_failures(
            case,
            {"body": body, "will_continue_work": False},
            body,
        )

        self.assertEqual(failures, [])

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

    def test_sms_delivery_stays_mocked_to_avoid_external_send(self):
        case = next(case for case in MESSAGE_QUALITY_CASES if case.channel == "sms")

        self.assertEqual(set(MessageQualityScenario()._mock_config(case)), {"send_sms"})

    def test_exact_copy_case_preserves_subject_body_punctuation_emoji_and_html(self):
        case = next(case for case in RECIPIENT_MESSAGE_QUALITY_CASES if case.quality_target == "exact_copy")
        params = {
            "to_address": case.recipient,
            "subject": case.exact_subject,
            "mobile_first_html": case.exact_body,
            "will_continue_work": False,
        }

        self.assertEqual(MessageQualityScenario()._formatting_failures(case, params, case.exact_body), [])
        judge_context = MessageQualityScenario._judge_context(case, params, case.exact_body)
        self.assertIn(f"Expected exact subject:\n{case.exact_subject}", judge_context)
        self.assertIn(f"Expected exact body:\n{case.exact_body}", judge_context)

        changed = {**params, "subject": "Launch note approved", "mobile_first_html": "<p>Rewritten.</p>"}
        failures = MessageQualityScenario()._formatting_failures(case, changed, changed["mobile_first_html"])
        self.assertIn("Exact-copy email subject was changed.", failures)
        self.assertIn("Exact-copy email body was changed.", failures)

    def test_recipient_message_checks_placeholders_fake_reply_subjects_and_sms_recipient(self):
        email_case = next(
            case
            for case in RECIPIENT_MESSAGE_QUALITY_CASES
            if case.channel == "email" and not case.is_followup and case.quality_target == "recipient_message"
        )
        failures = MessageQualityScenario()._formatting_failures(
            email_case,
            {
                "to_address": email_case.recipient,
                "subject": "Re: Update",
                "mobile_first_html": "<p>Hi {{first_name}}</p>",
                "will_continue_work": False,
            },
            "<p>Hi {{first_name}}</p>",
        )
        self.assertIn("Initial email should not use a fake reply or forward subject.", failures)
        self.assertIn("Message contains an unresolved placeholder.", failures)

        sms_case = next(case for case in RECIPIENT_MESSAGE_QUALITY_CASES if case.channel == "sms")
        failures = MessageQualityScenario()._formatting_failures(
            sms_case,
            {"to_number": "+15555559999", "body": "Moved to 3 PM.", "will_continue_work": False},
            "Moved to 3 PM.",
        )
        self.assertIn(f"send_sms.to_number should be {sms_case.recipient}.", failures)

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


@tag("eval_sim")
class MessageQualityFollowupThreadTests(TestCase):
    def setUp(self):
        suffix = uuid.uuid4().hex
        self.user = User.objects.create_user(
            username=f"message-quality-eval-{suffix}@example.com",
            email=f"message-quality-eval-{suffix}@example.com",
            password="password",
        )
        browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name=f"message-quality-browser-{suffix}",
        )
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Message Quality Eval Agent",
            charter="Send approved messages.",
            browser_use_agent=browser_agent,
        )

    def test_sms_case_prepares_a_mock_safe_sender_and_recipient(self):
        case = next(case for case in RECIPIENT_MESSAGE_QUALITY_CASES if case.channel == "sms")

        MessageQualityScenario()._prepare_agent_for_case(str(self.agent.id), case)

        self.assertTrue(
            PersistentAgentCommsEndpoint.objects.filter(
                owner_agent=self.agent,
                channel=CommsChannel.SMS,
                is_primary=True,
            ).exists()
        )
        allowlist = CommsAllowlistEntry.objects.get(
            agent=self.agent,
            channel=CommsChannel.SMS,
            address=case.recipient,
        )
        self.assertTrue(allowlist.allow_outbound)
        self.assertTrue(allowlist.sms_contact_permission_attested)

    def test_followup_requires_the_seeded_reply_message_id(self):
        scenario = MessageQualityScenario()
        case = next(case for case in RECIPIENT_MESSAGE_QUALITY_CASES if case.is_followup)
        prior = scenario._seed_prior_message(self.agent, case)
        step = PersistentAgentStep.objects.create(agent=self.agent, description="Send follow-up")
        body = (
            "<p>Hi Maya,</p>"
            "<p>We put together a short duplicate-invoice checklist for growing AP teams. "
            "Would it be useful if I sent it over?</p>"
            "<p>If not, no problem.</p><p>Thanks,<br>Elena</p>"
        )
        params = {
            "to_address": case.recipient,
            "subject": f"Re: {case.prior_subject}",
            "reply_to_message_id": str(prior.id),
            "mobile_first_html": body,
            "will_continue_work": False,
        }
        send_call = PersistentAgentToolCall.objects.create(
            step=step,
            tool_name="send_email",
            tool_params=params,
        )

        self.assertEqual(scenario._formatting_failures(case, params, body, send_call=send_call), [])

        wrong_thread_params = {**params, "reply_to_message_id": str(uuid.uuid4())}
        failures = scenario._formatting_failures(
            case,
            wrong_thread_params,
            body,
            send_call=send_call,
        )
        self.assertIn("Follow-up email should reply in the seeded thread.", failures)
