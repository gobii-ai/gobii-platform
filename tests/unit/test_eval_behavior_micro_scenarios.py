from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

import api.evals.loader  # noqa: F401 - registers scenarios and suites
from api.agent.tools.eval_synthetic_tools import EVAL_SYNTHETIC_TOOL_SERVER
from api.agent.tools.search_tools import search_tools
from api.agent.tools.tool_manager import get_enabled_tool_definitions
from api.evals.registry import ScenarioRegistry
from api.evals.scenarios.behavior_micro import (
    BEHAVIOR_MICRO_SCENARIO_SLUGS,
    CommonUseCaseEvalDefinition,
    COMMON_USE_CASE_EVAL_CASES,
    COMMON_USE_CASE_MICRO_SCENARIO_SLUGS,
    IGNORED_FIRST_ACTION_TOOL_NAMES,
    PLANNING_MICRO_SCENARIO_SLUGS,
    TOOL_CHOICE_MICRO_SCENARIO_SLUGS,
    UPDATE_PLAN_POLICIES,
    UPDATE_PLAN_POLICY_EXPECT,
    UPDATE_PLAN_POLICY_FORBID,
    all_requests_have_options,
    get_forbidden_calls_before_end_planning,
    get_common_use_case_tool_calls_for_run,
    get_first_common_use_case_tool_call,
    get_first_relevant_tool_call,
    get_plan_activity_calls_for_run,
    get_pending_human_input_requests,
    get_planning_mutation_calls_before_end_planning,
    tool_call_is_plan_activity,
)
from api.evals.stop_policy import should_stop_for_eval_policy
from api.evals.suites import SuiteRegistry
from api.models import (
    BrowserUseAgent,
    CommsChannel,
    EvalRun,
    PersistentAgent,
    PersistentAgentConversation,
    PersistentAgentEnabledTool,
    PersistentAgentHumanInputRequest,
    PersistentAgentStep,
    PersistentAgentToolCall,
)


