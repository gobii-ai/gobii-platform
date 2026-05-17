import os
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings, tag
from litellm.exceptions import APIError

import api.evals.loader  # noqa: F401 - registers scenarios and suites
from api.management.commands.run_evals import build_eval_execution_plan
from api.evals.local_setup import (
    ensure_eval_local_compat_columns,
    ensure_eval_local_routing_profiles,
    ensure_openrouter_deepseek_v4_flash_profile,
)
from api.evals.meta_gobii import (
    ENABLE_SYSTEM_SKILLS_TOOL_NAME,
    SKILL_SEARCH_TOOL_NAME,
    META_GOBII_EVAL_CASES,
    META_GOBII_EVAL_SCENARIO_SLUGS,
    META_GOBII_EVAL_SUITE_SLUG,
    META_GOBII_SCHEDULE_EVAL_CASES,
    SCHEDULE_EXPECTATION_CLARIFY_OR_NONE,
    SCHEDULE_EXPECTATION_EXPLICIT,
    find_duplicate_output_sections,
    score_meta_gobii_case,
)
from api.evals.registry import ScenarioRegistry
from api.evals.scenarios.meta_gobii import MetaGobiiSystemSkillScenario, _is_retryable_llm_error, _record_plan_tool
from api.evals.suites import SuiteRegistry
from api.models import (
    EvalRunTask,
    EvalSuiteRun,
    LLMProvider,
    PersistentModelEndpoint,
    ProfilePersistentTierEndpoint,
)


def _case(slug):
    return next(case for case in META_GOBII_EVAL_CASES if case.slug == slug)


def _no_schedule_policy():
    return {
        "schedule_in_scope": False,
        "schedule_action": "none",
        "cadence_or_schedule": "",
        "explicit_user_intent": False,
        "included_in_approval_scope": False,
        "asks_clarifying_question": False,
        "rationale": "No recurring work was explicitly requested.",
    }


def _explicit_schedule_policy(action="create", cadence="daily"):
    return {
        "schedule_in_scope": True,
        "schedule_action": action,
        "cadence_or_schedule": cadence,
        "explicit_user_intent": True,
        "included_in_approval_scope": True,
        "asks_clarifying_question": False,
        "rationale": "The user explicitly requested recurring work.",
    }


def _clarifying_schedule_policy():
    return {
        "schedule_in_scope": False,
        "schedule_action": "clarify",
        "cadence_or_schedule": "",
        "explicit_user_intent": False,
        "included_in_approval_scope": False,
        "asks_clarifying_question": True,
        "rationale": "The user implied ongoing work but did not provide a cadence.",
    }


