import uuid

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, tag

import api.evals.loader  # noqa: F401 - registers scenarios and suites
from api.evals.registry import ScenarioRegistry
from api.evals.scenarios.outreach import (
    OUTREACH_CASES,
    OUTREACH_SCENARIO_SLUGS,
    OUTREACH_SUITE_SLUG,
    OutreachScenario,
)
from api.evals.suites import SuiteRegistry
from api.models import BrowserUseAgent, PersistentAgent, PersistentAgentStep, PersistentAgentToolCall


User = get_user_model()


@tag("eval_sim")
class OutreachScenarioTests(SimpleTestCase):
    def test_outreach_suite_contains_exactly_five_cross_domain_judge_cases(self):
        suite = SuiteRegistry.get(OUTREACH_SUITE_SLUG)

        self.assertIsNotNone(suite)
        self.assertEqual(tuple(suite.scenario_slugs), OUTREACH_SCENARIO_SLUGS)
        self.assertEqual(len(suite.scenario_slugs), 5)
        self.assertEqual(
            {case.domain for case in OUTREACH_CASES},
            {"cold_sales", "recruiting", "customer_success", "partnership", "followup"},
        )

    def test_all_outreach_scenarios_use_the_real_harness_and_llm_judge(self):
        registered = ScenarioRegistry.list_all()

        for slug in OUTREACH_SCENARIO_SLUGS:
            scenario = registered[slug]
            metadata = scenario.get_metadata()
            self.assertEqual(metadata.category, "outreach")
            self.assertEqual(metadata.area, "system_skills")
            self.assertIn("human_output", metadata.tags)
            self.assertIn("llm_judge", metadata.tags)
            self.assertIn(
                "llm_judge",
                {task.assertion_type for task in scenario.tasks},
            )

    def test_prompts_do_not_leak_the_style_rubric(self):
        scenario = OutreachScenario()
        forbidden_prompt_terms = (
            "em dash",
            "human-sounding",
            "emoji",
            "report-style",
            "unresolved placeholder",
            "80 to 180",
            "3 to 7 words",
        )

        for case in OUTREACH_CASES:
            prompt = scenario._prompt(case).lower()
            self.assertIn(case.recipient, prompt)
            self.assertIn("send the email now", prompt)
            for term in forbidden_prompt_terms:
                self.assertNotIn(term, prompt)

    def test_all_external_sends_are_mocked(self):
        scenario = OutreachScenario()

        for case in OUTREACH_CASES:
            mock_config = scenario._mock_config(case)
            self.assertEqual(set(mock_config), {"send_email"})
            self.assertEqual(mock_config["send_email"]["status"], "ok")

    def test_shared_judge_requires_grounded_human_outreach(self):
        case = OUTREACH_CASES[0]
        question = OutreachScenario._judge_question(case)

        self.assertIn("thoughtful human", question)
        self.assertIn("grounded in the supplied facts", question)
        self.assertIn("one low-pressure next step", question)
        self.assertIn("unresolved placeholders", question)
        self.assertIn("em dashes", question)
        self.assertIn("report-style headings or tables", question)

    def test_formatting_checks_reject_outreach_regressions(self):
        scenario = OutreachScenario()
        case = OUTREACH_CASES[0]
        body = (
            "<h2 style='color: blue'>Opportunity 🚀</h2>"
            "<p>Hi {{first_name}}, this changes everything — let us talk.</p>"
            "<table><tr><td>Metric</td></tr></table>"
            "\n## Results\n- First item"
        )

        failures = scenario._formatting_failures(
            case,
            {
                "to_address": case.recipient,
                "subject": "Re: Big opportunity 🚀",
                "mobile_first_html": body,
                "will_continue_work": False,
            },
            body,
        )

        self.assertIn("Outreach should not use em dashes.", failures)
        self.assertIn("Outreach should not use emoji or decorative symbols.", failures)
        self.assertIn("Outreach contains an unresolved placeholder.", failures)
        self.assertIn("Outreach should not use report-style headings, tables, or lists.", failures)
        self.assertIn("Outreach should not use Markdown headings or lists.", failures)
        self.assertIn("Outreach should not use decorative style or class attributes.", failures)
        self.assertIn("Initial outreach should not use a fake reply or forward subject.", failures)

    def test_restrained_outreach_passes_deterministic_formatting_checks(self):
        scenario = OutreachScenario()
        case = OUTREACH_CASES[0]
        body = (
            "<p>Hi Maya,</p>"
            "<p>Northstar's AP Manager opening caught my attention. Ridge Analytics flags unusual vendor spend "
            "and duplicate invoice risk as finance teams grow.</p>"
            "<p>Are you open to a 15-minute introduction next week?</p>"
            "<p>Thanks,<br>Elena</p>"
        )

        failures = scenario._formatting_failures(
            case,
            {
                "to_address": case.recipient,
                "subject": "AP controls during team growth",
                "mobile_first_html": body,
                "will_continue_work": False,
            },
            body,
        )

        self.assertEqual(failures, [])

    def test_followup_case_requires_existing_thread_without_prompting_the_style(self):
        followup = next(case for case in OUTREACH_CASES if case.domain == "followup")
        prompt = OutreachScenario()._prompt(followup)

        self.assertTrue(followup.is_followup)
        self.assertTrue(followup.prior_subject)
        self.assertTrue(followup.prior_body)
        self.assertIn("existing thread", prompt)
        self.assertNotIn("low pressure", prompt.lower())


@tag("eval_sim")
class OutreachFollowupThreadTests(TestCase):
    def setUp(self):
        suffix = uuid.uuid4().hex
        self.user = User.objects.create_user(
            username=f"outreach-eval-{suffix}@example.com",
            email=f"outreach-eval-{suffix}@example.com",
            password="password",
        )
        browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name=f"outreach-eval-browser-{suffix}",
        )
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Outreach Eval Agent",
            charter="Send approved outreach.",
            browser_use_agent=browser_agent,
        )

    def test_followup_formatting_requires_seeded_reply_message_id(self):
        scenario = OutreachScenario()
        case = next(case for case in OUTREACH_CASES if case.domain == "followup")
        prior = scenario._seed_prior_message(self.agent, case)
        step = PersistentAgentStep.objects.create(agent=self.agent, description="Send follow-up")
        body = (
            "<p>Hi Maya,</p>"
            "<p>We put together a short duplicate-invoice checklist for growing AP teams. Would it be useful if I "
            "sent it over?</p>"
            "<p>If not, no problem.</p>"
            "<p>Thanks,<br>Elena</p>"
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

        failures = scenario._formatting_failures(case, params, body, send_call=send_call)
        self.assertNotIn("Follow-up outreach should reply in the seeded email thread.", failures)

        wrong_thread_params = {**params, "reply_to_message_id": str(uuid.uuid4())}
        failures = scenario._formatting_failures(
            case,
            wrong_thread_params,
            body,
            send_call=send_call,
        )
        self.assertIn("Follow-up outreach should reply in the seeded email thread.", failures)