@tag("batch_eval_fingerprint")
class BehaviorMicroScenarioRegistrationTests(TestCase):
    def test_all_behavior_micro_scenarios_are_registered(self):
        registered = ScenarioRegistry.list_all()

        for slug in BEHAVIOR_MICRO_SCENARIO_SLUGS:
            self.assertIn(slug, registered)

    def test_behavior_micro_suites_include_expected_scenarios(self):
        agent_behavior_suite = SuiteRegistry.get("agent_behavior_micro")
        planning_suite = SuiteRegistry.get("planning_micro")
        tool_choice_suite = SuiteRegistry.get("tool_choice_micro")

        self.assertEqual(agent_behavior_suite.scenario_slugs, BEHAVIOR_MICRO_SCENARIO_SLUGS)
        self.assertEqual(planning_suite.scenario_slugs, PLANNING_MICRO_SCENARIO_SLUGS)
        self.assertEqual(tool_choice_suite.scenario_slugs, TOOL_CHOICE_MICRO_SCENARIO_SLUGS)

    def test_common_use_case_micro_evals_are_complete_and_registered(self):
        registered = ScenarioRegistry.list_all()

        self.assertEqual(len(COMMON_USE_CASE_EVAL_CASES), 100)
        self.assertEqual(len(COMMON_USE_CASE_MICRO_SCENARIO_SLUGS), 100)
        self.assertEqual(len(set(COMMON_USE_CASE_MICRO_SCENARIO_SLUGS)), 100)
        self.assertTrue(set(COMMON_USE_CASE_MICRO_SCENARIO_SLUGS).issubset(TOOL_CHOICE_MICRO_SCENARIO_SLUGS))
        self.assertTrue(set(COMMON_USE_CASE_MICRO_SCENARIO_SLUGS).issubset(BEHAVIOR_MICRO_SCENARIO_SLUGS))

        for case in COMMON_USE_CASE_EVAL_CASES:
            self.assertIsInstance(case, CommonUseCaseEvalDefinition)
            self.assertIn(case.slug, registered)
            self.assertLessEqual(len(case.prompt), 180)
            self.assertGreaterEqual(len(case.expected_tools), 1)
            self.assertNotIn("update_plan", case.expected_tools)
            self.assertNotIn("update_plan", case.forbidden_tools)
            self.assertIsInstance(case.plan_expected, bool)
            self.assertIn(case.update_plan_policy, UPDATE_PLAN_POLICIES)
            self.assertIsInstance(case.allowed_preamble_tools, tuple)
            self.assertIsInstance(case.ignored_tools, tuple)
            self.assertIsInstance(case.accepted_tool_alternatives, dict)
            self.assertIsInstance(case.eval_synthetic_tools, tuple)
            self.assertIsInstance(case.stop_after_success, bool)
            self.assertEqual(
                case.update_plan_policy,
                UPDATE_PLAN_POLICY_EXPECT if case.plan_expected else UPDATE_PLAN_POLICY_FORBID,
            )
            self.assertEqual(
                [task.name for task in registered[case.slug].tasks],
                [
                    "inject_prompt",
                    "verify_plan_policy",
                    "verify_expected_tool_usage",
                    "verify_forbidden_tool_absence",
                ],
            )

        by_slug = {case.slug: case for case in COMMON_USE_CASE_EVAL_CASES}
        self.assertFalse(by_slug["common_use_case_001_fetch_inventory_json"].plan_expected)
        self.assertFalse(by_slug["common_use_case_061_send_summary_email"].plan_expected)
        self.assertEqual(
            by_slug["common_use_case_061_send_summary_email"].accepted_tool_alternatives,
            {"send_email": ("request_contact_permission",)},
        )
        self.assertEqual(by_slug["common_use_case_069_secure_api_key_request"].forbidden_tools, ())
        self.assertEqual(by_slug["common_use_case_036_apollo_contacts"].allowed_preamble_tools, ("search_tools",))
        self.assertEqual(by_slug["common_use_case_037_apollo_accounts"].allowed_preamble_tools, ("search_tools",))
        self.assertEqual(by_slug["common_use_case_038_apollo_enrich_person"].allowed_preamble_tools, ("search_tools",))
        self.assertEqual(
            by_slug["common_use_case_036_apollo_contacts"].eval_synthetic_tools,
            ("apollo_io-search-contacts",),
        )
        self.assertEqual(
            by_slug["common_use_case_037_apollo_accounts"].eval_synthetic_tools,
            ("apollo_io-search-accounts",),
        )
        self.assertEqual(
            by_slug["common_use_case_038_apollo_enrich_person"].eval_synthetic_tools,
            ("apollo_io-people-enrichment",),
        )
        self.assertIn("sheet-123", by_slug["common_use_case_051_sheets_update_row"].prompt)
        self.assertEqual(by_slug["common_use_case_077_create_bar_chart"].allowed_preamble_tools, ("sqlite_batch",))
        self.assertIn("Jan 120", by_slug["common_use_case_079_create_report_with_chart"].prompt)
        self.assertIn("already has accounts and contacts", by_slug["common_use_case_085_sqlite_join_tables"].prompt)
        self.assertEqual(
            by_slug["common_use_case_089_enable_database"].accepted_tool_alternatives,
            {"enable_database": ("sqlite_batch",)},
        )
        self.assertTrue(by_slug["common_use_case_031_linkedin_person_profile"].plan_expected)
        self.assertTrue(by_slug["common_use_case_091_schedule_daily_digest"].plan_expected)
        self.assertEqual(
            by_slug["common_use_case_091_schedule_daily_digest"].accepted_tool_alternatives,
            {"update_schedule": ("sqlite_batch",)},
        )


