from types import SimpleNamespace
from unittest.mock import patch

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, tag

import api.evals.loader  # noqa: F401 - registers scenarios and suites
from api.agent.core.event_processing import _get_completed_process_run_count
from api.agent.core.prompt_context import build_prompt_context_preview
from api.agent.core.tool_results import _wrap_as_sqlite_result
from api.agent.tools.create_chart import get_create_chart_tool
from api.agent.tools.eval_synthetic_tools import EVAL_SYNTHETIC_TOOL_DEFINITIONS
from api.agent.tools.plan import get_update_plan_tool
from api.agent.tools.request_contact_permission import get_request_contact_permission_tool
from api.agent.tools.request_human_input import execute_request_human_input, get_request_human_input_tool
from api.agent.tools.web_chat_sender import execute_send_chat_message
from api.evals.scenarios.effort_calibration import (
    EFFORT_CALIBRATION_SCENARIO_SLUGS,
    EffortTrivialAnswerStopsScenario,
)
from api.evals.stop_policy import should_stop_for_eval_policy
from api.evals.suites import SuiteRegistry
from api.models import (
    BrowserUseAgent,
    CommsChannel,
    EvalRun,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentMessage,
    PersistentAgentStep,
    PersistentAgentToolCall,
)


@tag("eval_sim")
class EffortCalibrationSuiteTests(SimpleTestCase):
    def test_effort_calibration_suite_contains_expected_scenarios(self):
        suite = SuiteRegistry.get("effort_calibration")

        self.assertIsNotNone(suite)
        self.assertEqual(suite.scenario_slugs, EFFORT_CALIBRATION_SCENARIO_SLUGS)

    def test_chart_tool_description_requires_request_or_material_need(self):
        description = get_create_chart_tool()["function"]["description"]

        self.assertIn("when the user requests a chart or a visual is materially necessary", description)
        self.assertIn("Do not use this for routine summaries just because numbers are present", description)

    def test_plan_tool_description_excludes_simple_one_shot_work(self):
        description = get_update_plan_tool()["function"]["description"]

        self.assertIn("real multi-step work", description)
        self.assertIn("Do not use for quick lookups", description)
        self.assertIn("one-shot chart requests", description)

    def test_contact_permission_description_defers_setup_only_future_sends(self):
        description = get_request_contact_permission_tool()["function"]["description"]

        self.assertIn("do not request contact permission during setup", description)
        self.assertIn("when an actual outbound send is needed", description)

    def test_human_input_description_excludes_category_choice_surveys(self):
        description = get_request_human_input_tool()["function"]["description"]

        self.assertIn("category example choices", description)
        self.assertIn("which vendor/company", description)
        self.assertIn("choose and disclose afterward", description)

    def test_linkedin_jobs_synthetic_tool_accepts_category_queries(self):
        description = EVAL_SYNTHETIC_TOOL_DEFINITIONS["mcp_brightdata_web_data_linkedin_job_listings"][
            "description"
        ]

        self.assertIn("category query", description)
        self.assertIn("representative category such as a fintech company", description)
        self.assertIn("instead of asking which company", description)

    def test_fresh_full_tool_result_wrapper_discourages_redundant_sqlite_rereads(self):
        wrapped = _wrap_as_sqlite_result('{"answer": "ready"}', 19)

        self.assertIn("reply directly in the next message", wrapped)
        self.assertIn("Do not query __tool_results or sqlite_batch just to reread", wrapped)
        self.assertIn("use SQL only for real filtering", wrapped)


