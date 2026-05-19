from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

import api.evals.loader  # noqa: F401 - registers scenarios and suites
from api.agent.core import event_processing as ep
from api.agent.tools.eval_synthetic_tools import EVAL_SYNTHETIC_TOOL_DEFINITIONS, EVAL_SYNTHETIC_TOOL_SERVER
from api.agent.tools.search_tools import search_tools
from api.agent.tools.tool_manager import execute_enabled_tool, get_enabled_tool_definitions
from api.evals.registry import ScenarioRegistry
from api.evals.scenarios.bitcoin_price_multiturn import is_supported_bitcoin_price_api_url
from api.evals.scenarios.behavior_micro import (
    BEHAVIOR_MICRO_SCENARIO_SLUGS,
    BehaviorMicroScenario,
    CommonUseCaseEvalDefinition,
    CommonUseCaseToolChoiceScenario,
    COMMON_USE_CASE_EVAL_CASES,
    COMMON_USE_CASE_MICRO_SCENARIO_SLUGS,
    GOOGLE_SHEETS_EVAL_SYNTHETIC_TOOL_NAMES,
    IGNORED_FIRST_ACTION_TOOL_NAMES,
    PLANNING_MICRO_SCENARIO_SLUGS,
    PLANNING_DISMISS_AFTER_GREETING_DOES_NOT_RESUME,
    TOOL_CHOICE_MICRO_SCENARIO_SLUGS,
    UPDATE_PLAN_POLICIES,
    UPDATE_PLAN_POLICY_EXPECT,
    UPDATE_PLAN_POLICY_OPTIONAL,
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
from api.evals.scenarios.monitor_pollution import (
    _charter_mentions_pollution_monitoring,
    _schedule_is_reasonable_pollution_monitoring,
)
from api.evals.scenarios.permit_followup_single_reply import PermitFollowupSingleReplyScenario
from api.evals.scenarios.weather_lookup import _is_free_weather_request
from api.evals.stop_policy import (
    should_stop_for_eval_policy,
    sqlite_batch_is_only_eval_bookkeeping_read,
    sqlite_batch_is_only_planning_state_read,
)
from api.evals.suites import SuiteRegistry
from api.models import (
    BrowserUseAgent,
    CommsChannel,
    EvalRun,
    EvalRunTask,
    PersistentAgent,
    PersistentAgentConversation,
    PersistentAgentEnabledTool,
    PersistentAgentHumanInputRequest,
    PersistentAgentMessage,
    PersistentAgentStep,
    PersistentAgentSystemStep,
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
                UPDATE_PLAN_POLICY_EXPECT if case.plan_expected else UPDATE_PLAN_POLICY_OPTIONAL,
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

        brightdata_tools = {
            tool_name
            for case in COMMON_USE_CASE_EVAL_CASES
            for tool_name in [*case.expected_tools, *case.forbidden_tools]
            if tool_name.startswith("mcp_brightdata_")
        }
        self.assertTrue(brightdata_tools.issubset(EVAL_SYNTHETIC_TOOL_DEFINITIONS))
        google_sheets_tools = {
            tool_name
            for case in COMMON_USE_CASE_EVAL_CASES
            for tool_name in [*case.expected_tools, *case.forbidden_tools]
            if tool_name.startswith("google_sheets-")
        }
        self.assertTrue(google_sheets_tools.issubset(EVAL_SYNTHETIC_TOOL_DEFINITIONS))

        by_slug = {case.slug: case for case in COMMON_USE_CASE_EVAL_CASES}
        self.assertFalse(by_slug["common_use_case_001_fetch_inventory_json"].plan_expected)
        self.assertFalse(by_slug["common_use_case_061_send_summary_email"].plan_expected)
        self.assertFalse(by_slug["common_use_case_020_search_reddit_mentions"].plan_expected)
        self.assertFalse(by_slug["common_use_case_020_search_reddit_mentions"].stop_after_success)
        self.assertIn("BiomeBoost Pro", by_slug["common_use_case_020_search_reddit_mentions"].prompt)
        self.assertIn("API latency stayed under 120 ms", by_slug["common_use_case_064_send_digest_email"].prompt)
        self.assertEqual(
            by_slug["common_use_case_061_send_summary_email"].accepted_tool_alternatives,
            {"send_email": ("request_contact_permission",)},
        )
        self.assertIn("Enterprise leads increased", by_slug["common_use_case_061_send_summary_email"].prompt)
        self.assertIn("sqlite_batch", by_slug["common_use_case_062_send_attachment_email"].allowed_preamble_tools)
        self.assertIn("Action items", by_slug["common_use_case_075_create_markdown_file"].prompt)
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
        self.assertIn("Jordan Lee at Acme AI", by_slug["common_use_case_031_linkedin_person_profile"].prompt)
        self.assertIn("Acme AI", by_slug["common_use_case_032_linkedin_company_profile"].prompt)
        self.assertIn("Search LinkedIn", by_slug["common_use_case_034_linkedin_people_search"].prompt)
        self.assertIn("beta launched", by_slug["common_use_case_073_create_status_pdf"].prompt)
        self.assertIn("site plan", by_slug["common_use_case_074_create_permit_pdf"].prompt)
        self.assertIn("Run a SQLite query", by_slug["common_use_case_086_sqlite_export_query_csv"].prompt)
        self.assertIn("https://status.example.test/support", by_slug["common_use_case_092_schedule_hourly_monitor"].prompt)
        self.assertIn("BTC-USD", by_slug["common_use_case_096_schedule_price_alert"].prompt)
        self.assertIn("https://borough.example.test/permits/decks", by_slug["common_use_case_097_schedule_permit_check"].prompt)
        self.assertEqual(
            by_slug["common_use_case_089_enable_database"].accepted_tool_alternatives,
            {"enable_database": ("sqlite_batch",)},
        )
        self.assertFalse(by_slug["common_use_case_031_linkedin_person_profile"].plan_expected)
        self.assertEqual(
            by_slug["common_use_case_031_linkedin_person_profile"].allowed_preamble_tools,
            (
                "search_tools",
                "mcp_brightdata_search_engine",
                "mcp_brightdata_web_data_linkedin_company_profile",
            ),
        )
        self.assertFalse(by_slug["common_use_case_091_schedule_daily_digest"].plan_expected)
        self.assertIn("ET schedule", by_slug["common_use_case_093_schedule_weekly_report"].prompt)
        self.assertEqual(
            by_slug["common_use_case_020_search_reddit_mentions"].accepted_tool_alternatives,
            {"mcp_brightdata_web_data_reddit_posts": ("mcp_brightdata_search_engine",)},
        )
        self.assertEqual(
            by_slug["common_use_case_020_search_reddit_mentions"].expected_tools,
            ("mcp_brightdata_web_data_reddit_posts",),
        )
        self.assertEqual(
            by_slug["common_use_case_091_schedule_daily_digest"].expected_tools,
            ("sqlite_batch",),
        )
        self.assertEqual(by_slug["common_use_case_094_update_agent_charter"].expected_tools, ("sqlite_batch",))
        self.assertIn(
            "google_sheets-get-spreadsheet-by-id",
            by_slug["common_use_case_048_sheets_add_single_row"].allowed_preamble_tool_names(),
        )
        self.assertEqual(
            by_slug["common_use_case_046_sheets_read_range"].accepted_tool_alternatives,
            {"google_sheets-get-values-in-range": ("google_sheets-read-rows",)},
        )
        self.assertEqual(
            by_slug["common_use_case_060_sheets_append_rows"].accepted_tool_alternatives,
            {"google_sheets-add-rows": ("google_sheets-add-multiple-rows",)},
        )
        sheets_mock = CommonUseCaseToolChoiceScenario._google_sheets_mock_success(
            "google_sheets-get-spreadsheet-by-id"
        )
        self.assertIn("use the requested Google Sheets tool next", sheets_mock["message"])
        self.assertNotIn("mutation tool", sheets_mock["message"])
        self.assertIn("Tasks", sheets_mock["content"]["worksheets"])
        self.assertIn(
            "mcp_brightdata_search_engine",
            by_slug["common_use_case_096_schedule_price_alert"].allowed_preamble_tool_names(),
        )
        self.assertFalse(
            ScenarioRegistry.get("common_use_case_090_sqlite_summarize_messages")._build_eval_stop_policy()[
                "ignore_sqlite_eval_bookkeeping_reads"
            ]
        )

    def test_eval_synthetic_tools_execute_without_external_integration_handlers(self):
        user = get_user_model().objects.create_user(username="eval-synth")
        browser_agent = BrowserUseAgent.objects.create(user=user, name="Eval browser")
        agent = PersistentAgent.objects.create(
            user=user,
            browser_use_agent=browser_agent,
            name="Eval agent",
            execution_environment="eval",
        )
        PersistentAgentEnabledTool.objects.create(
            agent=agent,
            tool_full_name="mcp_brightdata_search_engine",
            tool_server=EVAL_SYNTHETIC_TOOL_SERVER,
            tool_name="mcp_brightdata_search_engine",
        )

        result = execute_enabled_tool(agent, "mcp_brightdata_search_engine", {"query": "pricing"})

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["tool"], "mcp_brightdata_search_engine")

    def test_weather_request_validation_is_deterministic(self):
        valid, reason = _is_free_weather_request({"url": "https://wttr.in/Frederick,MD?format=j1"})
        self.assertTrue(valid, reason)

        valid, reason = _is_free_weather_request(
            {"url": "https://api.open-meteo.com/v1/forecast?latitude=39.4143&longitude=-77.4105"}
        )
        self.assertTrue(valid, reason)

        invalid, reason = _is_free_weather_request(
            {"url": "https://api.open-meteo.com/v1/forecast?latitude=38.9072&longitude=-77.0369"}
        )
        self.assertFalse(invalid)
        self.assertIn("does not target Frederick", reason)

        invalid, reason = _is_free_weather_request(
            {"url": "https://geocoding-api.open-meteo.com/v1/search?name=Frederick,MD"}
        )
        self.assertFalse(invalid)
        self.assertIn("geocoding only resolves coordinates", reason)

        invalid, reason = _is_free_weather_request({"url": "https://example.test/weather"})
        self.assertFalse(invalid)
        self.assertIn("supported free weather API", reason)

    def test_sqlite_agent_config_reads_are_common_use_case_bookkeeping(self):
        call = SimpleNamespace(
            tool_name="sqlite_batch",
            tool_params={"sql": "SELECT * FROM __agent_config WHERE id = 1;"},
        )

        self.assertTrue(sqlite_batch_is_only_planning_state_read(call))

        result_table_call = SimpleNamespace(
            tool_name="sqlite_batch",
            tool_params={"sql": "SELECT result_id, tool_name FROM __tool_results ORDER BY created_at DESC;"},
        )
        self.assertTrue(sqlite_batch_is_only_eval_bookkeeping_read(result_table_call))

        messages_table_call = SimpleNamespace(
            tool_name="sqlite_batch",
            tool_params={"sql": "SELECT * FROM __messages ORDER BY timestamp DESC LIMIT 5;"},
        )
        self.assertTrue(sqlite_batch_is_only_eval_bookkeeping_read(messages_table_call))

    def test_bitcoin_price_api_validation_accepts_supported_direct_price_apis(self):
        self.assertTrue(
            is_supported_bitcoin_price_api_url(
                "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd"
            )
        )
        self.assertTrue(is_supported_bitcoin_price_api_url("https://api.coindesk.com/v1/bpi/currentprice.json"))
        self.assertTrue(is_supported_bitcoin_price_api_url("https://api.coindesk.com/v1/bpi/currentprice/USD.json"))
        self.assertFalse(is_supported_bitcoin_price_api_url("https://api.coindesk.com/v1/bpi/currentprice/BTC.json"))
        self.assertFalse(is_supported_bitcoin_price_api_url("https://example.test/bitcoin"))

    def test_monitor_pollution_checks_are_deterministic(self):
        charter_ok, charter_reason = _charter_mentions_pollution_monitoring(
            "Monitor the pollution index in Washington DC every day."
        )
        self.assertTrue(charter_ok, charter_reason)

        missing_charter_ok, missing_charter_reason = _charter_mentions_pollution_monitoring(
            "Check the weather every day."
        )
        self.assertFalse(missing_charter_ok, missing_charter_reason)

        schedule_ok, schedule_reason = _schedule_is_reasonable_pollution_monitoring("0 9 * * *")
        self.assertTrue(schedule_ok, schedule_reason)

        too_frequent_ok, too_frequent_reason = _schedule_is_reasonable_pollution_monitoring("* * * * *")
        self.assertFalse(too_frequent_ok, too_frequent_reason)


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

    def test_common_use_case_stop_policy_allows_tool_discovery_for_eval_synthetic_tools(self):
        scenario = ScenarioRegistry.get("common_use_case_046_sheets_read_range")

        policy = scenario._build_eval_stop_policy()

        self.assertIn("search_tools", policy["allowed_tool_names"])

    def test_google_sheets_eval_synthetic_tools_are_defined(self):
        for tool_name in GOOGLE_SHEETS_EVAL_SYNTHETIC_TOOL_NAMES:
            self.assertIn(tool_name, EVAL_SYNTHETIC_TOOL_DEFINITIONS)
            self.assertIn("do not call search_tools first", EVAL_SYNTHETIC_TOOL_DEFINITIONS[tool_name]["description"])

    def test_revenue_chart_eval_sqlite_mock_returns_revenue_rows(self):
        scenario = ScenarioRegistry.get("common_use_case_079_create_report_with_chart")

        mock_config = scenario._build_mock_config()

        sqlite_mock = mock_config["sqlite_batch"]
        self.assertIn("revenue_data", sqlite_mock["content"]["tables"])
        self.assertEqual(sqlite_mock["content"]["columns"], ["month", "revenue"])
        self.assertEqual(sqlite_mock["content"]["rows"][0], {"month": "Jan", "revenue": 120})
        self.assertIn("call create_chart next", sqlite_mock["content"]["next_step"])

    def test_reddit_eval_fixture_has_terminal_structured_data(self):
        scenario = ScenarioRegistry.get("common_use_case_020_search_reddit_mentions")
        policy = scenario._build_eval_stop_policy()
        result = scenario._mock_success("mcp_brightdata_web_data_reddit_posts")

        self.assertNotIn("stop_when_all_seen", policy)
        self.assertIn("spawn_web_task", policy["stop_on_tool_names"])
        self.assertIn("BiomeBoost Pro", str(result["content"]))
        self.assertIn("sentiment", str(result["content"]).lower())

    def test_brightdata_eval_synthetic_descriptions_prefer_structured_data_over_browser(self):
        search_description = EVAL_SYNTHETIC_TOOL_DEFINITIONS["mcp_brightdata_search_engine"]["description"]
        reddit_description = EVAL_SYNTHETIC_TOOL_DEFINITIONS["mcp_brightdata_web_data_reddit_posts"]["description"]

        self.assertIn("ordinary research", search_description)
        self.assertIn("Reddit mentions", reddit_description)
        self.assertIn("browser automation", reddit_description)

    @patch("api.agent.tools.tool_manager._get_manager")
    @patch("api.agent.tools.tool_manager.sandbox_compute_enabled_for_agent", return_value=False)
    def test_eval_synthetic_tool_definition_overrides_same_named_mcp_tool(
        self,
        _mock_sandbox_compute_enabled,
        mock_get_manager,
    ):
        self.agent.execution_environment = "eval"
        self.agent.save(update_fields=["execution_environment"])
        tool_name = "google_sheets-get-values-in-range"
        PersistentAgentEnabledTool.objects.create(
            agent=self.agent,
            tool_full_name=tool_name,
            tool_server=EVAL_SYNTHETIC_TOOL_SERVER,
            tool_name=tool_name,
        )
        mock_manager = MagicMock()
        mock_manager.get_enabled_tools_definitions.return_value = [
            {
                "type": "function",
                "function": {
                    "name": tool_name,
                    "description": "Real integration schema requiring worksheet IDs.",
                    "parameters": {
                        "type": "object",
                        "properties": {"worksheetId": {"type": "integer"}},
                        "required": ["worksheetId"],
                    },
                },
            }
        ]
        mock_get_manager.return_value = mock_manager

        definitions = get_enabled_tool_definitions(self.agent)
        matching = [
            definition["function"]
            for definition in definitions
            if definition["function"]["name"] == tool_name
        ]

        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["description"], EVAL_SYNTHETIC_TOOL_DEFINITIONS[tool_name]["description"])
        self.assertEqual(matching[0]["parameters"].get("required", []), [])
        self.assertEqual(matching[0]["parameters"]["properties"]["worksheetId"]["type"], "string")

    def test_seed_completed_process_run_disables_first_run_once(self):
        scenario = ScenarioRegistry.get("common_use_case_046_sheets_read_range")

        scenario._seed_completed_process_run(self.agent.id)
        scenario._seed_completed_process_run(self.agent.id)

        process_steps = PersistentAgentSystemStep.objects.filter(
            step__agent=self.agent,
            code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
            step__description="Process events",
        )
        self.assertEqual(process_steps.count(), 1)

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
        self.assertEqual(simple.update_plan_policy, UPDATE_PLAN_POLICY_OPTIONAL)
        self.assertNotIn("update_plan", planned.expected_tool_names())
        self.assertNotIn("update_plan", planned.forbidden_tool_names())
        self.assertEqual(planned.update_plan_policy, UPDATE_PLAN_POLICY_EXPECT)

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

    @patch("api.evals.scenarios.permit_followup_single_reply.process_agent_events")
    def test_permit_followup_prompt_under_test_is_current_inbound(self, mock_process_agent_events):
        prompt_body = (
            "I filled in the Example Borough zoning permit. Where's your source that I need a building permit?"
        )

        def send_one_reply(agent_id, **_kwargs):
            agent = PersistentAgent.objects.get(id=agent_id)
            inbound = (
                PersistentAgentMessage.objects.filter(
                    owner_agent_id=agent_id,
                    is_outbound=False,
                    body=prompt_body,
                )
                .order_by("-timestamp")
                .get()
            )
            PersistentAgentMessage.objects.create(
                is_outbound=True,
                from_endpoint=agent.preferred_contact_endpoint,
                conversation=inbound.conversation,
                body=(
                    "The source is the Example Borough deck handout: decks over 30 inches above grade "
                    "need a UCC building permit."
                ),
                raw_payload={"source": "test_reply"},
                owner_agent_id=agent_id,
            )

        mock_process_agent_events.side_effect = send_one_reply
        for index, task in enumerate(PermitFollowupSingleReplyScenario.tasks, start=1):
            EvalRunTask.objects.create(
                run=self.run,
                sequence=index,
                name=task.name,
                assertion_type=task.assertion_type,
            )

        PermitFollowupSingleReplyScenario().run(str(self.run.id), str(self.agent.id))

        prompts = list(
            PersistentAgentMessage.objects.filter(
                owner_agent=self.agent,
                is_outbound=False,
                body=prompt_body,
            ).order_by("timestamp")
        )
        self.assertEqual(len(prompts), 1)
        self.assertEqual(prompts[0].raw_payload, {"source": "eval_prompt"})
        self.assertGreater(prompts[0].timestamp, timezone.now() - timedelta(minutes=1))
        self.assertTrue(
            PersistentAgentToolCall.objects.filter(
                step__agent=self.agent,
                tool_name="browser_task",
                result__icontains="deck-permit-handout.pdf",
            ).exists()
        )

        statuses = {
            task.name: task.status
            for task in EvalRunTask.objects.filter(run=self.run).order_by("sequence")
        }
        self.assertEqual(statuses["inject_prompt"], EvalRunTask.Status.PASSED)
        self.assertEqual(statuses["verify_single_reply"], EvalRunTask.Status.PASSED)

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

    def test_common_use_case_expected_params_mock_requires_exact_http_url(self):
        scenario = ScenarioRegistry.get("common_use_case_010_fetch_form_json")
        mock_config = scenario._build_mock_config()

        missing_url_result = ep._resolve_eval_mock_result(
            mock_config,
            "http_request",
            {"method": "GET", "will_continue_work": True},
        )
        wrong_url_result = ep._resolve_eval_mock_result(
            mock_config,
            "http_request",
            {"method": "GET", "url": "https://permits.example.test/forms/other.json"},
        )
        expected_url_result = ep._resolve_eval_mock_result(
            mock_config,
            "http_request",
            {"method": "GET", "url": "https://permits.example.test/forms/zoning.json"},
        )

        self.assertEqual(missing_url_result["status"], "error")
        self.assertIn("missing required eval parameter: url", missing_url_result["message"])
        self.assertEqual(wrong_url_result["status"], "error")
        self.assertEqual(expected_url_result["status"], "ok")
        self.assertEqual(expected_url_result["content"], {"ok": True})

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

    def test_common_use_case_tool_calls_can_count_sqlite_message_history_when_expected(self):
        message_history_read = self._add_tool_call(
            "sqlite_batch",
            {"sql": "SELECT * FROM __messages ORDER BY timestamp DESC LIMIT 5"},
        )

        self.assertEqual(get_common_use_case_tool_calls_for_run(self.run.id), [])
        self.assertEqual(
            get_common_use_case_tool_calls_for_run(
                self.run.id,
                include_sqlite_eval_bookkeeping_reads=True,
            ),
            [message_history_read],
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

    def test_record_forbidden_before_end_handles_tool_call_primary_key(self):
        EvalRunTask.objects.create(
            run=self.run,
            sequence=1,
            name="verify_no_work_before_end_planning",
            assertion_type="manual",
        )
        self._add_tool_call("http_request")

        passed = BehaviorMicroScenario()._record_forbidden_before_end(
            self.run.id,
            None,
            "verify_no_work_before_end_planning",
            {"http_request"},
        )

        self.assertFalse(passed)
        task = self.run.tasks.get(name="verify_no_work_before_end_planning")
        self.assertEqual(task.status, EvalRunTask.Status.FAILED)
        self.assertIn("http_request", task.observed_summary)

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

    def test_planning_dismiss_after_greeting_scenario_covers_no_resume(self):
        scenario = ScenarioRegistry.get(PLANNING_DISMISS_AFTER_GREETING_DOES_NOT_RESUME)
        self.run.scenario_slug = scenario.slug
        self.run.save(update_fields=["scenario_slug"])
        for sequence, task in enumerate(scenario.tasks, start=1):
            EvalRunTask.objects.create(
                run=self.run,
                sequence=sequence,
                name=task.name,
                assertion_type=task.assertion_type,
            )

        with (
            patch("api.agent.comms.human_input_requests._emit_pending_human_input_updates"),
            patch("api.agent.tasks.process_agent_events_task.delay") as mock_delay,
        ):
            scenario.run(self.run.id, self.agent.id)

        self.assertEqual(
            list(self.run.tasks.order_by("sequence").values_list("status", flat=True)),
            [
                EvalRunTask.Status.PASSED,
                EvalRunTask.Status.PASSED,
                EvalRunTask.Status.PASSED,
            ],
        )
        request_obj = PersistentAgentHumanInputRequest.objects.get(agent=self.agent)
        self.assertEqual(request_obj.status, PersistentAgentHumanInputRequest.Status.CANCELLED)
        self.assertIsNone(request_obj.raw_reply_message_id)
        self.assertFalse(PersistentAgentMessage.objects.filter(owner_agent=self.agent, is_outbound=False).exists())
        mock_delay.assert_not_called()

    def test_all_requests_have_options_requires_nonempty_options(self):
        with_options = SimpleNamespace(options_json=[{"key": "yes", "title": "Yes"}])
        without_options = SimpleNamespace(options_json=[])

        self.assertTrue(all_requests_have_options([with_options]))
        self.assertFalse(all_requests_have_options([with_options, without_options]))