@tag("batch_eval_fingerprint")
class BehaviorMicroHelperTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="behavior-micro@example.com",
            email="behavior-micro@example.com",
            password="testpass",
        )
        self.browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="Behavior Micro Browser Agent",
        )
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            browser_use_agent=self.browser_agent,
            name="Behavior Micro Agent",
            charter="Test helper behavior.",
        )
        self.run = EvalRun.objects.create(
            scenario_slug="helper_test",
            agent=self.agent,
            initiated_by=self.user,
            status=EvalRun.Status.RUNNING,
        )

    def _tool_definition_names(self, agent):
        with patch("api.agent.tools.tool_manager.sandbox_compute_enabled_for_agent", return_value=False):
            return {
                definition["function"]["name"]
                for definition in get_enabled_tool_definitions(agent)
            }

    def _add_tool_call(self, tool_name, params=None):
        step = PersistentAgentStep.objects.create(
            agent=self.agent,
            eval_run=self.run,
            description=f"{tool_name} call",
        )
        return PersistentAgentToolCall.objects.create(
            step=step,
            tool_name=tool_name,
            tool_params=params or {},
            result="{}",
            status="complete",
        )

    def _add_human_input_request(self, run, question):
        conversation = PersistentAgentConversation.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.WEB,
            address=f"web://user/{run.id}",
        )
        step = PersistentAgentStep.objects.create(
            agent=self.agent,
            eval_run=run,
            description=f"{question} step",
        )
        return PersistentAgentHumanInputRequest.objects.create(
            agent=self.agent,
            conversation=conversation,
            originating_step=step,
            question=question,
            options_json=[{"key": "yes", "title": "Yes"}],
            requested_via_channel=CommsChannel.WEB,
        )

    def test_eval_synthetic_tools_are_catalog_backed_for_eval_agents(self):
        self.agent.execution_environment = "eval"
        self.agent.save(update_fields=["execution_environment"])
        scenario = ScenarioRegistry.get("common_use_case_037_apollo_accounts")

        scenario._enable_builtin_tools(self.agent.id, ["apollo_io-search-accounts"])
        row = PersistentAgentEnabledTool.objects.get(
            agent=self.agent,
            tool_full_name="apollo_io-search-accounts",
        )
        self.assertEqual(row.tool_server, "")
        self.assertNotIn("apollo_io-search-accounts", self._tool_definition_names(self.agent))

        scenario._enable_eval_synthetic_tools(self.agent.id, ["apollo_io-search-accounts"])
        row.refresh_from_db()

        self.assertEqual(row.tool_server, EVAL_SYNTHETIC_TOOL_SERVER)
        self.assertEqual(row.tool_name, "apollo_io-search-accounts")
        self.assertIn("apollo_io-search-accounts", self._tool_definition_names(self.agent))

    @patch("api.agent.tools.search_tools._has_active_pipedream_runtime", return_value=False)
    @patch("api.agent.tools.search_tools.enable_tools")
    @patch("api.agent.tools.search_tools.run_completion")
    @patch("api.agent.tools.search_tools.get_mcp_manager")
    @patch("api.agent.tools.search_tools.get_llm_config_with_failover")
    @patch("api.agent.tools.tool_manager.sandbox_compute_enabled_for_agent", return_value=False)
    def test_search_tools_catalog_includes_eval_synthetic_tools(
        self,
        _mock_sandbox_compute_enabled,
        mock_get_config,
        mock_get_manager,
        mock_run_completion,
        mock_enable_tools,
        _mock_has_active_pipedream_runtime,
    ):
        self.agent.execution_environment = "eval"
        self.agent.save(update_fields=["execution_environment"])
        mock_manager = MagicMock()
        mock_manager._initialized = True
        mock_manager.get_tools_for_agent.return_value = []
        mock_get_manager.return_value = mock_manager
        mock_get_config.return_value = [("openai", "gpt-4o-mini", {})]

        mock_response = MagicMock()
        message = MagicMock()
        message.content = "No relevant tools."
        message.tool_calls = []
        choice = MagicMock()
        choice.message = message
        mock_response.choices = [choice]
        mock_run_completion.return_value = mock_response

        result = search_tools(self.agent, "Apollo company search")

        self.assertEqual(result["status"], "success")
        user_message = mock_run_completion.call_args.kwargs["messages"][1]["content"]
        self.assertIn("apollo_io-search-accounts", user_message)
        mock_enable_tools.assert_not_called()

    def test_first_relevant_tool_call_skips_ignored_tools(self):
        self._add_tool_call("send_chat_message")
        expected = self._add_tool_call("request_human_input")

        first = get_first_relevant_tool_call(
            self.run.id,
            ignored_tool_names={"send_chat_message"},
        )

        self.assertEqual(first, expected)

    def test_base_first_action_ignore_set_does_not_hide_update_plan(self):
        expected = self._add_tool_call("update_plan")
        self._add_tool_call("http_request")

        first = get_first_relevant_tool_call(
            self.run.id,
            ignored_tool_names=IGNORED_FIRST_ACTION_TOOL_NAMES,
        )

        self.assertEqual(first, expected)

    def test_common_eval_definition_applies_plan_expected_to_update_plan_policy(self):
        simple = CommonUseCaseEvalDefinition.from_mapping(
            {
                "slug": "simple_no_plan",
                "category": "tool_choice",
                "prompt": "Fetch a JSON URL.",
                "expected_tools": ["http_request"],
                "plan_expected": False,
            }
        )
        planned = CommonUseCaseEvalDefinition.from_mapping(
            {
                "slug": "complex_plan",
                "category": "planning",
                "prompt": "Create a plan.",
                "expected_tools": ["request_human_input"],
                "plan_expected": True,
            }
        )

        self.assertNotIn("update_plan", simple.expected_tool_names())
        self.assertNotIn("update_plan", simple.forbidden_tool_names())
        self.assertNotIn("update_plan", planned.expected_tool_names())
        self.assertNotIn("update_plan", planned.forbidden_tool_names())

    def test_common_eval_definition_requires_plan_expected_for_update_plan(self):
        with self.assertRaises(ValueError):
            CommonUseCaseEvalDefinition.from_mapping(
                {
                    "slug": "bad_expected_update_plan",
                    "category": "planning",
                    "prompt": "Create a plan.",
                    "expected_tools": ["update_plan"],
                    "plan_expected": True,
                }
            )

    def test_common_eval_definition_rejects_expected_params_for_multi_tool_cases(self):
        with self.assertRaisesMessage(
            ValueError,
            "expected_params is only supported for single-tool evals",
        ):
            CommonUseCaseEvalDefinition.from_mapping(
                {
                    "slug": "bad_multi_tool_params",
                    "category": "tool_choice",
                    "prompt": "Fetch data and export it.",
                    "expected_tools": ["http_request", "create_csv"],
                    "expected_params": {"url": "https://example.test/data.json"},
                    "plan_expected": True,
                }
            )

    def test_plan_activity_only_includes_update_plan(self):
        read = self._add_tool_call("sqlite_batch", {"sql": "SELECT * FROM __agent_config"})
        sqlite_mutation = self._add_tool_call(
            "sqlite_batch",
            {"sql": "UPDATE __agent_config SET charter = 'Monitor competitors'"},
        )
        update_plan = self._add_tool_call("update_plan", {"plan": [{"step": "Research", "status": "todo"}]})

        self.assertFalse(tool_call_is_plan_activity(read))
        self.assertFalse(tool_call_is_plan_activity(sqlite_mutation))
        self.assertTrue(tool_call_is_plan_activity(update_plan))
        self.assertEqual(get_plan_activity_calls_for_run(self.run.id), [update_plan])

    def test_common_use_case_tool_calls_ignore_sqlite_config_mutations(self):
        config_update = self._add_tool_call(
            "sqlite_batch",
            {"sql": "UPDATE __agent_config SET charter = 'Monitor competitors'"},
        )
        expected = self._add_tool_call("sqlite_batch", {"sql": "SELECT * FROM leads"})
        unrelated = self._add_tool_call("http_request")

        self.assertEqual(
            get_common_use_case_tool_calls_for_run(self.run.id, tool_names=["sqlite_batch"]),
            [expected],
        )
        self.assertEqual(
            get_common_use_case_tool_calls_for_run(self.run.id),
            [expected, unrelated],
        )
        self.assertNotIn(config_update, get_common_use_case_tool_calls_for_run(self.run.id))

    def test_common_use_case_tool_calls_keep_mixed_sqlite_config_and_domain_work(self):
        mixed_batch = self._add_tool_call(
            "sqlite_batch",
            {
                "sql": (
                    "UPDATE __agent_config SET charter = 'Manage leads'; "
                    "CREATE TABLE leads (company TEXT, email TEXT)"
                )
            },
        )

        self.assertEqual(
            get_common_use_case_tool_calls_for_run(self.run.id, tool_names=["sqlite_batch"]),
            [mixed_batch],
        )

    def test_first_common_use_case_tool_call_ignores_chat_and_config_mutation(self):
        self._add_tool_call("send_chat_message")
        self._add_tool_call(
            "sqlite_batch",
            {"sql": "UPDATE __agent_config SET charter = 'Fetch inventory'"},
        )
        expected = self._add_tool_call("http_request")

        first = get_first_common_use_case_tool_call(
            self.run.id,
            ignored_tool_names=IGNORED_FIRST_ACTION_TOOL_NAMES,
        )

        self.assertEqual(first, expected)

    def test_eval_stop_policy_stops_when_expected_tool_seen(self):
        should_stop, _reason = should_stop_for_eval_policy(
            str(self.run.id),
            {"stop_when_all_seen": [{"tool_name": "http_request", "params": {"url": "https://example.test"}}]},
        )
        self.assertFalse(should_stop)

        self._add_tool_call("http_request", {"url": "https://example.test"})

        should_stop, reason = should_stop_for_eval_policy(
            str(self.run.id),
            {"stop_when_all_seen": [{"tool_name": "http_request", "params": {"url": "https://example.test"}}]},
        )

        self.assertTrue(should_stop)
        self.assertIn("all terminal expected", reason)

    def test_eval_stop_policy_stops_on_unexpected_relevant_tool(self):
        self._add_tool_call("send_chat_message")
        self._add_tool_call(
            "sqlite_batch",
            {"sql": "UPDATE __agent_config SET charter = 'Fetch inventory'"},
        )
        self._add_tool_call("mcp_brightdata_search_engine")

        should_stop, reason = should_stop_for_eval_policy(
            str(self.run.id),
            {
                "ignored_tool_names": list(IGNORED_FIRST_ACTION_TOOL_NAMES),
                "allowed_tool_names": ["http_request"],
                "stop_on_unexpected_relevant_tool": True,
            },
        )

        self.assertTrue(should_stop)
        self.assertIn("unexpected relevant tool", reason)

    def test_eval_stop_policy_ignores_config_mutation_for_common_tool_calls(self):
        self._add_tool_call(
            "sqlite_batch",
            {"sql": "UPDATE __agent_config SET charter = 'Fetch inventory'"},
        )
        should_stop, _reason = should_stop_for_eval_policy(
            str(self.run.id),
            {"stop_when_all_seen": [{"tool_name": "sqlite_batch"}]},
        )
        self.assertFalse(should_stop)

        self._add_tool_call("sqlite_batch", {"sql": "SELECT * FROM leads"})
        should_stop, _reason = should_stop_for_eval_policy(
            str(self.run.id),
            {"stop_when_all_seen": [{"tool_name": "sqlite_batch"}]},
        )
        self.assertTrue(should_stop)

    def test_eval_stop_policy_counts_mixed_config_and_domain_sqlite_batch(self):
        self._add_tool_call(
            "sqlite_batch",
            {
                "sql": (
                    "UPDATE __agent_config SET charter = 'Fetch inventory'; "
                    "CREATE TABLE leads (company TEXT)"
                )
            },
        )

        should_stop, _reason = should_stop_for_eval_policy(
            str(self.run.id),
            {"stop_when_all_seen": [{"tool_name": "sqlite_batch"}]},
        )

        self.assertTrue(should_stop)

    def test_eval_stop_policy_accepts_sqlite_schedule_alternative(self):
        self._add_tool_call(
            "sqlite_batch",
            {"sql": "UPDATE __agent_config SET schedule = '0 9 * * *' WHERE id = 1"},
        )

        should_stop, _reason = should_stop_for_eval_policy(
            str(self.run.id),
            {
                "ignore_sqlite_agent_config_mutations": False,
                "stop_when_all_seen": [
                    {
                        "tool_name": "update_schedule",
                        "alternatives": ["sqlite_batch"],
                        "agent_config_field": "schedule",
                    }
                ],
            },
        )

        self.assertTrue(should_stop)

    def test_eval_stop_policy_can_stop_on_sqlite_config_mutation(self):
        self._add_tool_call(
            "sqlite_batch",
            {"sql": "UPDATE __agent_config SET charter = 'Monitor competitors'"},
        )

        should_stop, reason = should_stop_for_eval_policy(
            str(self.run.id),
            {"stop_on_sqlite_agent_config_mutation": True},
        )

        self.assertTrue(should_stop)
        self.assertIn("config mutation", reason)

    def test_eval_stop_policy_stops_on_human_input_request(self):
        should_stop, _reason = should_stop_for_eval_policy(
            str(self.run.id),
            {"stop_on_human_input_request": True},
        )
        self.assertFalse(should_stop)

        self._add_human_input_request(self.run, "Which client?")
        should_stop, reason = should_stop_for_eval_policy(
            str(self.run.id),
            {"stop_on_human_input_request": True},
        )

        self.assertTrue(should_stop)
        self.assertIn("human input", reason)

    def test_common_eval_definition_requires_explicit_plan_expected(self):
        with self.assertRaises(ValueError):
            CommonUseCaseEvalDefinition.from_mapping(
                {
                    "slug": "missing_plan_expected",
                    "category": "tool_choice",
                    "prompt": "Fetch a JSON URL.",
                    "expected_tools": ["http_request"],
                }
            )

    def test_forbidden_calls_before_end_planning_stops_at_end_planning(self):
        before = self._add_tool_call("http_request")
        self._add_tool_call("end_planning")
        self._add_tool_call("send_email")

        calls = get_forbidden_calls_before_end_planning(
            self.run.id,
            forbidden_tool_names={"http_request", "send_email"},
        )

        self.assertEqual(calls, [before])

    def test_planning_mutation_detection_ignores_reads_and_after_end_planning(self):
        self._add_tool_call("sqlite_batch", {"sql": "SELECT * FROM __agent_config"})
        mutation = self._add_tool_call("sqlite_batch", {"sql": "UPDATE __agent_config SET schedule='0 9 * * *'"})
        plan_mutation = self._add_tool_call("update_plan", {"plan": [{"step": "x", "status": "todo"}]})
        self._add_tool_call("end_planning")
        self._add_tool_call("update_plan", {"plan": [{"step": "after", "status": "todo"}]})

        calls = get_planning_mutation_calls_before_end_planning(self.run.id)

        self.assertEqual(calls, [mutation, plan_mutation])

    def test_pending_human_input_requests_are_scoped_to_eval_run(self):
        expected = self._add_human_input_request(self.run, "Current run question?")
        other_run = EvalRun.objects.create(
            scenario_slug="helper_test_other",
            agent=self.agent,
            initiated_by=self.user,
            status=EvalRun.Status.RUNNING,
        )
        self._add_human_input_request(other_run, "Other run question?")

        requests = get_pending_human_input_requests(self.agent.id, self.run.id)

        self.assertEqual(requests, [expected])

    def test_all_requests_have_options_requires_nonempty_options(self):
        with_options = SimpleNamespace(options_json=[{"key": "yes", "title": "Yes"}])
        without_options = SimpleNamespace(options_json=[])

        self.assertTrue(all_requests_have_options([with_options]))
        self.assertFalse(all_requests_have_options([with_options, without_options]))