@tag("eval_sim")
class EvalStopPolicyBudgetTests(TestCase):
    def test_relevant_tool_call_budget_ignores_config_bookkeeping_reads(self):
        User = get_user_model()
        user = User.objects.create_user(username="eval_budget_user")
        browser_agent = BrowserUseAgent.objects.create(user=user, name="Eval Budget Browser")
        agent = PersistentAgent.objects.create(
            name="Eval Budget Agent",
            user=user,
            browser_use_agent=browser_agent,
            execution_environment="eval",
            charter="Test agent.",
        )
        run = EvalRun.objects.create(
            scenario_slug="effort_test",
            scenario_version="1.0.0",
            agent=agent,
            initiated_by=user,
        )

        bookkeeping_step = PersistentAgentStep.objects.create(agent=agent, eval_run=run)
        PersistentAgentToolCall.objects.create(
            step=bookkeeping_step,
            tool_name="sqlite_batch",
            tool_params={"sql": "SELECT charter, schedule FROM __agent_config WHERE id = 1;"},
            result='{"status":"ok"}',
        )
        relevant_step = PersistentAgentStep.objects.create(agent=agent, eval_run=run)
        PersistentAgentToolCall.objects.create(
            step=relevant_step,
            tool_name="http_request",
            tool_params={"url": "https://example.test/data.json"},
            result='{"status":"ok"}',
        )

        should_stop, reason = should_stop_for_eval_policy(
            str(run.id),
            {"max_relevant_tool_calls": 1},
        )

        self.assertTrue(should_stop)
        self.assertIn("relevant tool call budget reached: 1/1", reason)