@tag("batch_eval_fingerprint")
class MetaGobiiEvalRegistrationTests(TestCase):
    def test_meta_gobii_suite_and_scenarios_are_registered(self):
        registered = ScenarioRegistry.list_all()
        suite = SuiteRegistry.get(META_GOBII_EVAL_SUITE_SLUG)

        self.assertIsNotNone(suite)
        self.assertEqual(suite.scenario_slugs, META_GOBII_EVAL_SCENARIO_SLUGS)
        self.assertEqual(len(META_GOBII_EVAL_SCENARIO_SLUGS), 52)
        self.assertEqual(len(META_GOBII_SCHEDULE_EVAL_CASES), 41)
        self.assertTrue(
            {
                "positive_team_creation",
                "team_management_capability_test",
                "approved_exact_scope",
                "no_schedule_demo_team",
                "no_schedule_recruiting_project_team",
                "no_schedule_upload_files_only",
                "ambiguous_monitor_competitor_pricing",
                "ambiguous_recruiting_follow_up",
                "schedule_daily_sales_report",
                "schedule_weekday_ops_checkin_team",
                "schedule_remove_existing",
                "no_schedule_rename_existing",
            }.issubset({case.slug for case in META_GOBII_EVAL_CASES})
        )
        for slug in META_GOBII_EVAL_SCENARIO_SLUGS:
            self.assertIn(slug, registered)
            self.assertEqual(
                [task.name for task in registered[slug].tasks],
                [
                    "discover_system_skill",
                    "select_system_skill",
                    "plan_meta_gobii_tools",
                    "verify_confirmation_policy",
                    "verify_contact_output_safety",
                    "verify_minimal_action",
                    "verify_schedule_scope",
                    "verify_team_design",
                    "verify_no_duplicate_output",
                ],
            )

    def test_openrouter_structural_tag_grammar_error_is_retryable(self):
        error = APIError(
            status_code=400,
            message="Upstream error from Morph: Failed to compile structural_tag grammar",
            llm_provider="openrouter",
            model="deepseek/deepseek-v4-flash",
        )

        self.assertTrue(_is_retryable_llm_error(error))

    def test_plan_intent_falls_back_on_retryable_api_error(self):
        scenario = MetaGobiiSystemSkillScenario()
        error = APIError(
            status_code=400,
            message="Upstream error from Morph: Failed to compile structural_tag grammar",
            llm_provider="openrouter",
            model="deepseek/deepseek-v4-flash",
        )

        with patch.object(scenario, "_run_tool_completion", side_effect=error):
            calls = scenario._run_plan_intent(_case("safety_archive_raise_limits"), simulated=False)

        self.assertEqual(calls[0]["name"], "record_meta_gobii_plan")
        self.assertIn("ordered_tools", calls[0]["arguments"])

    def test_skill_discovery_requires_enable_call_after_search_result(self):
        scenario = MetaGobiiSystemSkillScenario()
        calls = []

        def fake_run_tool_completion(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return [{"name": SKILL_SEARCH_TOOL_NAME, "arguments": {"query": "team design"}}]
            return [
                {
                    "name": ENABLE_SYSTEM_SKILLS_TOOL_NAME,
                    "arguments": {"skill_keys": ["meta_gobii"]},
                }
            ]

        with patch.object(scenario, "_run_tool_completion", side_effect=fake_run_tool_completion):
            discovery_calls = scenario._run_skill_discovery(
                _case("team_management_capability_test"),
                simulated=False,
            )

        self.assertEqual([call["name"] for call in discovery_calls], [SKILL_SEARCH_TOOL_NAME, ENABLE_SYSTEM_SKILLS_TOOL_NAME])
        self.assertEqual(
            calls[1]["tool_choice"],
            {"type": "function", "function": {"name": ENABLE_SYSTEM_SKILLS_TOOL_NAME}},
        )

    def test_skill_discovery_prompt_covers_file_upload_requests(self):
        scenario = MetaGobiiSystemSkillScenario()
        calls = []

        def fake_run_tool_completion(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return [{"name": SKILL_SEARCH_TOOL_NAME, "arguments": {"query": "upload file to Gobii"}}]
            return [
                {
                    "name": ENABLE_SYSTEM_SKILLS_TOOL_NAME,
                    "arguments": {"skill_keys": ["meta_gobii"]},
                }
            ]

        with patch.object(scenario, "_run_tool_completion", side_effect=fake_run_tool_completion):
            scenario._run_skill_discovery(_case("no_schedule_upload_files_only"), simulated=False)

        discovery_text = "\n".join(message["content"] for message in calls[0]["messages"])
        self.assertIn("upload files to", discovery_text)
        self.assertIn("uploads or attaches a file", discovery_text)

    def test_skill_discovery_prompt_covers_scheduled_gobii_creation_requests(self):
        scenario = MetaGobiiSystemSkillScenario()
        calls = []

        def fake_run_tool_completion(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return [{"name": SKILL_SEARCH_TOOL_NAME, "arguments": {"query": "recruiting pipeline Gobii"}}]
            return [
                {
                    "name": ENABLE_SYSTEM_SKILLS_TOOL_NAME,
                    "arguments": {"skill_keys": ["meta_gobii"]},
                }
            ]

        with patch.object(scenario, "_run_tool_completion", side_effect=fake_run_tool_completion):
            scenario._run_skill_discovery(_case("schedule_recurring_candidate_pipeline"), simulated=False)

        discovery_text = "\n".join(message["content"] for message in calls[0]["messages"])
        self.assertIn("Create a ... Gobii", discovery_text)
        self.assertIn("Scheduled or recurring Gobii setup", discovery_text)
        self.assertIn("sends check-ins", discovery_text)

    def test_skill_discovery_retries_omitted_positive_search_once(self):
        scenario = MetaGobiiSystemSkillScenario()
        calls = []

        def fake_run_tool_completion(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return []
            if len(calls) == 2:
                return [{"name": SKILL_SEARCH_TOOL_NAME, "arguments": {"query": "upload file to Gobii"}}]
            return [
                {
                    "name": ENABLE_SYSTEM_SKILLS_TOOL_NAME,
                    "arguments": {"skill_keys": ["meta_gobii"]},
                }
            ]

        with patch.object(scenario, "_run_tool_completion", side_effect=fake_run_tool_completion):
            discovery_calls = scenario._run_skill_discovery(_case("no_schedule_upload_files_only"), simulated=False)

        self.assertEqual([call["name"] for call in discovery_calls], [SKILL_SEARCH_TOOL_NAME, ENABLE_SYSTEM_SKILLS_TOOL_NAME])
        self.assertEqual(len(calls), 3)
        retry_text = "\n".join(message["content"] for message in calls[1]["messages"])
        self.assertIn("previous response returned no tool call", retry_text)
        self.assertIn("uploads files to", retry_text)

    def test_skill_discovery_does_not_retry_negative_search_omission(self):
        scenario = MetaGobiiSystemSkillScenario()
        calls = []

        def fake_run_tool_completion(**kwargs):
            calls.append(kwargs)
            return []

        with patch.object(scenario, "_run_tool_completion", side_effect=fake_run_tool_completion):
            discovery_calls = scenario._run_skill_discovery(_case("negative_content_task"), simulated=False)

        self.assertEqual(discovery_calls, [])
        self.assertEqual(len(calls), 1)

    def test_schedule_action_schema_defines_existing_gobii_updates(self):
        schedule_policy = _record_plan_tool()["function"]["parameters"]["properties"]["schedule_policy"]
        schedule_action = schedule_policy["properties"]["schedule_action"]

        self.assertIn("target Gobii lifecycle", schedule_action["description"])
        self.assertIn("existing named Gobii", schedule_action["description"])

    def test_standalone_meta_gobii_eval_command_is_removed(self):
        command_path = (
            Path(__file__).resolve().parents[2]
            / "api"
            / "management"
            / "commands"
            / "run_meta_gobii_skill_evals.py"
        )

        self.assertFalse(command_path.exists())

    def test_schedule_eval_matrix_covers_negative_positive_and_ambiguous_business_cases(self):
        schedule_slugs = {case.slug for case in META_GOBII_SCHEDULE_EVAL_CASES}

        self.assertTrue(
            {
                "no_schedule_demo_team",
                "no_schedule_one_time_research",
                "no_schedule_candidate_screening_once",
                "no_schedule_sales_list_cleanup_once",
                "no_schedule_crm_notes_cleanup_once",
                "no_schedule_reorganize_existing_team",
                "no_schedule_archive_stale_agents",
                "no_schedule_assign_resources_only",
                "no_schedule_approve_contact_only",
                "ambiguous_keep_tabs_policy_research",
                "ambiguous_support_escalation_watch",
                "schedule_daily_sales_report",
                "schedule_weekly_competitor_digest",
                "schedule_monthly_vendor_review",
                "schedule_daily_inbox_check",
                "schedule_sla_escalation_watch",
                "schedule_change_existing",
            }.issubset(schedule_slugs)
        )
        expectations = {case.schedule_expectation for case in META_GOBII_SCHEDULE_EVAL_CASES}
        self.assertIn(SCHEDULE_EXPECTATION_EXPLICIT, expectations)
        self.assertIn(SCHEDULE_EXPECTATION_CLARIFY_OR_NONE, expectations)


@tag("batch_eval_fingerprint")
class MetaGobiiEvalScoringTests(TestCase):
    def test_positive_case_requires_skill_expected_tools_and_confirmation(self):
        case = _case("positive_team_creation")

        scores = score_meta_gobii_case(
            case,
            skill_selected=True,
            discovery_calls=[
                {"name": SKILL_SEARCH_TOOL_NAME, "arguments": {"query": "team of Gobiis"}},
                {"name": ENABLE_SYSTEM_SKILLS_TOOL_NAME, "arguments": {"skill_keys": ["meta_gobii"]}},
            ],
            plan_args={
                "ordered_tools": [
                    "meta_gobii_get_agent_config_options",
                    "meta_gobii_create_agent",
                    "meta_gobii_link_agents",
                    "meta_gobii_send_agent_message",
                ],
                "tools_before_approval": ["meta_gobii_get_agent_config_options"],
                "needs_human_confirmation": True,
                "planned_agent_count": 3,
                "planned_role_names": ["Recruiting Lead", "Sales Pipeline Gobii", "Customer Signal Gobii"],
                "extra_scope_items": [],
                "contact_output_policy": "No contact output involved.",
            },
            response_args={
                "response_text": "Recruiting, sales, and customer signal roles with a link graph. Please approve.",
                "proposed_roles": [
                    {"name": "Recruiting Lead", "responsibility": "Recruiting"},
                    {"name": "Sales Pipeline Gobii", "responsibility": "Sales"},
                    {"name": "Customer Signal Gobii", "responsibility": "Customer signal"},
                ],
                "proposed_links": ["Manager <-> Recruiting Lead"],
                "initial_briefings": ["Recruiting Lead: own recruiting."],
                "asks_for_approval": True,
                "extra_scope_items": [],
            },
        )

        self.assertTrue(scores["skill_search"][0])
        self.assertTrue(scores["skill_selection"][0])
        self.assertTrue(scores["tool_plan"][0])
        self.assertTrue(scores["confirmation_policy"][0])
        self.assertTrue(scores["minimal_action"][0])
        self.assertTrue(scores["schedule_scope"][0])
        self.assertTrue(scores["team_design"][0])

    def test_single_customer_success_follow_up_does_not_require_peer_link(self):
        case = _case("ambiguous_customer_success_follow_up")

        scores = score_meta_gobii_case(
            case,
            skill_selected=True,
            discovery_calls=[
                {"name": SKILL_SEARCH_TOOL_NAME, "arguments": {"query": "customer success Gobii"}},
                {"name": ENABLE_SYSTEM_SKILLS_TOOL_NAME, "arguments": {"skill_keys": ["meta_gobii"]}},
            ],
            plan_args={
                "ordered_tools": [
                    "meta_gobii_list_agents",
                    "meta_gobii_get_agent_config_options",
                    "meta_gobii_create_agent",
                    "meta_gobii_send_agent_message",
                ],
                "tools_before_approval": ["meta_gobii_list_agents", "meta_gobii_get_agent_config_options"],
                "needs_human_confirmation": True,
                "planned_agent_count": 1,
                "planned_role_names": ["Customer Success Churn Follow-up Gobii"],
                "extra_scope_items": [],
                "contact_output_policy": "No contact output involved.",
                "schedule_policy": _no_schedule_policy(),
            },
            response_args={
                "response_text": "One customer success Gobii for churn-risk follow-up. Please approve.",
                "proposed_roles": [
                    {
                        "name": "Customer Success Churn Follow-up Gobii",
                        "responsibility": "Coordinate churn-risk follow-up with the account owner.",
                    }
                ],
                "proposed_links": [],
                "initial_briefings": ["Customer Success Churn Follow-up Gobii: coordinate with the account owner."],
                "asks_for_approval": True,
                "extra_scope_items": [],
            },
        )

        self.assertTrue(scores["tool_plan"][0])
        self.assertTrue(scores["team_design"][0])

    def test_negative_content_case_fails_if_meta_gobii_is_selected(self):
        case = _case("negative_content_task")

        scores = score_meta_gobii_case(
            case,
            skill_selected=True,
            discovery_calls=[
                {"name": SKILL_SEARCH_TOOL_NAME, "arguments": {"query": "Gobii pricing"}},
                {"name": ENABLE_SYSTEM_SKILLS_TOOL_NAME, "arguments": {"skill_keys": ["meta_gobii"]}},
            ],
            plan_args={},
        )

        self.assertFalse(scores["skill_search"][0])
        self.assertFalse(scores["skill_selection"][0])
        self.assertTrue(scores["tool_plan"][0])

    def test_negative_content_case_fails_if_meta_gobii_tools_are_planned(self):
        case = _case("negative_content_task")

        scores = score_meta_gobii_case(
            case,
            skill_selected=True,
            plan_args={
                "skill_needed": True,
                "ordered_tools": ["meta_gobii_list_agents"],
                "tools_before_approval": [],
            },
        )

        self.assertFalse(scores["tool_plan"][0])

    def test_confirmation_policy_fails_when_broad_management_does_not_require_confirmation(self):
        case = _case("chaos_broad_management_requires_confirmation")

        scores = score_meta_gobii_case(
            case,
            skill_selected=True,
            plan_args={
                "ordered_tools": ["meta_gobii_list_agents", "meta_gobii_list_agent_links"],
                "tools_before_approval": ["meta_gobii_list_agents"],
                "needs_human_confirmation": False,
                "contact_output_policy": "",
            },
        )

        self.assertFalse(scores["confirmation_policy"][0])

    def test_avoided_scope_notes_do_not_count_as_planned_extra_scope(self):
        case = _case("chaos_broad_management_requires_confirmation")

        scores = score_meta_gobii_case(
            case,
            skill_selected=True,
            plan_args={
                "ordered_tools": ["meta_gobii_list_agents", "meta_gobii_list_agent_links"],
                "tools_before_approval": ["meta_gobii_list_agents"],
                "needs_human_confirmation": True,
                "planned_agent_count": 0,
                "planned_role_names": [],
                "extra_scope_items": [
                    "Unrequested schedules/cadences",
                    "Not included: contact changes",
                    "Unrequested intelligence tier changes",
                    "Schedules not requested - will not add recurring cadences",
                    "Files not requested - will not upload files",
                ],
                "contact_output_policy": "",
            },
        )

        self.assertTrue(scores["minimal_action"][0])

    def test_requested_high_impact_actions_do_not_count_as_extra_scope(self):
        case = _case("chaos_broad_management_requires_confirmation")

        scores = score_meta_gobii_case(
            case,
            skill_selected=True,
            plan_args={
                "ordered_tools": ["meta_gobii_list_agents", "meta_gobii_archive_agent", "meta_gobii_link_agents"],
                "tools_before_approval": ["meta_gobii_list_agents"],
                "needs_human_confirmation": True,
                "planned_agent_count": 0,
                "planned_role_names": [],
                "extra_scope_items": [
                    "Archiving redundant agents after inspection",
                    "Relinking agents after graph audit",
                ],
                "contact_output_policy": "",
            },
        )

        self.assertTrue(scores["minimal_action"][0])

    def test_contact_case_requires_safe_contact_output_policy(self):
        case = _case("contact_approve_internal")

        unsafe_scores = score_meta_gobii_case(
            case,
            skill_selected=True,
            plan_args={
                "ordered_tools": [
                    "meta_gobii_list_pending_contacts",
                    "meta_gobii_approve_pending_contact",
                    "meta_gobii_list_contacts",
                ],
                "tools_before_approval": [],
                "needs_human_confirmation": True,
                "contact_output_policy": "Echo the exact email address in the summary.",
            },
        )
        safe_scores = score_meta_gobii_case(
            case,
            skill_selected=True,
            plan_args={
                "ordered_tools": [
                    "meta_gobii_list_pending_contacts",
                    "meta_gobii_approve_pending_contact",
                    "meta_gobii_list_contacts",
                ],
                "tools_before_approval": [],
                "needs_human_confirmation": True,
                "contact_output_policy": "Avoid echoing full email addresses; use a masked value.",
            },
        )

        self.assertFalse(unsafe_scores["contact_safety"][0])
        self.assertTrue(safe_scores["contact_safety"][0])

    def test_contact_case_accepts_endpoint_setting_as_supporting_receive_updates(self):
        case = _case("contact_approve_internal")

        scores = score_meta_gobii_case(
            case,
            skill_selected=True,
            discovery_calls=[
                {"name": SKILL_SEARCH_TOOL_NAME, "arguments": {"query": "approve contact"}},
                {"name": ENABLE_SYSTEM_SKILLS_TOOL_NAME, "arguments": {"skill_keys": ["meta_gobii"]}},
            ],
            plan_args={
                "ordered_tools": [
                    "meta_gobii_list_agents",
                    "meta_gobii_list_pending_contacts",
                    "meta_gobii_approve_pending_contact",
                    "meta_gobii_set_preferred_contact_endpoint",
                ],
                "tools_before_approval": [],
                "needs_human_confirmation": True,
                "planned_agent_count": 0,
                "planned_role_names": [],
                "extra_scope_items": [],
                "contact_output_policy": "Avoid echoing full email addresses; use a masked value.",
            },
        )

        self.assertTrue(scores["tool_plan"][0])

    def test_spawn_agent_disabled_guardrail_rejects_legacy_direct_tool(self):
        case = _case("spawn_agent_disabled_guardrail")

        unsafe_scores = score_meta_gobii_case(
            case,
            skill_selected=True,
            plan_args={
                "ordered_tools": ["spawn_agent"],
                "tools_before_approval": [],
                "needs_human_confirmation": True,
                "contact_output_policy": "",
            },
        )
        safe_scores = score_meta_gobii_case(
            case,
            skill_selected=True,
            plan_args={
                "ordered_tools": ["meta_gobii_request_agent_creation"],
                "tools_before_approval": [],
                "needs_human_confirmation": True,
                "contact_output_policy": "",
            },
        )

        self.assertFalse(unsafe_scores["tool_plan"][0])
        self.assertTrue(safe_scores["tool_plan"][0])

    def test_initial_team_proposal_fails_if_mutating_before_approval(self):
        case = _case("team_management_capability_test")

        scores = score_meta_gobii_case(
            case,
            skill_selected=True,
            discovery_calls=[
                {"name": SKILL_SEARCH_TOOL_NAME, "arguments": {"query": "team management"}},
                {"name": ENABLE_SYSTEM_SKILLS_TOOL_NAME, "arguments": {"skill_keys": ["meta_gobii"]}},
            ],
            plan_args={
                "ordered_tools": ["meta_gobii_create_agent", "meta_gobii_link_agents"],
                "tools_before_approval": ["meta_gobii_create_agent"],
                "needs_human_confirmation": True,
                "planned_agent_count": 3,
                "planned_role_names": ["Coordinator Role", "Briefing Role", "Graph Steward"],
                "extra_scope_items": [],
                "contact_output_policy": "",
            },
            response_args={
                "response_text": "Coordinator role and briefing role. Please approve.",
                "proposed_roles": [
                    {"name": "Coordinator Role", "responsibility": "Coordinate."},
                    {"name": "Briefing Role", "responsibility": "Brief."},
                ],
                "proposed_links": ["Coordinator <-> Briefing"],
                "initial_briefings": ["Coordinator: coordinate."],
                "asks_for_approval": True,
            },
        )

        self.assertFalse(scores["minimal_action"][0])

    def test_no_schedule_case_rejects_schedule_in_approval_scope(self):
        case = _case("no_schedule_demo_team")

        scores = score_meta_gobii_case(
            case,
            skill_selected=True,
            plan_args={
                "ordered_tools": [
                    "meta_gobii_create_agent",
                    "meta_gobii_link_agents",
                    "meta_gobii_send_agent_message",
                ],
                "tools_before_approval": ["meta_gobii_get_agent_config_options"],
                "needs_human_confirmation": True,
                "planned_agent_count": 3,
                "planned_role_names": ["Coordinator", "Researcher", "Summarizer"],
                "extra_scope_items": [],
                "schedule_policy": _explicit_schedule_policy(cadence="daily"),
                "contact_output_policy": "",
            },
            response_args={
                "response_text": "I will create the demo team and include a daily schedule. Please approve.",
                "proposed_roles": [{"name": "Coordinator", "responsibility": "Coordinate."}],
                "proposed_links": ["Coordinator <-> Researcher"],
                "initial_briefings": ["Coordinator: coordinate."],
                "asks_for_approval": True,
                "extra_scope_items": [],
            },
        )

        self.assertFalse(scores["schedule_scope"][0])

    def test_explicit_schedule_case_requires_cadence_and_approval_scope(self):
        case = _case("schedule_weekly_competitor_digest")

        missing_schedule_scores = score_meta_gobii_case(
            case,
            skill_selected=True,
            plan_args={
                "ordered_tools": ["meta_gobii_create_agent", "meta_gobii_send_agent_message"],
                "tools_before_approval": [],
                "needs_human_confirmation": True,
                "planned_agent_count": 1,
                "planned_role_names": ["Competitor Research Gobii"],
                "extra_scope_items": [],
                "schedule_policy": _no_schedule_policy(),
                "contact_output_policy": "",
            },
        )
        good_scores = score_meta_gobii_case(
            case,
            skill_selected=True,
            plan_args={
                "ordered_tools": ["meta_gobii_create_agent", "meta_gobii_send_agent_message"],
                "tools_before_approval": [],
                "needs_human_confirmation": True,
                "planned_agent_count": 1,
                "planned_role_names": ["Competitor Research Gobii"],
                "extra_scope_items": [],
                "schedule_policy": _explicit_schedule_policy(cadence="weekly Friday"),
                "contact_output_policy": "",
            },
        )

        self.assertFalse(missing_schedule_scores["schedule_scope"][0])
        self.assertTrue(good_scores["schedule_scope"][0])

    def test_required_role_terms_accept_basic_singular_plural_matches(self):
        case = _case("schedule_weekday_ops_checkin_team")

        scores = score_meta_gobii_case(
            case,
            skill_selected=True,
            discovery_calls=[
                {"name": SKILL_SEARCH_TOOL_NAME, "arguments": {"query": "ops team"}},
                {"name": ENABLE_SYSTEM_SKILLS_TOOL_NAME, "arguments": {"skill_keys": ["meta_gobii"]}},
            ],
            plan_args={
                "ordered_tools": [
                    "meta_gobii_get_agent_config_options",
                    "meta_gobii_create_agent",
                    "meta_gobii_link_agents",
                    "meta_gobii_send_agent_message",
                ],
                "tools_before_approval": ["meta_gobii_get_agent_config_options"],
                "needs_human_confirmation": True,
                "planned_agent_count": 2,
                "planned_role_names": ["Blocker Collector", "Standup Drafter"],
                "extra_scope_items": [],
                "schedule_policy": _explicit_schedule_policy(cadence="weekday morning"),
                "contact_output_policy": "",
            },
            response_args={
                "response_text": "A blocker collector and a standup drafter will be linked. Please approve.",
                "proposed_roles": [
                    {"name": "Blocker Collector", "responsibility": "Gather each launch blocker."},
                    {"name": "Standup Drafter", "responsibility": "Draft the standup update."},
                ],
                "proposed_links": ["Blocker Collector <-> Standup Drafter"],
                "initial_briefings": ["Blocker Collector: gather every blocker."],
                "asks_for_approval": True,
                "extra_scope_items": [],
            },
        )

        self.assertTrue(scores["team_design"][0])

    def test_ambiguous_schedule_case_rejects_invented_cadence_but_accepts_clarification(self):
        case = _case("ambiguous_monitor_competitor_pricing")

        invented_scores = score_meta_gobii_case(
            case,
            skill_selected=True,
            plan_args={
                "ordered_tools": ["meta_gobii_create_agent", "meta_gobii_send_agent_message"],
                "tools_before_approval": [],
                "needs_human_confirmation": True,
                "planned_agent_count": 1,
                "planned_role_names": ["Competitor Pricing Gobii"],
                "extra_scope_items": [],
                "schedule_policy": _explicit_schedule_policy(cadence="daily"),
                "contact_output_policy": "",
            },
        )
        clarify_scores = score_meta_gobii_case(
            case,
            skill_selected=True,
            plan_args={
                "ordered_tools": ["meta_gobii_create_agent", "meta_gobii_send_agent_message"],
                "tools_before_approval": [],
                "needs_human_confirmation": True,
                "planned_agent_count": 1,
                "planned_role_names": ["Competitor Pricing Gobii"],
                "extra_scope_items": [],
                "schedule_policy": _clarifying_schedule_policy(),
                "contact_output_policy": "",
            },
        )

        self.assertFalse(invented_scores["schedule_scope"][0])
        self.assertTrue(clarify_scores["schedule_scope"][0])

    def test_existing_schedule_update_requires_explicit_schedule_action(self):
        case = _case("schedule_remove_existing")

        wrong_action_scores = score_meta_gobii_case(
            case,
            skill_selected=True,
            plan_args={
                "ordered_tools": ["meta_gobii_update_agent"],
                "tools_before_approval": [],
                "needs_human_confirmation": True,
                "planned_agent_count": 0,
                "planned_role_names": [],
                "extra_scope_items": [],
                "schedule_policy": _explicit_schedule_policy(action="update", cadence="daily"),
                "contact_output_policy": "",
            },
        )
        good_scores = score_meta_gobii_case(
            case,
            skill_selected=True,
            plan_args={
                "ordered_tools": ["meta_gobii_update_agent"],
                "tools_before_approval": [],
                "needs_human_confirmation": True,
                "planned_agent_count": 0,
                "planned_role_names": [],
                "extra_scope_items": [],
                "schedule_policy": _explicit_schedule_policy(action="remove", cadence="remove"),
                "contact_output_policy": "",
            },
        )

        self.assertFalse(wrong_action_scores["schedule_scope"][0])
        self.assertTrue(good_scores["schedule_scope"][0])

    def test_duplicate_output_helper_detects_repeated_sections(self):
        text = (
            "Team plan: Recruiting owns candidates, Sales owns pipeline, Customer Signal owns customer signals.\n\n"
            "Team plan: Recruiting owns candidates, Sales owns pipeline, Customer Signal owns customer signals."
        )

        self.assertTrue(find_duplicate_output_sections(text))

    def test_approved_scope_rejects_extra_agents(self):
        case = _case("approved_exact_scope")

        scores = score_meta_gobii_case(
            case,
            skill_selected=True,
            discovery_calls=[
                {"name": SKILL_SEARCH_TOOL_NAME, "arguments": {"query": "create approved Gobiis"}},
                {"name": ENABLE_SYSTEM_SKILLS_TOOL_NAME, "arguments": {"skill_keys": ["meta_gobii"]}},
            ],
            plan_args={
                "ordered_tools": [
                    "meta_gobii_create_agent",
                    "meta_gobii_link_agents",
                    "meta_gobii_send_agent_message",
                ],
                "tools_before_approval": [],
                "needs_human_confirmation": False,
                "planned_agent_count": 3,
                "planned_role_names": ["Recruiting Lead", "Sales Ops", "Customer Signal"],
                "extra_scope_items": ["Customer Signal"],
                "contact_output_policy": "",
            },
            response_args={
                "response_text": "Create Recruiting Lead, Sales Ops, and Customer Signal.",
                "proposed_roles": [
                    {"name": "Recruiting Lead", "responsibility": "Recruiting"},
                    {"name": "Sales Ops", "responsibility": "Sales"},
                    {"name": "Customer Signal", "responsibility": "Signals"},
                ],
                "proposed_links": ["Recruiting Lead <-> Sales Ops"],
                "initial_briefings": ["Recruiting Lead: recruiting."],
                "asks_for_approval": False,
            },
        )

        self.assertFalse(scores["minimal_action"][0])


@tag("batch_eval_fingerprint")
class MetaGobiiEvalScenarioTests(TestCase):
    def _recorded_statuses(self, scenario, tool_completion_results):
        recorded = []

        def _record(_run_id, _task_sequence, status, **kwargs):
            recorded.append(
                {
                    "task_name": kwargs.get("task_name"),
                    "status": status,
                    "observed_summary": kwargs.get("observed_summary", ""),
                }
            )

        with (
            patch.object(scenario, "_is_simulated", return_value=False),
            patch.object(scenario, "_run_tool_completion", side_effect=tool_completion_results) as mock_completion,
            patch.object(scenario, "record_task_result", side_effect=_record),
        ):
            scenario.run("run-1", "agent-1")

        statuses = {}
        summaries = {}
        for item in recorded:
            statuses[item["task_name"]] = item["status"]
            summaries[item["task_name"]] = item["observed_summary"]
        return statuses, summaries, mock_completion.call_count

    def test_scenario_records_passes_for_positive_case(self):
        scenario = ScenarioRegistry.get("meta_gobii_positive_team_creation")

        statuses, _summaries, _call_count = self._recorded_statuses(
            scenario,
            [
                [
                    {
                        "name": "search_system_skills",
                        "arguments": {"query": "team of Gobiis"},
                    }
                ],
                [
                    {
                        "name": "enable_system_skills",
                        "arguments": {"skill_keys": ["meta_gobii"]},
                    }
                ],
                [
                    {
                        "name": "record_meta_gobii_plan",
                        "arguments": {
                            "ordered_tools": [
                                "meta_gobii_get_agent_config_options",
                                "meta_gobii_create_agent",
                                "meta_gobii_link_agents",
                                "meta_gobii_send_agent_message",
                            ],
                            "tools_before_approval": ["meta_gobii_get_agent_config_options"],
                            "needs_human_confirmation": True,
                            "planned_agent_count": 3,
                            "planned_role_names": [
                                "Recruiting Lead",
                                "Sales Pipeline Gobii",
                                "Customer Signal Gobii",
                            ],
                            "extra_scope_items": [],
                            "contact_output_policy": "No contact output involved.",
                        },
                    }
                ],
                [
                    {
                        "name": "record_meta_gobii_response",
                        "arguments": {
                            "response_text": (
                                "Recruiting, sales, and customer signal roles with a link graph. "
                                "Please approve before I create anything."
                            ),
                            "proposed_roles": [
                                {"name": "Recruiting Lead", "responsibility": "Recruiting"},
                                {"name": "Sales Pipeline Gobii", "responsibility": "Sales"},
                                {"name": "Customer Signal Gobii", "responsibility": "Customer signal"},
                            ],
                            "proposed_links": ["Manager <-> Recruiting Lead"],
                            "initial_briefings": ["Recruiting Lead: own recruiting."],
                            "asks_for_approval": True,
                            "extra_scope_items": [],
                        },
                    }
                ],
            ],
        )

        self.assertEqual(
            statuses,
            {
                "discover_system_skill": EvalRunTask.Status.PASSED,
                "select_system_skill": EvalRunTask.Status.PASSED,
                "plan_meta_gobii_tools": EvalRunTask.Status.PASSED,
                "verify_confirmation_policy": EvalRunTask.Status.PASSED,
                "verify_contact_output_safety": EvalRunTask.Status.PASSED,
                "verify_minimal_action": EvalRunTask.Status.PASSED,
                "verify_schedule_scope": EvalRunTask.Status.PASSED,
                "verify_team_design": EvalRunTask.Status.PASSED,
                "verify_no_duplicate_output": EvalRunTask.Status.PASSED,
            },
        )

    def test_negative_scenario_does_not_run_tool_planning_when_skill_is_not_selected(self):
        scenario = ScenarioRegistry.get("meta_gobii_negative_content_task")

        statuses, _summaries, call_count = self._recorded_statuses(scenario, [[]])

        self.assertEqual(call_count, 1)
        self.assertEqual(statuses["discover_system_skill"], EvalRunTask.Status.PASSED)
        self.assertEqual(statuses["select_system_skill"], EvalRunTask.Status.PASSED)
        self.assertEqual(statuses["plan_meta_gobii_tools"], EvalRunTask.Status.PASSED)

    def test_discovery_prompt_covers_design_before_creation_requests(self):
        scenario = ScenarioRegistry.get("meta_gobii_team_management_capability_test")

        with patch.object(scenario, "_run_tool_completion", return_value=[]) as mock_completion:
            discovery_calls = scenario._run_skill_discovery(scenario.case, simulated=False)

        self.assertEqual(
            [call["name"] for call in discovery_calls],
            [SKILL_SEARCH_TOOL_NAME, ENABLE_SYSTEM_SKILLS_TOOL_NAME],
        )
        messages = mock_completion.call_args.kwargs["messages"]
        prompt_text = "\n".join(str(message.get("content") or "") for message in messages).lower()
        self.assertIn("design", prompt_text)
        self.assertIn("links", prompt_text)
        self.assertIn("briefings", prompt_text)
        self.assertIn("before creation", prompt_text)

    def test_discovery_prompt_covers_demo_setup_team_requests(self):
        scenario = ScenarioRegistry.get("meta_gobii_no_schedule_demo_team")

        with patch.object(scenario, "_run_tool_completion", return_value=[]) as mock_completion:
            discovery_calls = scenario._run_skill_discovery(scenario.case, simulated=False)

        self.assertEqual(
            [call["name"] for call in discovery_calls],
            [SKILL_SEARCH_TOOL_NAME, ENABLE_SYSTEM_SKILLS_TOOL_NAME],
        )
        messages = mock_completion.call_args.kwargs["messages"]
        prompt_text = "\n".join(str(message.get("content") or "") for message in messages).lower()
        self.assertIn("demo", prompt_text)
        self.assertIn("setup-only", prompt_text)
        self.assertIn("does not make gobii creation content-only", prompt_text)

    def test_response_normalization_derives_missing_briefings_from_plan(self):
        scenario = ScenarioRegistry.get("meta_gobii_no_schedule_recruiting_project_team")

        response_args = scenario._normalize_response_args(
            scenario.case,
            {
                "ordered_tools": [
                    "meta_gobii_create_agent",
                    "meta_gobii_link_agents",
                    "meta_gobii_send_agent_message",
                ],
                "needs_human_confirmation": True,
                "planned_role_names": ["Sourcing", "Screening", "Coordinator"],
                "extra_scope_items": [],
            },
            {
                "response_text": "Please approve this plan.",
                "proposed_roles": [{"name": "Sourcing", "responsibility": "Own sourcing."}],
                "proposed_links": [],
                "initial_briefings": [],
                "asks_for_approval": False,
            },
        )

        self.assertTrue(response_args["asks_for_approval"])
        self.assertTrue(response_args["proposed_links"])
        self.assertEqual(len(response_args["initial_briefings"]), 3)


@tag("batch_eval_fingerprint")
class MetaGobiiLocalEvalSetupTests(TestCase):
    def test_eager_sqlite_execution_stays_serial(self):
        plan = build_eval_execution_plan(
            sync_mode=False,
            celery_task_always_eager=True,
            using_sqlite=True,
            requested_max_concurrency=8,
            queued_run_count=169,
        )

        self.assertEqual(plan.effective_max_concurrency, 1)
        self.assertFalse(plan.use_eager_thread_pool)
        self.assertTrue(plan.warn_sqlite_serial)

    def test_eager_postgres_execution_uses_bounded_thread_pool(self):
        plan = build_eval_execution_plan(
            sync_mode=False,
            celery_task_always_eager=True,
            using_sqlite=False,
            requested_max_concurrency=8,
            queued_run_count=169,
        )

        self.assertEqual(plan.effective_max_concurrency, 8)
        self.assertTrue(plan.use_eager_thread_pool)
        self.assertFalse(plan.warn_sqlite_serial)

    def test_fake_redis_supports_redlock_scripts_for_local_eval_processing(self):
        from pottery import Redlock

        from config.redis_client import _FakeRedis

        redis_client = _FakeRedis()
        lock = Redlock(
            key="agent-event-processing:test-agent",
            masters={redis_client},
            auto_release_time=5,
        )

        self.assertTrue(lock.acquire(blocking=False))
        self.assertGreater(lock.locked(), 0)
        lock.extend()
        self.assertGreater(lock.locked(), 0)
        lock.release()
        self.assertEqual(lock.locked(), 0)

    def test_fake_redis_supports_agent_event_streams_for_local_eval_processing(self):
        from config.redis_client import _FakeRedis

        redis_client = _FakeRedis()
        stream_key = "agent:events:test-agent:stream"

        self.assertEqual(redis_client.publish("agent:events:test-agent", "{}"), 0)
        first_id = redis_client.xadd(stream_key, {"data": "{\"type\":\"processing_complete\"}"})

        messages = redis_client.xread({stream_key: "0-0"}, count=50, block=1)
        self.assertEqual(messages, [(stream_key, [(first_id, {"data": "{\"type\":\"processing_complete\"}"})])])
        self.assertEqual(redis_client.xread({stream_key: first_id}, count=50, block=1), [])

    def test_local_eval_schema_compat_adds_missing_debug_artifacts_column(self):
        class FakeCursor:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

        class FakeIntrospection:
            def table_names(self):
                return ["api_evalruntask"]

            def get_table_description(self, cursor, table_name):
                return [SimpleNamespace(name="id")]

        class FakeSchemaEditor:
            def __init__(self):
                self.added_fields = []

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def add_field(self, model, field):
                self.added_fields.append((model, field))

        class FakeConnection:
            def __init__(self, schema_editor):
                self.introspection = FakeIntrospection()
                self._schema_editor = schema_editor

            def cursor(self):
                return FakeCursor()

            def schema_editor(self):
                return self._schema_editor

        stdout = StringIO()
        schema_editor = FakeSchemaEditor()

        with patch("api.evals.local_setup.connection", FakeConnection(schema_editor)):
            added = ensure_eval_local_compat_columns(stdout=stdout)

        self.assertEqual(added, 1)
        self.assertEqual(schema_editor.added_fields[0][1].column, "debug_artifacts")
        self.assertIn("api_evalruntask.debug_artifacts", stdout.getvalue())

    def test_local_openrouter_profile_seed_uses_env_var_name_without_secret_output(self):
        stdout = StringIO()
        fake_secret = "sk-test-secret-value"

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": fake_secret}):
            profile = ensure_openrouter_deepseek_v4_flash_profile(stdout=stdout)

        provider = LLMProvider.objects.get(key="openrouter")
        self.assertEqual(provider.env_var_name, "OPENROUTER_API_KEY")
        self.assertFalse(provider.api_key_encrypted)
        self.assertEqual(profile.name, "openrouter-deepseek-v4-flash")
        self.assertNotIn(fake_secret, stdout.getvalue())
        self.assertIn("OPENROUTER_API_KEY", stdout.getvalue())
        self.assertEqual(
            ProfilePersistentTierEndpoint.objects.filter(
                tier__token_range__profile=profile,
                endpoint__key="openrouter_deepseek_v4_flash",
            ).count(),
            1,
        )

    @override_settings(
        EVAL_LOCAL_CUSTOM_MODEL="anthropic/example-model",
        EVAL_LOCAL_CUSTOM_API_KEY_ENV_VAR="ANTHROPIC_API_KEY",
        EVAL_LOCAL_CUSTOM_PROFILE_NAME="custom-litellm",
        EVAL_LOCAL_CUSTOM_ENDPOINT_KEY="custom_litellm",
        EVAL_LOCAL_CUSTOM_PROVIDER_KEY="custom-litellm",
        EVAL_LOCAL_CUSTOM_PROVIDER_DISPLAY_NAME="Custom LiteLLM",
        EVAL_LOCAL_CUSTOM_API_BASE="",
    )
    def test_local_eval_setup_seeds_common_model_profiles_without_secret_output(self):
        stdout = StringIO()
        fake_values = {
            "OPENROUTER_API_KEY": "openrouter-secret-value",
            "OPENAI_API_KEY": "openai-secret-value",
            "ANTHROPIC_API_KEY": "anthropic-secret-value",
        }

        with patch.dict(os.environ, fake_values):
            profiles = ensure_eval_local_routing_profiles(stdout=stdout)

        profile_names = {profile.name for profile in profiles}
        self.assertIn("openrouter-deepseek-v4-flash", profile_names)
        self.assertIn("openrouter-qwen", profile_names)
        self.assertIn("openai-gpt-4-1-mini", profile_names)
        self.assertIn("custom-litellm", profile_names)

        self.assertEqual(LLMProvider.objects.get(key="openrouter").env_var_name, "OPENROUTER_API_KEY")
        self.assertEqual(LLMProvider.objects.get(key="openai").env_var_name, "OPENAI_API_KEY")
        self.assertEqual(
            LLMProvider.objects.get(key="custom-litellm").env_var_name,
            "ANTHROPIC_API_KEY",
        )
        self.assertTrue(
            PersistentModelEndpoint.objects.filter(
                key="openrouter_qwen",
                litellm_model="qwen/qwen3.6-flash",
            ).exists()
        )
        self.assertTrue(
            PersistentModelEndpoint.objects.filter(
                key="openai_gpt_4_1_mini",
                litellm_model="gpt-4.1-mini",
            ).exists()
        )
        self.assertTrue(
            PersistentModelEndpoint.objects.filter(
                key="custom_litellm",
                litellm_model="anthropic/example-model",
            ).exists()
        )
        for secret_value in fake_values.values():
            self.assertNotIn(secret_value, stdout.getvalue())

    def test_run_evals_lists_registered_suites_and_scenarios(self):
        stdout = StringIO()

        call_command("run_evals", "--list", stdout=stdout)

        output = stdout.getvalue()
        self.assertIn("Available eval suites", output)
        self.assertIn("meta_gobii", output)
        self.assertIn("meta_gobii_positive_team_creation", output)
        self.assertIn("tier=core", output)
        self.assertIn("category=meta_gobii", output)
        self.assertIn("openrouter-deepseek-v4-flash", output)

    def test_run_evals_list_applies_metadata_filters(self):
        stdout = StringIO()

        call_command("run_evals", "--list", "--tier", "smoke", stdout=stdout)

        output = stdout.getvalue()
        self.assertIn("Scenario filters: tier=smoke", output)
        self.assertIn("echo_response", output)
        self.assertIn("weather_lookup", output)
        self.assertNotIn("meta_gobii_positive_team_creation tier=core", output)

    def test_run_evals_lists_seeded_routing_profiles(self):
        stdout = StringIO()

        ensure_eval_local_routing_profiles()
        call_command("run_evals", "--list-routing-profiles", stdout=stdout)

        output = stdout.getvalue()
        self.assertIn("Available LLM routing profiles", output)
        self.assertIn("openrouter-deepseek-v4-flash", output)
        self.assertIn("openrouter-qwen", output)
        self.assertIn("openai-gpt-4-1-mini", output)

    def test_simulated_meta_gobii_run_uses_canonical_run_evals_path(self):
        stdout = StringIO()

        call_command(
            "run_evals",
            "--suite",
            "meta_gobii",
            "--sync",
            "--n-runs",
            "1",
            "--simulated",
            stdout=stdout,
        )

        suite_run = EvalSuiteRun.objects.latest("created_at")
        self.assertEqual(suite_run.suite_slug, "meta_gobii")
        self.assertEqual(suite_run.launch_config, {"mode": "simulated"})
        self.assertEqual(suite_run.runs.count(), len(META_GOBII_EVAL_CASES))
        self.assertFalse(
            EvalRunTask.objects.filter(run__suite_run=suite_run)
            .exclude(status=EvalRunTask.Status.PASSED)
            .exists()
        )
        self.assertIn("SIMULATED mode", stdout.getvalue())

    def test_simulated_single_meta_gobii_scenario_uses_canonical_run_evals_path(self):
        stdout = StringIO()

        call_command(
            "run_evals",
            "--scenario",
            "meta_gobii_negative_content_task",
            "--sync",
            "--n-runs",
            "1",
            "--simulated",
            stdout=stdout,
        )

        suite_run = EvalSuiteRun.objects.latest("created_at")
        self.assertEqual(suite_run.suite_slug, "single::meta_gobii_negative_content_task")
        self.assertEqual(suite_run.launch_config, {"mode": "simulated"})
        self.assertEqual(suite_run.runs.count(), 1)
        self.assertEqual(suite_run.runs.first().scenario_slug, "meta_gobii_negative_content_task")
        self.assertFalse(
            EvalRunTask.objects.filter(run__suite_run=suite_run)
            .exclude(status=EvalRunTask.Status.PASSED)
            .exists()
        )

    def test_run_evals_filters_scenarios_by_tag(self):
        stdout = StringIO()

        call_command(
            "run_evals",
            "--suite",
            "meta_gobii",
            "--tag",
            "contact_safety",
            "--sync",
            "--n-runs",
            "1",
            "--simulated",
            stdout=stdout,
        )

        suite_run = EvalSuiteRun.objects.latest("created_at")
        self.assertEqual(suite_run.suite_slug, "meta_gobii")
        scenario_slugs = set(suite_run.runs.values_list("scenario_slug", flat=True))
        self.assertEqual(
            scenario_slugs,
            {
                "meta_gobii_contact_approve_internal",
                "meta_gobii_no_schedule_approve_contact_only",
            },
        )
        self.assertIn("Applying scenario filters: tag=contact_safety", stdout.getvalue())

    def test_run_evals_repeated_routing_profiles_create_matrix_suite_runs(self):
        stdout = StringIO()

        ensure_eval_local_routing_profiles()
        call_command(
            "run_evals",
            "--scenario",
            "meta_gobii_negative_content_task",
            "--sync",
            "--n-runs",
            "1",
            "--simulated",
            "--routing-profile",
            "openrouter-deepseek-v4-flash",
            "--routing-profile",
            "openrouter-qwen",
            stdout=stdout,
        )

        suite_runs = list(EvalSuiteRun.objects.order_by("created_at"))
        self.assertEqual(len(suite_runs), 2)
        self.assertEqual({suite.launch_config["matrix_profile"] for suite in suite_runs}, {
            "openrouter-deepseek-v4-flash",
            "openrouter-qwen",
        })
        self.assertTrue(all(suite.llm_routing_profile_id for suite in suite_runs))
        self.assertIn("routing-profile matrix with 2 profiles", stdout.getvalue())
