import json
import sqlite3
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

import api.evals.loader  # noqa: F401 - registers scenarios and suites
from api.agent.core import event_processing as ep
from api.agent.tools.eval_synthetic_tools import EVAL_SYNTHETIC_TOOL_DEFINITIONS, EVAL_SYNTHETIC_TOOL_SERVER
from api.agent.tools.mcp_manager import MCPToolInfo
from api.agent.tools.search_tools import search_tools
from api.agent.tools.sqlite_state import agent_sqlite_db
from api.agent.tools.static_tools import get_static_tool_definitions
from api.agent.tools.tool_manager import execute_enabled_tool, get_enabled_tool_definitions
from api.evals.registry import ScenarioRegistry
from api.evals.scenarios.bitcoin_price_multiturn import (
    bitcoin_response_has_unnecessary_followup_question,
    bitcoin_tool_calls_include_supported_finance_lookup,
    bitcoin_tool_calls_include_supported_price_api,
    is_greeting_response,
    is_supported_bitcoin_price_api_url,
)
from api.evals.scenarios.behavior_micro import (
    BEHAVIOR_MICRO_SCENARIO_SLUGS,
    BehaviorMicroScenario,
    CHARTER_ADDS_DURABLE_PREFERENCE_PRESERVING_EXISTING,
    CHARTER_ADDS_FEEDBACK_RULE_FROM_CORRECTION,
    CHARTER_ADDS_INFERRED_PREFERENCE_PRESERVING_EXISTING,
    CHARTER_ADDS_PLAIN_PREFERENCE_WITHOUT_SAVE_WORD,
    CHARTER_EXPANDS_SPARSE_CHARTER_WITH_DETAIL,
    CHARTER_IGNORES_ONE_OFF_PREFERENCE,
    CHARTER_MEMORY_MICRO_SCENARIO_SLUGS,
    CHARTER_NARROWS_SCOPE_PRESERVING_UNRELATED_GUIDANCE,
    CHARTER_PATCHES_SUBTLE_CORRECTION_PRESERVING_GUIDANCE,
    CHARTER_APPLIES_TWO_SEPARATE_CORRECTIONS,
    CHARTER_REJECTS_NONCONFIGURING_CONTACT_CORRECTION,
    CharterRejectsNonconfiguringContactCorrectionScenario,
    CommonUseCaseEvalDefinition,
    CommonUseCaseToolChoiceScenario,
    COMMON_USE_CASE_EVAL_CASES,
    COMMON_USE_CASE_MICRO_SCENARIO_SLUGS,
    GOOGLE_SHEETS_EVAL_SYNTHETIC_TOOL_NAMES,
    IGNORED_FIRST_ACTION_TOOL_NAMES,
    PLANNING_MICRO_SCENARIO_SLUGS,
    PLANNING_DISMISS_AFTER_GREETING_DOES_NOT_RESUME,
    PLANNING_NO_DIRECT_SCHEDULE_OR_CONFIG_UPDATES,
    PlanningOneOffResearchReportEndsPlanningFirstScenario,
    TOOL_CHOICE_MISSING_RECIPIENT_USES_HUMAN_INPUT,
    TOOL_CHOICE_MICRO_SCENARIO_SLUGS,
    ProactiveRoleInfersSensibleScheduleScenario,
    OneOffResearchPreservesNoScheduleScenario,
    UPDATE_PLAN_POLICIES,
    UPDATE_PLAN_POLICY_EXPECT,
    UPDATE_PLAN_POLICY_OPTIONAL,
    requests_have_valid_shapes,
    charter_is_compact_and_nonduplicative,
    charter_preserves_email_prohibition,
    get_agent_config_mutation_calls_for_run,
    get_forbidden_calls_before_end_planning,
    get_common_use_case_tool_calls_for_run,
    get_first_common_use_case_tool_call,
    get_first_relevant_tool_call,
    get_first_successful_tool_call,
    get_plan_activity_calls_for_run,
    get_pending_human_input_requests,
    get_planning_mutation_calls_before_end_planning,
    tool_call_is_plan_activity,
)
from api.evals.scenarios.effort_calibration import _hierarchical_report_shape
from api.evals.scenarios.monitor_pollution import (
    MonitorPollutionScenario,
    _charter_mentions_pollution_monitoring,
    _schedule_is_reasonable_pollution_monitoring,
)
from api.evals.scenarios.permit_followup_single_reply import PermitFollowupSingleReplyScenario
from api.evals.scenarios.weather_lookup import _is_free_weather_request, _weather_lookup_http_mock
from api.evals.stop_policy import (
    should_stop_for_eval_policy,
    sqlite_batch_mutates_agent_config_field,
    sqlite_batch_is_only_eval_bookkeeping_read,
    sqlite_batch_is_only_planning_state_read,
)
from api.evals.suites import SuiteRegistry
from api.models import (
    BrowserUseAgent,
    AgentFsNode,
    CommsAllowlistEntry,
    CommsChannel,
    EvalRun,
    EvalRunTask,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
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

        for slug in BEHAVIOR_MICRO_SCENARIO_SLUGS + CHARTER_MEMORY_MICRO_SCENARIO_SLUGS:
            self.assertIn(slug, registered)

    def test_behavior_micro_suites_include_expected_scenarios(self):
        agent_behavior_suite = SuiteRegistry.get("agent_behavior_micro")
        charter_memory_suite = SuiteRegistry.get("charter_memory_micro")
        planning_suite = SuiteRegistry.get("planning_micro")
        tool_choice_suite = SuiteRegistry.get("tool_choice_micro")

        self.assertEqual(agent_behavior_suite.scenario_slugs, BEHAVIOR_MICRO_SCENARIO_SLUGS)
        self.assertEqual(charter_memory_suite.scenario_slugs, CHARTER_MEMORY_MICRO_SCENARIO_SLUGS)
        self.assertEqual(planning_suite.scenario_slugs, PLANNING_MICRO_SCENARIO_SLUGS)
        self.assertEqual(tool_choice_suite.scenario_slugs, TOOL_CHOICE_MICRO_SCENARIO_SLUGS)
        self.assertFalse(set(CHARTER_MEMORY_MICRO_SCENARIO_SLUGS) & set(BEHAVIOR_MICRO_SCENARIO_SLUGS))
        self.assertFalse(set(CHARTER_MEMORY_MICRO_SCENARIO_SLUGS) & set(TOOL_CHOICE_MICRO_SCENARIO_SLUGS))
        self.assertIn(CHARTER_ADDS_DURABLE_PREFERENCE_PRESERVING_EXISTING, charter_memory_suite.scenario_slugs)
        self.assertIn(CHARTER_ADDS_INFERRED_PREFERENCE_PRESERVING_EXISTING, charter_memory_suite.scenario_slugs)
        self.assertIn(CHARTER_EXPANDS_SPARSE_CHARTER_WITH_DETAIL, charter_memory_suite.scenario_slugs)
        self.assertIn(CHARTER_NARROWS_SCOPE_PRESERVING_UNRELATED_GUIDANCE, charter_memory_suite.scenario_slugs)
        self.assertIn(CHARTER_IGNORES_ONE_OFF_PREFERENCE, charter_memory_suite.scenario_slugs)
        self.assertIn(CHARTER_ADDS_FEEDBACK_RULE_FROM_CORRECTION, charter_memory_suite.scenario_slugs)
        self.assertIn(CHARTER_ADDS_PLAIN_PREFERENCE_WITHOUT_SAVE_WORD, charter_memory_suite.scenario_slugs)
        self.assertIn(CHARTER_PATCHES_SUBTLE_CORRECTION_PRESERVING_GUIDANCE, charter_memory_suite.scenario_slugs)
        self.assertIn(CHARTER_APPLIES_TWO_SEPARATE_CORRECTIONS, charter_memory_suite.scenario_slugs)
        self.assertIn(CHARTER_REJECTS_NONCONFIGURING_CONTACT_CORRECTION, charter_memory_suite.scenario_slugs)

    def test_schedule_intent_pair_distinguishes_ongoing_role_from_one_off(self):
        proactive_prompt = ProactiveRoleInfersSensibleScheduleScenario.prompt.lower()
        one_off_prompt = OneOffResearchPreservesNoScheduleScenario.prompt.lower()

        self.assertIn("ongoing", proactive_prompt)
        self.assertIn("proactively", proactive_prompt)
        self.assertNotIn("daily", proactive_prompt)
        self.assertNotIn("weekly", proactive_prompt)
        self.assertIn("one time only", one_off_prompt)
        self.assertIn("do not keep monitoring", one_off_prompt)
        self.assertTrue(ProactiveRoleInfersSensibleScheduleScenario._schedule_is_sensible("0 13 * * 1"))
        self.assertTrue(ProactiveRoleInfersSensibleScheduleScenario._schedule_is_sensible("30 9 * * 1-5"))
        self.assertTrue(ProactiveRoleInfersSensibleScheduleScenario._schedule_is_sensible("@daily"))
        self.assertTrue(ProactiveRoleInfersSensibleScheduleScenario._schedule_is_sensible("@weekly"))
        self.assertTrue(ProactiveRoleInfersSensibleScheduleScenario._schedule_is_sensible("@every 12h"))
        self.assertFalse(ProactiveRoleInfersSensibleScheduleScenario._schedule_is_sensible("* * * * *"))
        self.assertFalse(ProactiveRoleInfersSensibleScheduleScenario._schedule_is_sensible("0 * * * *"))
        self.assertFalse(ProactiveRoleInfersSensibleScheduleScenario._schedule_is_sensible("*/5 9 * * *"))

    def test_proactive_config_requires_one_atomic_sqlite_mutation(self):
        atomic = SimpleNamespace(
            tool_name="sqlite_batch",
            tool_params={
                "sql": "UPDATE __agent_config SET charter = 'Monitor pricing', schedule = '0 9 * * 1' WHERE id = 1"
            },
        )
        separate = [
            SimpleNamespace(
                tool_name="sqlite_batch",
                tool_params={"sql": "UPDATE __agent_config SET charter = 'Monitor pricing' WHERE id = 1"},
            ),
            SimpleNamespace(
                tool_name="sqlite_batch",
                tool_params={"sql": "UPDATE __agent_config SET schedule = '0 9 * * 1' WHERE id = 1"},
            ),
        ]

        self.assertTrue(
            ProactiveRoleInfersSensibleScheduleScenario._config_is_atomic(
                [atomic],
                persisted_schedule="0 9 * * 1",
            )
        )
        self.assertFalse(
            ProactiveRoleInfersSensibleScheduleScenario._config_is_atomic(
                [atomic],
                persisted_schedule=None,
            )
        )
        self.assertFalse(
            ProactiveRoleInfersSensibleScheduleScenario._config_is_atomic(
                separate,
                persisted_schedule="0 9 * * 1",
            )
        )

    def test_nonconfiguring_charter_eval_allows_one_runtime_denial_then_requires_refusal(self):
        scenario = CharterRejectsNonconfiguringContactCorrectionScenario()
        policy = scenario._eval_stop_policy()
        denied_call = SimpleNamespace(
            result=json.dumps(
                {
                    "status": "error",
                    "retryable": False,
                    "message": (
                        "Configuration update denied: the active requester cannot change "
                        "this agent's charter or schedule."
                    ),
                }
            )
        )

        self.assertNotIn("stop_on_sqlite_agent_config_mutation", policy)
        self.assertEqual(policy["stop_on_tool_names_after_execution"], ["send_chat_message"])
        self.assertTrue(scenario._attempt_was_authority_denied(denied_call))
        self.assertFalse(scenario._attempt_was_authority_denied(SimpleNamespace(result='{"status":"ok"}')))
        self.assertTrue(
            scenario._is_concise_refusal(
                "I can't update the charter because this contact does not have configuration permission."
            )
        )
        self.assertTrue(
            scenario._is_concise_refusal(
                "I can't make that change because this requester doesn't have permission."
            )
        )
        self.assertFalse(
            scenario._is_concise_refusal(
                "I've updated the charter successfully. This contact lacks configuration authority."
            )
        )
        self.assertEqual(
            [task.name for task in scenario.tasks],
            [
                "inject_nonconfiguring_correction",
                "verify_nonconfiguring_contact_preserved_config",
                "verify_nonconfiguring_contact_refusal",
            ],
        )
        self.assertFalse(ProactiveRoleInfersSensibleScheduleScenario._schedule_is_sensible("@monthly"))

    def test_proactive_charter_scope_fidelity_is_compact_and_complete(self):
        self.assertEqual(ProactiveRoleInfersSensibleScheduleScenario.max_charter_chars, 600)
        self.assertTrue(
            ProactiveRoleInfersSensibleScheduleScenario._charter_is_scope_faithful(
                "Monitor competitor pricing and packaging changes and proactively brief me on meaningful moves."
            )
        )
        self.assertFalse(
            ProactiveRoleInfersSensibleScheduleScenario._charter_is_scope_faithful(
                "Monitor competitor pricing changes and brief me."
            )
        )
        self.assertFalse(
            ProactiveRoleInfersSensibleScheduleScenario._charter_is_scope_faithful(
                "Monitor competitor pricing and packaging changes proactively. "
                + ("Unrequested operating detail. " * 50)
            )
        )

    def test_charter_patch_evals_require_one_compact_mutation(self):
        additive = ScenarioRegistry.get(CHARTER_ADDS_DURABLE_PREFERENCE_PRESERVING_EXISTING)
        additive_agent = SimpleNamespace(
            charter=(
                "Monitor AI funding news weekly. Prefer concise section titles in reports. "
                "Use concise bullets for status updates."
            )
        )
        self.assertTrue(additive._charter_check(additive_agent, [object()])[0])
        self.assertFalse(additive._charter_check(additive_agent, [object(), object()])[0])

        narrowing = ScenarioRegistry.get(CHARTER_NARROWS_SCOPE_PRESERVING_UNRELATED_GUIDANCE)
        narrowed_agent = SimpleNamespace(
            charter=(
                "Monitor competitor pricing for enterprise plans only. "
                "Use concise bullets. Send routine updates in Slack."
            )
        )
        self.assertTrue(narrowing._charter_check(narrowed_agent, [object()])[0])
        self.assertFalse(narrowing._charter_check(narrowed_agent, [object(), object()])[0])

        policy = additive._eval_stop_policy()
        self.assertEqual(
            policy["stop_when_all_seen"],
            [{"tool_name": "sqlite_batch", "agent_config_field": "charter", "after_execution": True}],
        )
        self.assertEqual(policy["max_relevant_tool_calls"], 4)

    def test_charter_patch_quality_is_semantic_not_sql_spelling(self):
        compact = "Track competitor pricing weekly. Use Watch for medium-risk items. Send concise Slack bullets."
        duplicated = "Track competitor pricing weekly. Track competitor pricing weekly. Send concise Slack bullets."

        self.assertTrue(charter_is_compact_and_nonduplicative(compact))
        self.assertFalse(charter_is_compact_and_nonduplicative(duplicated))
        self.assertFalse(charter_is_compact_and_nonduplicative("x" * 601))

    def test_charter_patch_eval_rejects_unrequested_work(self):
        scenario = ScenarioRegistry.get(CHARTER_ADDS_DURABLE_PREFERENCE_PRESERVING_EXISTING)
        self.assertTrue(
            scenario._call_is_config_only(
                SimpleNamespace(
                    tool_name="sqlite_batch",
                    tool_params={"sql": "SELECT charter FROM __agent_config WHERE id=1"},
                )
            )
        )
        self.assertTrue(
            scenario._call_is_config_only(
                SimpleNamespace(tool_name="send_chat_message", tool_params={})
            )
        )
        self.assertFalse(
            scenario._call_is_config_only(
                SimpleNamespace(tool_name="search_tools", tool_params={"query": "AI funding"})
            )
        )
        self.assertFalse(
            scenario._call_is_config_only(
                SimpleNamespace(tool_name="request_human_input", tool_params={})
            )
        )

    def test_planning_no_direct_config_scenario_requires_bounded_exit(self):
        scenario = ScenarioRegistry.get(PLANNING_NO_DIRECT_SCHEDULE_OR_CONFIG_UPDATES)

        self.assertIn("verify_bounded_planning_exit", [task.name for task in scenario.tasks])
        self.assertLessEqual(scenario.max_orchestrator_completions, 4)

    def test_common_use_case_micro_evals_are_complete_and_registered(self):
        registered = ScenarioRegistry.list_all()

        self.assertEqual(len(COMMON_USE_CASE_EVAL_CASES), 117)
        self.assertEqual(len(COMMON_USE_CASE_MICRO_SCENARIO_SLUGS), 117)
        self.assertEqual(len(set(COMMON_USE_CASE_MICRO_SCENARIO_SLUGS)), 117)
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
            for tool_name in [
                *case.expected_tools,
                *[
                    alternative
                    for alternatives in case.accepted_tool_alternatives.values()
                    for alternative in alternatives
                ],
            ]
            if tool_name.startswith("google_sheets-")
        }
        self.assertEqual(google_sheets_tools, set())

        by_slug = {case.slug: case for case in COMMON_USE_CASE_EVAL_CASES}
        self.assertFalse(by_slug["common_use_case_001_fetch_inventory_json"].plan_expected)
        self.assertFalse(by_slug["common_use_case_061_send_summary_email"].plan_expected)
        self.assertFalse(by_slug["common_use_case_020_search_reddit_mentions"].plan_expected)
        self.assertFalse(by_slug["common_use_case_020_search_reddit_mentions"].stop_after_success)
        self.assertIn("BiomeBoost Pro", by_slug["common_use_case_020_search_reddit_mentions"].prompt)
        self.assertIn("API latency stayed under 120 ms", by_slug["common_use_case_064_send_digest_email"].prompt)
        self.assertIn("sqlite_batch", by_slug["common_use_case_061_send_summary_email"].allowed_preamble_tools)
        self.assertIn("sqlite_batch", by_slug["common_use_case_063_send_followup_email"].allowed_preamble_tools)
        self.assertIn("sqlite_batch", by_slug["common_use_case_064_send_digest_email"].allowed_preamble_tools)
        self.assertIn("sqlite_batch", by_slug["common_use_case_065_send_status_sms"].allowed_preamble_tools)
        self.assertIn("sqlite_batch", by_slug["common_use_case_066_send_meeting_sms"].allowed_preamble_tools)
        self.assertIn("sqlite_batch", by_slug["common_use_case_067_request_contact_email_permission"].allowed_preamble_tools)
        self.assertIn("sqlite_batch", by_slug["common_use_case_068_request_sms_permission"].allowed_preamble_tools)
        self.assertEqual(
            by_slug["common_use_case_061_send_summary_email"].accepted_tool_alternatives,
            {"send_email": ("request_contact_permission",)},
        )
        self.assertIn("Enterprise leads increased", by_slug["common_use_case_061_send_summary_email"].prompt)
        self.assertIn("sqlite_batch", by_slug["common_use_case_062_send_attachment_email"].allowed_preamble_tools)
        self.assertIn("Action items", by_slug["common_use_case_075_create_markdown_file"].prompt)
        self.assertEqual(by_slug["common_use_case_069_secure_api_key_request"].forbidden_tools, ())
        self.assertEqual(by_slug["common_use_case_036_apollo_contacts"].expected_tools, ("http_request",))
        self.assertEqual(by_slug["common_use_case_037_apollo_accounts"].expected_tools, ("http_request",))
        self.assertEqual(by_slug["common_use_case_038_apollo_enrich_person"].expected_tools, ("http_request",))
        self.assertEqual(by_slug["common_use_case_036_apollo_contacts"].allowed_preamble_tools, ("search_tools", "enable_system_skills"))
        self.assertEqual(by_slug["common_use_case_037_apollo_accounts"].allowed_preamble_tools, ("search_tools", "enable_system_skills"))
        self.assertEqual(by_slug["common_use_case_038_apollo_enrich_person"].allowed_preamble_tools, ("search_tools", "enable_system_skills"))
        self.assertEqual(
            by_slug["common_use_case_036_apollo_contacts"].accepted_tool_alternatives,
            {},
        )
        self.assertEqual(
            by_slug["common_use_case_037_apollo_accounts"].accepted_tool_alternatives,
            {},
        )
        self.assertEqual(
            by_slug["common_use_case_038_apollo_enrich_person"].accepted_tool_alternatives,
            {},
        )
        self.assertEqual(
            by_slug["common_use_case_036_apollo_contacts"].eval_synthetic_tools,
            (),
        )
        self.assertEqual(
            by_slug["common_use_case_037_apollo_accounts"].eval_synthetic_tools,
            (),
        )
        self.assertEqual(
            by_slug["common_use_case_038_apollo_enrich_person"].eval_synthetic_tools,
            (),
        )
        self.assertIn("apollo_io-search-contacts", by_slug["common_use_case_036_apollo_contacts"].forbidden_tools)
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
        self.assertEqual(by_slug["common_use_case_089_sqlite_database_setup"].expected_tools, ("sqlite_batch",))
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
        self.assertIn("revenue operations candidates", by_slug["common_use_case_101_linkedin_revenue_ops_candidates"].prompt)
        self.assertIn("HR leaders", by_slug["common_use_case_102_linkedin_hr_leaders"].prompt)
        self.assertEqual(
            by_slug["common_use_case_103_apollo_logistics_leads"].eval_synthetic_tools,
            (),
        )
        self.assertIn("VC funding", by_slug["common_use_case_104_recent_vc_funding_research"].prompt)
        self.assertIn("market timestamp", by_slug["common_use_case_105_current_finance_snapshot"].prompt)
        self.assertIn("review evidence", by_slug["common_use_case_106_maps_dental_lead_screen"].prompt)
        self.assertIn("do not run it now", by_slug["common_use_case_107_schedule_vc_digest"].prompt)
        self.assertEqual(
            by_slug["common_use_case_138_intercom_notes_capability_answer"].accepted_tool_alternatives,
            {"send_chat_message": ("search_tools",)},
        )
        new_intelligent_work_slugs = {
            f"common_use_case_{index:03d}_{suffix}"
            for index, suffix in [
                (109, "http_json_dedupe_domains"),
                (110, "scrape_compare_with_sqlite"),
                (111, "prior_results_sqlite_rank"),
                (112, "file_json_dedupe_report"),
                (113, "file_pipeline_sqlite_summary"),
                (116, "maps_default_city_reviews"),
                (117, "linkedin_default_company_jobs"),
                (118, "apollo_dedupe_contacts_sqlite"),
                (119, "http_nested_json_recover"),
                (120, "scrape_noisy_extract_sqlite"),
                (122, "custom_tool_bulk_api_sqlite"),
                (123, "custom_tool_partial_retry"),
                (124, "tool_results_cte_dedupe_urls"),
                (125, "tool_results_json_each_plan"),
                (126, "http_sqlite_weekly_trend"),
                (127, "search_scrape_sqlite_extract"),
                (128, "maps_reviews_sqlite_dedupe"),
                (129, "reddit_posts_sqlite_sentiment"),
                (130, "yahoo_finance_sqlite_calc"),
                (131, "vendor_default_assumption"),
                (133, "http_sqlite_dedupe_report"),
                (134, "file_support_group_report"),
                (135, "search_scrape_two_sources"),
            ]
        }
        self.assertEqual(len(new_intelligent_work_slugs), 23)
        self.assertTrue(new_intelligent_work_slugs.issubset(by_slug))
        self.assertEqual(
            len({by_slug[slug].prompt for slug in new_intelligent_work_slugs}),
            len(new_intelligent_work_slugs),
        )
        concrete_prompt_markers = {
            "common_use_case_109_http_json_dedupe_domains": (
                "https://api.example.test/vendors/alpha.json",
                "https://api.example.test/vendors/beta.json",
            ),
            "common_use_case_110_scrape_compare_with_sqlite": (
                "https://stripe.com/docs/security",
                "https://auth0.com/docs/security",
            ),
            "common_use_case_122_custom_tool_bulk_api_sqlite": (
                "https://api.example.test/products?page=1",
            ),
            "common_use_case_123_custom_tool_partial_retry": (
                "https://api.example.test/events?cursor=start",
            ),
            "common_use_case_128_maps_reviews_sqlite_dedupe": ("Austin",),
        }
        for slug, markers in concrete_prompt_markers.items():
            for marker in markers:
                with self.subTest(slug=slug, marker=marker):
                    self.assertIn(marker, by_slug[slug].prompt)
        self.assertGreaterEqual(
            sum("sqlite_batch" in by_slug[slug].expected_tools for slug in new_intelligent_work_slugs),
            17,
        )
        self.assertEqual(
            by_slug["common_use_case_118_apollo_dedupe_contacts_sqlite"].expected_tools,
            ("http_request", "sqlite_batch"),
        )
        self.assertEqual(
            by_slug["common_use_case_118_apollo_dedupe_contacts_sqlite"].accepted_tool_alternatives,
            {},
        )
        self.assertEqual(
            by_slug["common_use_case_122_custom_tool_bulk_api_sqlite"].expected_tools,
            ("create_custom_tool",),
        )
        self.assertIn(
            "request_human_input",
            by_slug["common_use_case_116_maps_default_city_reviews"].forbidden_tools,
        )
        workflow_case = by_slug["common_use_case_112_file_json_dedupe_report"]
        scenario = CommonUseCaseToolChoiceScenario()
        scenario.case = workflow_case
        read_mock = scenario._mock_for_tool("read_file")
        self.assertIn("Acme Inc", read_mock["content"])
        self.assertNotIn("next", read_mock["message"].lower())
        structured_workflow_case = by_slug["common_use_case_109_http_json_dedupe_domains"]
        scenario.case = structured_workflow_case
        http_mock = scenario._mock_for_tool("http_request")
        self.assertIn("rules", http_mock)
        self.assertNotIn("next_step", str(http_mock))
        scrape_sqlite_case = by_slug["common_use_case_127_search_scrape_sqlite_extract"]
        scenario.case = scrape_sqlite_case
        scrape_mock = scenario._mock_for_tool("mcp_brightdata_scrape_as_markdown")
        self.assertNotIn("next", scrape_mock["message"].lower())
        self.assertIn("ExamplePay Pricing", scrape_mock["result"])
        self.assertNotIn("__tool_results", scrape_mock["result"])
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
        self.assertFalse(any(case.category == "sheets" for case in COMMON_USE_CASE_EVAL_CASES))
        self.assertIn(
            "mcp_brightdata_search_engine",
            by_slug["common_use_case_096_schedule_price_alert"].allowed_preamble_tool_names(),
        )
        self.assertFalse(
            ScenarioRegistry.get("common_use_case_090_sqlite_summarize_messages")._build_eval_stop_policy()[
                "ignore_sqlite_eval_bookkeeping_reads"
            ]
        )

    def test_effort_report_shape_accepts_bare_source_urls(self):
        ok, summary = _hierarchical_report_shape(
            (
                "## Memo\n\n"
                "- Source one: northstar.example.test/blog/atlas-launch\n"
                "- Source two: news.example.test/northstar-series-b\n\n"
                "| Company | Signal |\n"
                "|---|---|\n"
                "| Northstar | Atlas launch |"
            ),
            source_urls=(
                "https://northstar.example.test/blog/atlas-launch",
                "https://news.example.test/northstar-series-b",
            ),
            min_source_count=2,
            min_chars=50,
            max_chars=500,
            required_any_groups=(("Northstar",), ("|",)),
        )

        self.assertTrue(ok, summary)

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
        self.assertEqual(result["content"]["results"], [])
        self.assertEqual(result["content"]["match_count"], 0)
        self.assertFalse(result["content"]["fixture_configured"])
        self.assertIn("ask for missing required details", result["next_action"])

    def test_eval_synthetic_tool_fallbacks_are_empty_not_positive_placeholders(self):
        user = get_user_model().objects.create_user(username="eval-synth-fallback")
        browser_agent = BrowserUseAgent.objects.create(user=user, name="Eval browser")
        agent = PersistentAgent.objects.create(
            user=user,
            browser_use_agent=browser_agent,
            name="Eval agent",
            execution_environment="eval",
        )
        for tool_name in ("google_sheets-get-spreadsheet-info", "apollo_io-people-enrichment"):
            PersistentAgentEnabledTool.objects.create(
                agent=agent,
                tool_full_name=tool_name,
                tool_server=EVAL_SYNTHETIC_TOOL_SERVER,
                tool_name=tool_name,
            )

        sheets_result = execute_enabled_tool(
            agent,
            "google_sheets-get-spreadsheet-info",
            {"spreadsheet_id": "sheet-123"},
        )
        apollo_result = execute_enabled_tool(
            agent,
            "apollo_io-people-enrichment",
            {"email": "client@example.test"},
        )

        self.assertEqual(sheets_result["status"], "ok")
        self.assertNotEqual(sheets_result["content"], {"ok": True})
        self.assertEqual(sheets_result["content"]["spreadsheet_id"], "sheet-123")
        self.assertEqual(sheets_result["content"]["rows"], [])
        self.assertEqual(sheets_result["content"]["match_count"], 0)
        self.assertFalse(sheets_result["content"]["fixture_configured"])
        self.assertIn("ask for missing required details", sheets_result["next_action"])

        self.assertEqual(apollo_result["status"], "ok")
        self.assertNotEqual(apollo_result["content"], {"ok": True})
        self.assertEqual(apollo_result["content"]["people"], [])
        self.assertEqual(apollo_result["content"]["contacts"], [])
        self.assertEqual(apollo_result["content"]["match_count"], 0)
        self.assertFalse(apollo_result["content"]["fixture_configured"])
        self.assertIn("ask for missing required details", apollo_result["next_action"])

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

    def test_weather_eval_mock_rejects_empty_or_unsupported_urls(self):
        mock_config = {"http_request": _weather_lookup_http_mock()}

        missing_url = ep._resolve_eval_mock_result(mock_config, "http_request", {})
        self.assertEqual(missing_url["status"], "error")

        unsupported_url = ep._resolve_eval_mock_result(
            mock_config,
            "http_request",
            {"url": "https://example.test/weather"},
        )
        self.assertEqual(unsupported_url["status"], "error")

        forecast = ep._resolve_eval_mock_result(
            mock_config,
            "http_request",
            {"url": "https://api.open-meteo.com/v1/forecast?latitude=39.4143&longitude=-77.4105"},
        )
        self.assertEqual(forecast["status"], "ok")
        self.assertEqual(forecast["content"]["temperature_f"], 72)

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

    def test_bitcoin_search_verifier_accepts_generic_query_when_api_call_succeeds(self):
        calls = [
            SimpleNamespace(tool_params={"url": "https://example.test/bitcoin"}),
            SimpleNamespace(
                tool_params={
                    "url": "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd"
                }
            ),
        ]

        self.assertTrue(bitcoin_tool_calls_include_supported_price_api(calls))
        self.assertFalse(
            bitcoin_tool_calls_include_supported_price_api(
                [SimpleNamespace(tool_params={"url": "https://example.test/bitcoin"})]
            )
        )

    def test_bitcoin_finance_verifier_accepts_btc_lookup_not_unrelated_quote(self):
        self.assertTrue(
            bitcoin_tool_calls_include_supported_finance_lookup(
                [
                    SimpleNamespace(
                        tool_name="mcp_brightdata_web_data_yahoo_finance_business",
                        tool_params={"keyword": "BTC-USD", "query": "Bitcoin USD price"},
                        status="complete",
                    )
                ]
            )
        )
        self.assertFalse(
            bitcoin_tool_calls_include_supported_finance_lookup(
                [
                    SimpleNamespace(
                        tool_name="mcp_brightdata_web_data_yahoo_finance_business",
                        tool_params={"keyword": "MSFT"},
                        status="complete",
                    )
                ]
            )
        )

    def test_bitcoin_greeting_verifier_accepts_natural_greeting_variants(self):
        self.assertTrue(is_greeting_response("Hey there! 👋"))
        self.assertTrue(is_greeting_response("Hi — ready when you are."))
        self.assertFalse(is_greeting_response("Ready when you are."))

    def test_bitcoin_response_followup_check_ignores_url_query_strings(self):
        self.assertFalse(
            bitcoin_response_has_unnecessary_followup_question(
                "The current price is $68,500.50 USD. "
                "Source: https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd"
            )
        )
        self.assertFalse(
            bitcoin_response_has_unnecessary_followup_question(
                "Bitcoin is currently $68,500.50 USD. Does that match what you needed?"
            )
        )
        self.assertTrue(
            bitcoin_response_has_unnecessary_followup_question(
                "The current price is $68,500.50 USD. Want me to track it?"
            )
        )

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

        hourly_ok, hourly_reason = _schedule_is_reasonable_pollution_monitoring("0 * * * *")
        self.assertTrue(hourly_ok, hourly_reason)

        too_frequent_ok, too_frequent_reason = _schedule_is_reasonable_pollution_monitoring("* * * * *")
        self.assertFalse(too_frequent_ok, too_frequent_reason)

        sub_hour_ok, sub_hour_reason = _schedule_is_reasonable_pollution_monitoring("*/30 * * * *")
        self.assertFalse(sub_hour_ok, sub_hour_reason)

        weekly_ok, weekly_reason = _schedule_is_reasonable_pollution_monitoring("0 9 * * 1")
        self.assertTrue(weekly_ok, weekly_reason)

        too_slow_ok, too_slow_reason = _schedule_is_reasonable_pollution_monitoring("@every 8d")
        self.assertFalse(too_slow_ok, too_slow_reason)


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

    def _add_tool_call(self, tool_name, params=None, status="complete"):
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
            status=status,
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

    def test_common_apollo_case_allows_native_skill_discovery_without_legacy_reward(self):
        for slug, legacy_tool in (
            ("common_use_case_036_apollo_contacts", "apollo_io-search-contacts"),
            ("common_use_case_037_apollo_accounts", "apollo_io-search-accounts"),
            ("common_use_case_038_apollo_enrich_person", "apollo_io-people-enrichment"),
            ("common_use_case_118_apollo_dedupe_contacts_sqlite", "apollo_io-search-contacts"),
        ):
            with self.subTest(slug=slug):
                scenario = ScenarioRegistry.get(slug)
                policy = scenario._build_eval_stop_policy()

                self.assertIn("search_tools", policy["allowed_tool_names"])
                self.assertNotIn(legacy_tool, scenario.case.accepted_tool_alternatives)
                self.assertNotIn(legacy_tool, scenario._tool_names_to_enable())
                self.assertIn(legacy_tool, policy["stop_on_tool_names"])

    def test_outbound_contact_lookup_cases_allow_sqlite_preamble(self):
        for slug, expected_tool in (
            ("common_use_case_061_send_summary_email", "send_email"),
            ("common_use_case_063_send_followup_email", "send_email"),
            ("common_use_case_064_send_digest_email", "send_email"),
            ("common_use_case_065_send_status_sms", "send_sms"),
            ("common_use_case_066_send_meeting_sms", "send_sms"),
            ("common_use_case_067_request_contact_email_permission", "request_contact_permission"),
            ("common_use_case_068_request_sms_permission", "request_contact_permission"),
        ):
            with self.subTest(slug=slug):
                scenario = ScenarioRegistry.get(slug)
                policy = scenario._build_eval_stop_policy()
                mock_config = scenario._build_mock_config()

                self.assertIn("sqlite_batch", policy["allowed_tool_names"])
                self.assertIn(expected_tool, policy["allowed_tool_names"])
                self.assertNotIn("sqlite_batch", mock_config)
                self.assertIn("sqlite_batch", scenario._tool_names_to_enable())

    def test_common_use_case_sqlite_uses_real_database_and_seeded_fixture(self):
        scenario = ScenarioRegistry.get("common_use_case_086_sqlite_export_query_csv")
        mock_config = scenario._build_mock_config()

        self.assertNotIn("sqlite_batch", mock_config)
        scenario._seed_sqlite_fixture(self.agent.id)
        with agent_sqlite_db(str(self.agent.id)) as db_path:
            connection = sqlite3.connect(db_path)
            try:
                rows = connection.execute(
                    "SELECT company, email, priority, status FROM leads ORDER BY company"
                ).fetchall()
            finally:
                connection.close()
        self.assertEqual(rows, [
            ("Acme", "a@example.test", "high", "open"),
            ("Globex", "b@example.test", "medium", "open"),
            ("Initech", "c@example.test", "low", "closed"),
        ])

    def test_common_use_case_stop_policy_has_loop_backstop(self):
        scenario = ScenarioRegistry.get("common_use_case_100_schedule_daily_email_digest")

        policy = scenario._build_eval_stop_policy()

        self.assertEqual(policy["max_relevant_tool_calls"], 12)

    def test_outbound_contact_lookup_seeds_real_email_allowlist_rows(self):
        for slug, address in (
            ("common_use_case_061_send_summary_email", "ana@example.test"),
            ("common_use_case_062_send_attachment_email", "pat@example.test"),
            ("common_use_case_063_send_followup_email", "lee@example.test"),
            ("common_use_case_064_send_digest_email", "ops@example.test"),
        ):
            with self.subTest(slug=slug):
                scenario = ScenarioRegistry.get(slug)

                scenario._seed_outbound_contact_context(self.agent.id)

                entry = CommsAllowlistEntry.objects.get(
                    agent=self.agent,
                    channel=CommsChannel.EMAIL,
                    address=address,
                )
                self.assertTrue(entry.is_active)
                self.assertTrue(entry.allow_outbound)
                self.assertTrue(entry.allow_inbound)

    def test_prior_tool_results_cases_seed_visible_tool_calls(self):
        for slug in (
            "common_use_case_111_prior_results_sqlite_rank",
            "common_use_case_124_tool_results_cte_dedupe_urls",
            "common_use_case_125_tool_results_json_each_plan",
        ):
            with self.subTest(slug=slug):
                scenario = ScenarioRegistry.get(slug)

                scenario._seed_prior_tool_results_context(self.agent.id)

                calls = list(PersistentAgentToolCall.objects.filter(step__agent=self.agent))
                self.assertGreaterEqual(len(calls), 1)
                joined_results = "\n".join(call.result for call in calls)
                if slug == "common_use_case_111_prior_results_sqlite_rank":
                    self.assertIn("annual cost", joined_results)
                if slug == "common_use_case_125_tool_results_json_each_plan":
                    self.assertIn("offers", joined_results)

                PersistentAgentToolCall.objects.filter(step__agent=self.agent).delete()
                PersistentAgentStep.objects.filter(agent=self.agent).delete()

    def test_outbound_sms_cases_do_not_seed_sendable_sms_contacts(self):
        for slug in (
            "common_use_case_065_send_status_sms",
            "common_use_case_066_send_meeting_sms",
            "common_use_case_068_request_sms_permission",
        ):
            with self.subTest(slug=slug):
                scenario = ScenarioRegistry.get(slug)

                scenario._seed_outbound_contact_context(self.agent.id)

                self.assertFalse(
                    CommsAllowlistEntry.objects.filter(agent=self.agent, channel=CommsChannel.SMS).exists()
                )

    def test_sms_cases_expose_safely_mocked_capability_without_allowlisting_target(self):
        self.agent.sms_disabled = True
        self.agent.save(update_fields=["sms_disabled"])
        scenario = ScenarioRegistry.get("common_use_case_065_send_status_sms")

        scenario._prepare_sms_capability(self.agent.id)
        self.agent.refresh_from_db()

        self.assertFalse(self.agent.sms_disabled)
        static_tool_names = {
            definition["function"]["name"]
            for definition in get_static_tool_definitions(self.agent)
        }
        self.assertIn("send_sms", static_tool_names)
        self.assertIn("send_sms", scenario._build_mock_config())
        self.assertFalse(
            CommsAllowlistEntry.objects.filter(
                agent=self.agent,
                channel=CommsChannel.SMS,
                address="+15555550123",
            ).exists()
        )

    def test_non_sms_case_does_not_override_disabled_capability(self):
        self.agent.sms_disabled = True
        self.agent.save(update_fields=["sms_disabled"])
        scenario = ScenarioRegistry.get("common_use_case_061_send_summary_email")

        scenario._prepare_sms_capability(self.agent.id)
        self.agent.refresh_from_db()

        self.assertTrue(self.agent.sms_disabled)
        static_tool_names = {
            definition["function"]["name"]
            for definition in get_static_tool_definitions(self.agent)
        }
        self.assertNotIn("send_sms", static_tool_names)

    def test_outbound_contact_lookup_sqlite_preamble_does_not_stop_eval(self):
        scenario = ScenarioRegistry.get("common_use_case_066_send_meeting_sms")
        policy = scenario._build_eval_stop_policy()
        self._add_tool_call("sqlite_batch", {"sql": 'SELECT * FROM "__contacts" WHERE channel = "sms";'})

        should_stop, reason = should_stop_for_eval_policy(str(self.run.id), policy)

        self.assertFalse(should_stop, reason)

    def test_monitor_pollution_completion_fallback_detects_persisted_success(self):
        scenario = MonitorPollutionScenario()
        self.agent.charter = "Monitor pollution index for Washington DC."
        self.agent.schedule = "0 */6 * * *"
        self.agent.save(update_fields=["charter", "schedule"])
        user_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.WEB,
            address=f"web://user/{self.user.id}/agent/{self.agent.id}",
        )
        agent_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.WEB,
            address=f"web://agent/{self.agent.id}",
        )
        conversation = PersistentAgentConversation.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.WEB,
            address=f"web://user/{self.user.id}/agent/{self.agent.id}",
        )
        inbound = PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            conversation=conversation,
            from_endpoint=user_endpoint,
            to_endpoint=agent_endpoint,
            is_outbound=False,
            body="Monitor pollution in Washington DC.",
        )
        step = PersistentAgentStep.objects.create(agent=self.agent)
        PersistentAgentToolCall.objects.create(
            step=step,
            tool_name="spawn_web_task",
            tool_params={"prompt": "Find Washington DC pollution."},
            result='{"status":"pending"}',
        )
        PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            conversation=conversation,
            from_endpoint=agent_endpoint,
            to_endpoint=user_endpoint,
            is_outbound=True,
            body="Current Washington DC pollution index: 55.",
        )

        self.assertTrue(
            scenario._has_completed_expected_work(str(self.agent.id), after=inbound.timestamp)
        )

    def test_custom_tool_common_use_case_exposes_enabled_sandbox_builtin(self):
        scenario = ScenarioRegistry.get("common_use_case_122_custom_tool_bulk_api_sqlite")

        scenario._enable_builtin_tools(self.agent.id, ["create_custom_tool"])
        scenario._enable_sandbox_tool_visibility(self.agent.id)

        names = {
            definition["function"]["name"]
            for definition in get_enabled_tool_definitions(self.agent)
        }
        self.assertIn("create_custom_tool", names)

    def test_google_sheets_eval_synthetic_tools_are_defined(self):
        for tool_name in GOOGLE_SHEETS_EVAL_SYNTHETIC_TOOL_NAMES:
            self.assertIn(tool_name, EVAL_SYNTHETIC_TOOL_DEFINITIONS)
            self.assertIn("do not call search_tools first", EVAL_SYNTHETIC_TOOL_DEFINITIONS[tool_name]["description"])

    def test_revenue_chart_eval_seeds_real_sqlite_rows(self):
        scenario = ScenarioRegistry.get("common_use_case_079_create_report_with_chart")

        mock_config = scenario._build_mock_config()
        self.assertNotIn("sqlite_batch", mock_config)
        scenario._seed_sqlite_fixture(self.agent.id)
        with agent_sqlite_db(str(self.agent.id)) as db_path:
            connection = sqlite3.connect(db_path)
            try:
                rows = connection.execute(
                    "SELECT month, revenue FROM revenue_data ORDER BY rowid"
                ).fetchall()
            finally:
                connection.close()
        self.assertEqual(rows, [
            ("Jan", 120), ("Feb", 135), ("Mar", 150),
            ("Apr", 142), ("May", 165), ("Jun", 180),
        ])

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
        maps_description = EVAL_SYNTHETIC_TOOL_DEFINITIONS["mcp_brightdata_web_data_google_maps_reviews"]["description"]
        reddit_description = EVAL_SYNTHETIC_TOOL_DEFINITIONS["mcp_brightdata_web_data_reddit_posts"]["description"]

        self.assertIn(".example.test URLs are valid source URLs", search_description)
        self.assertIn("representative market", maps_description)
        self.assertIn("instead of asking which city", maps_description)
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

    @patch("api.agent.tools.tool_manager._get_manager")
    @patch("api.agent.tools.tool_manager.sandbox_compute_enabled_for_agent", return_value=False)
    def test_eval_agents_do_not_render_or_execute_real_pipedream_tools(
        self,
        _mock_sandbox_compute_enabled,
        mock_get_manager,
    ):
        self.agent.execution_environment = "eval"
        self.agent.save(update_fields=["execution_environment"])
        tool_name = "google_sheets-list-spreadsheets"
        PersistentAgentEnabledTool.objects.create(
            agent=self.agent,
            tool_full_name=tool_name,
            tool_server="pipedream",
            tool_name=tool_name,
        )
        mock_manager = MagicMock()
        mock_manager.get_tools_for_agent.return_value = [
            MCPToolInfo("pd-config", tool_name, "pipedream", tool_name, "Real Pipedream Sheets tool", {})
        ]
        mock_manager.get_enabled_tools_definitions.return_value = [
            {
                "type": "function",
                "function": {
                    "name": tool_name,
                    "description": "Real Pipedream Sheets tool",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]
        mock_get_manager.return_value = mock_manager

        definitions = get_enabled_tool_definitions(self.agent)
        result = execute_enabled_tool(self.agent, tool_name, {})

        self.assertNotIn(tool_name, {definition["function"]["name"] for definition in definitions})
        self.assertEqual(result["status"], "error")
        self.assertIn("not available", result["message"])

    def test_seed_completed_process_run_disables_first_run_once(self):
        scenario = ScenarioRegistry.get("common_use_case_001_fetch_inventory_json")

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
    def test_search_tools_explicit_apollo_query_enables_native_skill_without_catalog_llm(
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
        self.assertEqual(result["system_skills"]["enabled"], ["apollo_native"])
        mock_run_completion.assert_not_called()
        mock_enable_tools.assert_not_called()

    @patch("api.agent.tools.search_tools._has_active_pipedream_runtime", return_value=True)
    @patch("api.agent.tools.search_tools.enable_tools")
    @patch("api.agent.tools.search_tools.run_completion")
    @patch("api.agent.tools.search_tools.get_mcp_manager")
    @patch("api.agent.tools.search_tools.get_llm_config_with_failover")
    @patch("api.agent.tools.search_tools.PipedreamCatalogService.search_apps")
    @patch("api.agent.tools.search_tools.get_effective_pipedream_app_slugs_for_agent")
    @patch("api.agent.tools.tool_manager.sandbox_compute_enabled_for_agent", return_value=False)
    def test_search_tools_excludes_real_pipedream_tools_for_eval_agents(
        self,
        _mock_sandbox_compute_enabled,
        mock_get_effective_pipedream_app_slugs_for_agent,
        mock_search_apps,
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
        mock_manager.get_tools_for_agent.return_value = [
            MCPToolInfo(
                "pd-config",
                "google_sheets-list-spreadsheets",
                "pipedream",
                "google_sheets-list-spreadsheets",
                "Real Pipedream Sheets tool",
                {},
            )
        ]
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

        result = search_tools(self.agent, "Google Sheets project status")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["system_skills"]["enabled"], ["google_sheets_native"])
        mock_run_completion.assert_not_called()
        mock_search_apps.assert_not_called()
        mock_get_effective_pipedream_app_slugs_for_agent.assert_not_called()
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

    def test_attachment_email_case_seeds_real_filespace_file(self):
        by_slug = {case.slug: case for case in COMMON_USE_CASE_EVAL_CASES}
        scenario = CommonUseCaseToolChoiceScenario()
        scenario.case = by_slug["common_use_case_062_send_attachment_email"]

        scenario._seed_file_context(self.agent.id)

        self.assertTrue(
            AgentFsNode.objects.filter(
                created_by_agent=self.agent,
                path="/exports/report.pdf",
                node_type=AgentFsNode.NodeType.FILE,
                mime_type="application/pdf",
                size_bytes__gt=0,
            ).exists()
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

    def test_common_use_case_stop_policy_waits_for_expected_and_plan_execution(self):
        scenario = CommonUseCaseToolChoiceScenario()
        scenario.case = CommonUseCaseEvalDefinition.from_mapping(
            {
                "slug": "pending_execution_race",
                "category": "api_lookup",
                "prompt": "Fetch the current status and track the work.",
                "expected_tools": ["http_request"],
                "plan_expected": True,
            }
        )
        policy = scenario._build_eval_stop_policy()
        conditions = {condition["tool_name"]: condition for condition in policy["stop_when_all_seen"]}

        self.assertTrue(conditions["update_plan"]["after_execution"])
        self.assertTrue(conditions["http_request"]["after_execution"])

        plan_call = self._add_tool_call("update_plan", status="pending")
        expected_call = self._add_tool_call("http_request", status="pending")
        should_stop, _reason = should_stop_for_eval_policy(str(self.run.id), policy)
        self.assertFalse(should_stop)

        expected_call.status = "complete"
        expected_call.save(update_fields=["status"])
        should_stop, _reason = should_stop_for_eval_policy(str(self.run.id), policy)
        self.assertFalse(should_stop)

        plan_call.status = "complete"
        plan_call.save(update_fields=["status"])
        should_stop, reason = should_stop_for_eval_policy(str(self.run.id), policy)
        self.assertTrue(should_stop)
        self.assertIn("all terminal expected", reason)

    def test_common_use_case_stop_policy_waits_for_warning_repair(self):
        warning_call = self._add_tool_call("sqlite_batch", {"sql": "SELECT result_text FROM __tool_results"})
        warning_call.result = json.dumps({"status": "warning", "advisories": [{"code": "unshaped_payload"}]})
        warning_call.save(update_fields=["result"])
        policy = {
            "stop_when_all_seen": [{"tool_name": "sqlite_batch", "after_execution": True}],
            "ignore_sqlite_eval_bookkeeping_reads": False,
        }

        should_stop, _reason = should_stop_for_eval_policy(str(self.run.id), policy)
        self.assertFalse(should_stop)

        repaired_call = self._add_tool_call(
            "sqlite_batch",
            {"sql": "SELECT json_extract(result_json, '$.content') FROM __tool_results"},
        )
        repaired_call.result = json.dumps({"status": "ok", "results": []})
        repaired_call.save(update_fields=["result"])
        should_stop, reason = should_stop_for_eval_policy(str(self.run.id), policy)

        self.assertTrue(should_stop)
        self.assertIn("all terminal expected", reason)

    def test_common_use_case_stop_policy_waits_for_nested_failure_repair(self):
        policy = {
            "stop_when_all_seen": [{"tool_name": "custom_sync", "after_execution": True}],
        }

        failure_results = {
            "error_status": {"status": "error"},
            "warning_status": {"status": "warning"},
            "error_detail": {"status": "success", "error": "upstream failed"},
        }
        for failure_kind, nested_result in failure_results.items():
            with self.subTest(failure_kind=failure_kind):
                failed_call = self._add_tool_call("custom_sync")
                failed_call.result = json.dumps(
                    {"status": "ok", "result": nested_result}
                )
                failed_call.save(update_fields=["result"])

                should_stop, _reason = should_stop_for_eval_policy(str(self.run.id), policy)
                self.assertFalse(should_stop)

                repaired_call = self._add_tool_call("custom_sync")
                repaired_call.result = json.dumps(
                    {"status": "ok", "result": {"status": "success"}}
                )
                repaired_call.save(update_fields=["result"])

                should_stop, reason = should_stop_for_eval_policy(str(self.run.id), policy)
                self.assertTrue(should_stop)
                self.assertIn("all terminal expected", reason)

                PersistentAgentStep.objects.filter(eval_run=self.run).delete()

    def test_eval_stop_policy_can_wait_for_required_param_any(self):
        self._add_tool_call("custom_sync", {"mode": "status"}, status="complete")
        policy = {
            "stop_when_all_seen": [
                {
                    "tool_name": "custom_sync",
                    "after_execution": True,
                    "required_params_any": ["batch_size", "limit"],
                }
            ]
        }

        should_stop, _reason = should_stop_for_eval_policy(str(self.run.id), policy)

        self.assertFalse(should_stop)

        self._add_tool_call("custom_sync", {"mode": "sync", "batch_size": 10}, status="complete")
        should_stop, reason = should_stop_for_eval_policy(str(self.run.id), policy)

        self.assertTrue(should_stop)
        self.assertIn("all terminal expected", reason)

    def test_eval_stop_policy_ignores_skipped_expected_execution_until_repaired(self):
        policy = {
            "stop_when_all_seen": [{"tool_name": "custom_sync", "after_execution": True}],
        }
        skipped_call = self._add_tool_call("custom_sync")
        skipped_call.result = json.dumps({"status": "ok", "skipped": True})
        skipped_call.save(update_fields=["result"])

        should_stop, _reason = should_stop_for_eval_policy(str(self.run.id), policy)
        self.assertFalse(should_stop)

        repaired_call = self._add_tool_call("custom_sync")
        repaired_call.result = json.dumps({"status": "ok"})
        repaired_call.save(update_fields=["result"])

        should_stop, reason = should_stop_for_eval_policy(str(self.run.id), policy)
        self.assertTrue(should_stop)
        self.assertIn("all terminal expected", reason)

    def test_eval_stop_policy_can_wait_for_tool_execution(self):
        self._add_tool_call("custom_sync", status="pending")

        should_stop, _reason = should_stop_for_eval_policy(
            str(self.run.id),
            {"stop_on_tool_names_after_execution": ["custom_sync"]},
        )
        self.assertFalse(should_stop)

        PersistentAgentStep.objects.filter(eval_run=self.run).delete()
        self._add_tool_call("custom_sync", status="error")

        should_stop, _reason = should_stop_for_eval_policy(
            str(self.run.id),
            {"stop_on_tool_names_after_execution": ["custom_sync"]},
        )
        self.assertFalse(should_stop)

        should_stop, reason = should_stop_for_eval_policy(
            str(self.run.id),
            {"stop_on_tool_names_after_finish": ["custom_sync"]},
        )
        self.assertTrue(should_stop)
        self.assertIn("finished", reason)

        PersistentAgentStep.objects.filter(eval_run=self.run).delete()
        self._add_tool_call("custom_sync", status="complete")

        should_stop, reason = should_stop_for_eval_policy(
            str(self.run.id),
            {"stop_on_tool_names_after_execution": ["custom_sync"]},
        )

        self.assertTrue(should_stop)
        self.assertIn("completed", reason)

    def test_eval_stop_policy_ignores_skipped_terminal_message(self):
        skipped_message = self._add_tool_call(
            "send_chat_message",
            {"body": "Working...", "will_continue_work": True},
        )
        skipped_message.result = json.dumps({"status": "ok", "skipped": True, "auto_sleep_ok": False})
        skipped_message.save(update_fields=["result"])
        self._add_tool_call("http_request", {"url": "https://example.test"}, status="complete")

        should_stop, _reason = should_stop_for_eval_policy(
            str(self.run.id),
            {"stop_on_tool_names_after_finish": ["send_chat_message"]},
        )

        self.assertFalse(should_stop)

        self._add_tool_call("send_chat_message", {"body": "Done", "will_continue_work": False})
        should_stop, reason = should_stop_for_eval_policy(
            str(self.run.id),
            {"stop_on_tool_names_after_finish": ["send_chat_message"]},
        )

        self.assertTrue(should_stop)
        self.assertIn("finished", reason)

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

    def test_sqlite_config_field_detection_requires_assigned_column(self):
        charter_only = self._add_tool_call(
            "sqlite_batch",
            {
                "sql": (
                    "UPDATE __agent_config SET charter = "
                    "'Monitor support status on an hourly schedule' WHERE id = 1"
                )
            },
        )
        self.assertTrue(sqlite_batch_mutates_agent_config_field(charter_only, "charter"))
        self.assertFalse(sqlite_batch_mutates_agent_config_field(charter_only, "schedule"))

        PersistentAgentStep.objects.filter(eval_run=self.run).delete()
        schedule_insert = self._add_tool_call(
            "sqlite_batch",
            {
                "sql": (
                    "INSERT INTO __agent_config (id, charter, schedule) "
                    "VALUES (1, 'Monitor support status', '0 * * * *')"
                )
            },
        )
        self.assertTrue(sqlite_batch_mutates_agent_config_field(schedule_insert, "schedule"))

    def test_eval_stop_policy_can_wait_for_expected_charter_mutation_execution(self):
        policy = {
            "ignore_sqlite_agent_config_mutations": False,
            "stop_when_all_seen": [
                {
                    "tool_name": "sqlite_batch",
                    "agent_config_field": "charter",
                    "after_execution": True,
                }
            ],
        }
        params = {"sql": "UPDATE __agent_config SET charter = 'Monitor competitors' WHERE id = 1"}
        call = self._add_tool_call("sqlite_batch", params, status="pending")

        self.assertTrue(sqlite_batch_mutates_agent_config_field(call, "charter"))

        should_stop, _reason = should_stop_for_eval_policy(str(self.run.id), policy)

        self.assertFalse(should_stop)

        PersistentAgentStep.objects.filter(eval_run=self.run).delete()
        self._add_tool_call("sqlite_batch", params, status="complete")

        should_stop, reason = should_stop_for_eval_policy(str(self.run.id), policy)

        self.assertTrue(should_stop)
        self.assertIn("all terminal expected", reason)

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

    def test_successful_end_planning_helpers_skip_failed_attempt(self):
        failed_end = self._add_tool_call("end_planning", status="error")
        forbidden = self._add_tool_call("http_request")
        delivered = self._add_tool_call("send_chat_message", {"body": "Answer before the valid gate."})
        successful_end = self._add_tool_call("end_planning", status="complete")

        self.assertIsNot(failed_end, successful_end)
        self.assertEqual(
            get_first_successful_tool_call(self.run.id, tool_names={"end_planning"}),
            successful_end,
        )
        self.assertEqual(
            get_forbidden_calls_before_end_planning(
                self.run.id,
                forbidden_tool_names={"http_request"},
            ),
            [forbidden],
        )

        scenario = PlanningOneOffResearchReportEndsPlanningFirstScenario()
        self.assertEqual(
            scenario._delivered_message_calls_before_end_planning(self.run.id, after=None),
            [delivered],
        )
        self.assertEqual(
            scenario._first_end_planning_call(self.run.id, after=None),
            successful_end,
        )

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

    def test_agent_config_mutation_detection_tracks_direct_and_sqlite_tools(self):
        self._add_tool_call("sqlite_batch", {"sql": "SELECT * FROM __agent_config"})
        sqlite_mutation = self._add_tool_call(
            "sqlite_batch",
            {"sql": "UPDATE __agent_config SET charter='Monitor competitors'"},
        )
        charter_mutation = self._add_tool_call("update_charter", {"charter": "Monitor competitors"})
        schedule_mutation = self._add_tool_call("update_schedule", {"schedule": "0 9 * * *"})
        self._add_tool_call("update_plan", {"plan": [{"step": "x", "status": "todo"}]})
        self._add_tool_call("http_request")

        calls = get_agent_config_mutation_calls_for_run(self.run.id)

        self.assertEqual(calls, [sqlite_mutation, charter_mutation, schedule_mutation])

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

    def test_tracked_request_shapes_allow_natural_free_text_and_validate_supplied_options(self):
        free_text = SimpleNamespace(question="Which competitors should I monitor?", options_json=[])
        with_options = SimpleNamespace(
            question="Which cadence should I use?",
            options_json=[{"key": "weekly", "title": "Weekly", "description": "Send one weekly digest."}],
        )
        invalid_options = SimpleNamespace(
            question="Which cadence should I use?",
            options_json=[{"key": "weekly", "title": "Weekly"}],
        )

        self.assertTrue(requests_have_valid_shapes([free_text, with_options]))
        self.assertFalse(requests_have_valid_shapes([]))
        self.assertFalse(requests_have_valid_shapes([invalid_options]))

    def test_charter_email_prohibition_check_preserves_polarity(self):
        self.assertTrue(charter_preserves_email_prohibition("Send updates in Slack; do not send email."))
        self.assertTrue(charter_preserves_email_prohibition("Use Teams, never email."))
        self.assertFalse(charter_preserves_email_prohibition("Send concise Slack and email updates."))
        self.assertFalse(charter_preserves_email_prohibition("Email updates are preferred."))

    def test_request_human_input_eval_tool_check_accepts_valid_options_or_free_text(self):
        valid_single = SimpleNamespace(
            tool_params={"options": [{"title": "Yes", "description": "Proceed with yes."}]}
        )
        free_text_single = SimpleNamespace(
            tool_params={"question": "What should I tell the team?"}
        )
        invalid_single = SimpleNamespace(
            tool_params={"options": [{"title": "Yes"}]}
        )
        valid_batch = SimpleNamespace(
            tool_params={
                "requests": [
                    {
                        "question": "Proceed?",
                        "options": [{"title": "Yes", "description": "Proceed with yes."}],
                    },
                    {
                        "question": "What context should I use?",
                    },
                ]
            }
        )
        invalid_batch = SimpleNamespace(
            tool_params={
                "requests": [
                    {
                        "question": "Proceed?",
                        "options": [{"title": "", "description": "Proceed with yes."}],
                    }
                ]
            }
        )

        self.assertTrue(CommonUseCaseToolChoiceScenario._request_human_input_call_has_options(valid_single))
        self.assertTrue(CommonUseCaseToolChoiceScenario._request_human_input_call_has_options(free_text_single))
        self.assertFalse(CommonUseCaseToolChoiceScenario._request_human_input_call_has_options(invalid_single))
        self.assertTrue(CommonUseCaseToolChoiceScenario._request_human_input_call_has_options(valid_batch))
        self.assertFalse(CommonUseCaseToolChoiceScenario._request_human_input_call_has_options(invalid_batch))

    def test_monitoring_scope_eval_accepts_equivalent_blocking_chat_questions(self):
        scenario = ScenarioRegistry.get("common_use_case_099_request_monitoring_scope")
        good_chat = SimpleNamespace(
            tool_name="send_chat_message",
            status="complete",
            result=json.dumps({"status": "sent"}),
            tool_params={
                "body": (
                    "Which competitors should I monitor, and what types of updates or changes matter to you?"
                )
            },
        )
        vague_chat = SimpleNamespace(
            tool_name="send_chat_message",
            status="complete",
            result=json.dumps({"status": "sent"}),
            tool_params={"body": "What should I do?"},
        )

        self.assertIn(
            "send_chat_message",
            scenario.case.accepted_tool_names_for_expected_tool("request_human_input"),
        )
        self.assertTrue(scenario._call_satisfies_expected_tool(good_chat, "request_human_input"))
        self.assertFalse(scenario._call_satisfies_expected_tool(vague_chat, "request_human_input"))

    def test_common_use_case_expected_tool_rejects_incomplete_or_failed_calls(self):
        scenario = ScenarioRegistry.get("common_use_case_001_fetch_inventory_json")
        base = {
            "tool_name": "http_request",
            "tool_params": {"url": "https://api.example.test/inventory/widget-123.json"},
        }

        for status, result in (
            ("pending", {"status": "ok"}),
            ("error", {"status": "ok"}),
            ("complete", {"status": "error"}),
            ("complete", {"status": "warning"}),
            ("complete", {"status": "ok", "error": "upstream failed"}),
        ):
            with self.subTest(status=status, result=result):
                call = SimpleNamespace(**base, status=status, result=json.dumps(result))
                self.assertFalse(scenario._call_satisfies_expected_tool(call, "http_request"))

        successful = SimpleNamespace(
            **base,
            status="complete",
            result=json.dumps({"status": "ok"}),
        )
        self.assertTrue(scenario._call_satisfies_expected_tool(successful, "http_request"))

    def test_missing_recipient_eval_accepts_bounded_blocking_chat(self):
        scenario = ScenarioRegistry.get(TOOL_CHOICE_MISSING_RECIPIENT_USES_HUMAN_INPUT)

        self.assertTrue(
            scenario._blocking_chat_requests_missing_email_details(
                "Who is the client and what email address should I use? "
                "Where can I find the latest project status?"
            )
        )
        self.assertFalse(
            scenario._blocking_chat_requests_missing_email_details(
                "Could you share more information?"
            )
        )