@tag("eval_sim")
class EffortCalibrationHarnessTests(TestCase):
    def test_ready_agent_seeds_completed_process_run(self):
        User = get_user_model()
        user = User.objects.create_user(username="effort_ready_user")
        browser_agent = BrowserUseAgent.objects.create(user=user, name="Effort Ready Browser")
        agent = PersistentAgent.objects.create(
            name="Effort Ready Agent",
            user=user,
            browser_use_agent=browser_agent,
            execution_environment="eval",
            charter="Test agent.",
        )

        EffortTrivialAnswerStopsScenario()._ready_agent(str(agent.id))

        self.assertEqual(_get_completed_process_run_count(agent), 1)

    def test_send_chat_rejects_schema_placeholder_body(self):
        User = get_user_model()
        user = User.objects.create_user(username="placeholder_chat_user")
        browser_agent = BrowserUseAgent.objects.create(user=user, name="Placeholder Chat Browser")
        agent = PersistentAgent.objects.create(
            name="Placeholder Chat Agent",
            user=user,
            browser_use_agent=browser_agent,
            execution_environment="eval",
            charter="Test agent.",
        )

        result = execute_send_chat_message(agent, {"body": "body", "will_continue_work": False})

        self.assertEqual(result["status"], "error")
        self.assertIn("schema placeholder", result["message"])
        self.assertFalse(PersistentAgentMessage.objects.filter(owner_agent=agent, is_outbound=True).exists())

    def test_send_chat_rejects_raw_tool_call_markup_body(self):
        User = get_user_model()
        user = User.objects.create_user(username="tool_markup_chat_user")
        browser_agent = BrowserUseAgent.objects.create(user=user, name="Tool Markup Chat Browser")
        agent = PersistentAgent.objects.create(
            name="Tool Markup Chat Agent",
            user=user,
            browser_use_agent=browser_agent,
            execution_environment="eval",
            charter="Test agent.",
        )

        result = execute_send_chat_message(
            agent,
            {
                "body": (
                    '<function><invoke name="http_request"><parameter name="url">'
                    "https://api.example.test/data.json</parameter></invoke></function>"
                ),
                "will_continue_work": False,
            },
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("raw tool-call markup", result["message"])
        self.assertFalse(PersistentAgentMessage.objects.filter(owner_agent=agent, is_outbound=True).exists())

    def test_send_chat_rejects_leaked_thinking_tag(self):
        User = get_user_model()
        user = User.objects.create_user(username="thinking_tag_chat_user")
        browser_agent = BrowserUseAgent.objects.create(user=user, name="Thinking Tag Chat Browser")
        agent = PersistentAgent.objects.create(
            name="Thinking Tag Chat Agent",
            user=user,
            browser_use_agent=browser_agent,
            execution_environment="eval",
            charter="Test agent.",
        )

        result = execute_send_chat_message(agent, {"body": "<endor_thinking>", "will_continue_work": True})

        self.assertEqual(result["status"], "error")
        self.assertIn("raw tool-call markup", result["message"])
        self.assertFalse(PersistentAgentMessage.objects.filter(owner_agent=agent, is_outbound=True).exists())

    def test_send_chat_skips_progress_only_message_before_any_reply(self):
        User = get_user_model()
        user = User.objects.create_user(username="progress_only_chat_user")
        browser_agent = BrowserUseAgent.objects.create(user=user, name="Progress Only Chat Browser")
        agent = PersistentAgent.objects.create(
            name="Progress Only Chat Agent",
            user=user,
            browser_use_agent=browser_agent,
            execution_environment="eval",
            charter="Test agent.",
        )

        result = execute_send_chat_message(
            agent,
            {
                "body": "Got what I need from the search - let me also grab the full profile for any extra detail.",
                "will_continue_work": True,
            },
        )

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["skipped"])
        self.assertFalse(PersistentAgentMessage.objects.filter(owner_agent=agent, is_outbound=True).exists())

    def test_send_chat_skips_optional_progress_question(self):
        User = get_user_model()
        user = User.objects.create_user(username="optional_progress_chat_user")
        browser_agent = BrowserUseAgent.objects.create(user=user, name="Optional Progress Chat Browser")
        agent = PersistentAgent.objects.create(
            name="Optional Progress Chat Agent",
            user=user,
            browser_use_agent=browser_agent,
            execution_environment="eval",
            charter="Test agent.",
        )

        result = execute_send_chat_message(
            agent,
            {
                "body": (
                    "I'll get the RSS feed parsed and the schedule wired up now. "
                    "Any tweaks before I lock this in? Otherwise I'm off and running!"
                ),
                "will_continue_work": True,
            },
        )

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["skipped"])
        self.assertFalse(PersistentAgentMessage.objects.filter(owner_agent=agent, is_outbound=True).exists())

    def test_send_chat_strips_trailing_optional_followup_from_final_answer(self):
        User = get_user_model()
        user = User.objects.create_user(username="optional_followup_final_user")
        browser_agent = BrowserUseAgent.objects.create(user=user, name="Optional Followup Final Browser")
        agent = PersistentAgent.objects.create(
            name="Optional Followup Final Agent",
            user=user,
            browser_use_agent=browser_agent,
            execution_environment="eval",
            charter="Test agent.",
        )

        result = execute_send_chat_message(
            agent,
            {
                "body": (
                    "## Bitcoin Price\n\n"
                    "**$68,500.50 USD**\n\n"
                    "> Markets move fast though—want me to keep an eye on it for you? 😊"
                ),
                "will_continue_work": False,
            },
        )

        self.assertEqual(result["status"], "ok")
        message = PersistentAgentMessage.objects.get(owner_agent=agent, is_outbound=True)
        self.assertEqual(message.body, "## Bitcoin Price\n\n**$68,500.50 USD**\n\n> Markets move fast though")

    def test_request_human_input_rejects_large_preference_survey_outside_planning(self):
        agent = SimpleNamespace(planning_state=PersistentAgent.PlanningState.SKIPPED)

        result = execute_request_human_input(
            agent,
            {
                "question": "Which fintech company should I use?",
                "options": [
                    {"title": "Stripe", "description": "Payments infrastructure"},
                    {"title": "Plaid", "description": "Financial data APIs"},
                    {"title": "Chime", "description": "Consumer digital banking"},
                    {"title": "Affirm", "description": "Buy now, pay later"},
                ],
                "will_continue_work": False,
            },
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("not preference surveys", result["message"])
        self.assertIn("choose a reasonable default", result["message"])


@tag("eval_sim")
class FirstRunPromptCalibrationTests(TestCase):
    def test_first_run_prompt_does_not_force_progress_greeting_or_default_schedule(self):
        User = get_user_model()
        user = User.objects.create_user(
            username="first-run-effort@example.com",
            email="first-run-effort@example.com",
        )
        EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
        browser_agent = BrowserUseAgent.objects.create(user=user, name="First Run Effort Browser")
        endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.WEB,
            address="first-run-effort-web",
        )
        agent = PersistentAgent.objects.create(
            name="First Run Effort Agent",
            user=user,
            browser_use_agent=browser_agent,
            execution_environment="eval",
            charter="Answer directly.",
            preferred_contact_endpoint=endpoint,
        )

        with patch("api.agent.core.prompt_context.ensure_steps_compacted"), patch(
            "api.agent.core.prompt_context.ensure_comms_compacted"
        ), patch(
            "api.agent.core.prompt_context.get_llm_config_with_failover",
            return_value=[("endpoint", "openai/gpt-4o-mini", {})],
        ):
            context, _, _ = build_prompt_context_preview(agent, is_first_run=True)

        system_prompt = next(message["content"] for message in context if message["role"] == "system")
        self.assertIn("If a concrete user task, scheduled trigger, or deliverable is already active", system_prompt)
        self.assertIn("Stopping without a schedule is correct for one-time work", system_prompt)
        self.assertIn("## Configuration Discipline (CRITICAL)", system_prompt)
        self.assertIn("## Plan Discipline (CRITICAL)", system_prompt)
        self.assertIn("A finished answer, briefing, chart, or lookup is not a charter change.", system_prompt)
        self.assertIn("update charter/schedule once and stop", system_prompt)
        self.assertIn("do not request contact permission during setup", system_prompt)
        self.assertIn("After simple facts, prices, statuses, exact lookups", system_prompt)
        self.assertIn("a fintech company", system_prompt)
        self.assertIn("Do not turn these into company-choice surveys", system_prompt)
        self.assertIn("Do not use sqlite_batch to reread __tool_results", system_prompt)
        self.assertNotIn("Before ANY tool calls", system_prompt)
        self.assertNotIn("Greeting comes first, always", system_prompt)
        self.assertNotIn("Schedule: When in doubt, set one", system_prompt)
        self.assertNotIn("Without a schedule, you die", system_prompt)

    def test_planning_mode_prompt_ends_clear_feed_setup_before_execution(self):
        User = get_user_model()
        user = User.objects.create_user(
            username="planning-feed-effort@example.com",
            email="planning-feed-effort@example.com",
        )
        EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
        browser_agent = BrowserUseAgent.objects.create(user=user, name="Planning Feed Effort Browser")
        endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.WEB,
            address="planning-feed-effort-web",
        )
        agent = PersistentAgent.objects.create(
            name="Planning Feed Effort Agent",
            user=user,
            browser_use_agent=browser_agent,
            execution_environment="eval",
            charter="Plan before executing.",
            planning_state=PersistentAgent.PlanningState.PLANNING,
            preferred_contact_endpoint=endpoint,
        )

        with patch("api.agent.core.prompt_context.ensure_steps_compacted"), patch(
            "api.agent.core.prompt_context.ensure_comms_compacted"
        ), patch(
            "api.agent.core.prompt_context.get_llm_config_with_failover",
            return_value=[("endpoint", "openai/gpt-4o-mini", {})],
        ):
            context, _, _ = build_prompt_context_preview(agent, is_first_run=True)

        system_prompt = next(message["content"] for message in context if message["role"] == "system")
        self.assertIn("For clear setup requests, especially scheduled digests", system_prompt)
        self.assertIn("Do not validate, fetch, parse, or test provided URLs", system_prompt)
        self.assertIn("call the welcome send tool and end_planning in the same response", system_prompt)
        self.assertIn("Do not say you will check, validate, test, fetch, or inspect a provided feed", system_prompt)
