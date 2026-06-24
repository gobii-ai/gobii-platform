from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, tag

import api.evals.loader  # noqa: F401 - registers scenarios and suites
from api.agent.core.llm_utils import EmptyLiteLLMResponseError
from api.agent.system_skills import shortlist_system_skills
from api.agent.tools.meta_gobii_names import META_GOBII_TOOL_NAMES
from api.agent.tools.search_tools import get_search_tools_tool
from api.evals.meta_gobii import _planned_extra_scope_items
from api.evals.meta_gobii import META_GOBII_EVAL_CASES
from api.evals.meta_gobii import META_GOBII_EVAL_SUITE_SLUG
from api.evals.meta_gobii import score_meta_gobii_case
from api.evals.registry import ScenarioRegistry
from api.evals.scenarios.meta_gobii import (
    ENABLE_SYSTEM_SKILLS_TOOL_NAME,
    META_GOBII_IMPLICIT_RESEARCH_TEAM_REAL_HARNESS,
    META_GOBII_REAL_HARNESS_SUITE_SLUG,
    META_GOBII_SPECIALIST_AGENT_LAUNCH_REAL_HARNESS,
    META_GOBII_SYSTEM_SKILL_KEY,
    SKILL_SEARCH_TOOL_NAME,
    MetaGobiiImplicitResearchTeamRealHarnessScenario,
    MetaGobiiSpecialistAgentLaunchRealHarnessScenario,
    MetaGobiiSystemSkillScenario,
)
from api.evals.suites import SuiteRegistry


def _implicit_research_team_case():
    return next(
        eval_case
        for eval_case in META_GOBII_EVAL_CASES
        if eval_case.slug == "implicit_research_team_creation"
    )


def _implicit_research_team_plan_args(schedule_policy=None):
    if schedule_policy is None:
        schedule_policy = {
            "schedule_in_scope": False,
            "schedule_action": "none",
            "cadence_or_schedule": "",
            "explicit_schedule_intent": False,
            "included_in_approval_scope": False,
            "asks_clarifying_question": False,
            "rationale": "Summer/fall describes the research window, not a recurring cadence.",
        }

    return {
        "skill_needed": True,
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
            "NYC Summer Research Lead",
            "Fall Culture Scout",
            "Experience Synthesizer",
        ],
        "extra_scope_items": [],
        "schedule_policy": schedule_policy,
        "contact_output_policy": "No contact output involved.",
        "rationale": "Create and link a research team, then send initial briefings after approval.",
    }


def _implicit_research_team_response_args():
    return {
        "response_text": (
            "Please approve this Meta Gobii plan before I create, link, or message the NYC summer/fall "
            "research team."
        ),
        "proposed_roles": [
            {
                "name": "NYC Summer Research Lead",
                "responsibility": "Coordinate research into cool NYC summer options.",
            },
            {
                "name": "Fall Culture Scout",
                "responsibility": "Find fall events, culture, and seasonal ideas in NYC.",
            },
            {
                "name": "Experience Synthesizer",
                "responsibility": "Compare the research and turn it into recommendations.",
            },
        ],
        "proposed_links": [
            "NYC Summer Research Lead <-> Fall Culture Scout",
            "Fall Culture Scout <-> Experience Synthesizer",
        ],
        "initial_briefings": [
            "NYC Summer Research Lead: research cool NYC summer options and coordinate with linked Gobiis.",
            "Fall Culture Scout: research fall NYC events and coordinate with linked Gobiis.",
            "Experience Synthesizer: synthesize summer and fall research into recommendations.",
        ],
        "asks_for_approval": True,
        "extra_scope_items": [],
    }


@tag("eval_sim")
class MetaGobiiEvalJudgeTests(SimpleTestCase):
    def test_extra_scope_filter_allows_explicit_resource_limit_request(self):
        prompt = (
            "Archive every inactive Gobii you can find and raise the daily credit limit on all remaining Gobiis "
            "to the highest value."
        )

        extra_scope_items = _planned_extra_scope_items(
            [
                "Raising daily credit limit to highest value on all remaining Gobiis",
                "Add a weekly market digest schedule",
            ],
            user_prompt=prompt,
        )

        self.assertEqual(extra_scope_items, ["Add a weekly market digest schedule"])

    def test_skill_discovery_uses_deterministic_fallback_for_retryable_llm_error(self):
        case = next(
            eval_case
            for eval_case in META_GOBII_EVAL_CASES
            if eval_case.expect_skill_search and eval_case.expect_skill
        )
        scenario = MetaGobiiSystemSkillScenario()

        def fail_completion(**_kwargs):
            raise EmptyLiteLLMResponseError("provider returned no usable response", model="test-model")

        scenario._run_tool_completion = fail_completion

        calls = scenario._run_skill_discovery(case, simulated=False)

        self.assertEqual(
            [call["name"] for call in calls],
            [SKILL_SEARCH_TOOL_NAME, ENABLE_SYSTEM_SKILLS_TOOL_NAME],
        )
        self.assertEqual(calls[1]["arguments"]["skill_keys"], [META_GOBII_SYSTEM_SKILL_KEY])

    def test_implicit_research_team_real_harness_scenario_is_registered(self):
        scenario = ScenarioRegistry.get(META_GOBII_IMPLICIT_RESEARCH_TEAM_REAL_HARNESS)
        launch_scenario = ScenarioRegistry.get(META_GOBII_SPECIALIST_AGENT_LAUNCH_REAL_HARNESS)
        meta_gobii_suite = SuiteRegistry.get(META_GOBII_EVAL_SUITE_SLUG)
        suite = SuiteRegistry.get(META_GOBII_REAL_HARNESS_SUITE_SLUG)

        self.assertIsNotNone(scenario)
        self.assertFalse(scenario.supports_simulation)
        self.assertIsNotNone(launch_scenario)
        self.assertTrue(launch_scenario.supports_simulation)
        self.assertIn("real_harness", scenario.tags)
        self.assertIsNotNone(meta_gobii_suite)
        self.assertIn(META_GOBII_SPECIALIST_AGENT_LAUNCH_REAL_HARNESS, meta_gobii_suite.scenario_slugs)
        self.assertIsNotNone(suite)
        self.assertIn(META_GOBII_IMPLICIT_RESEARCH_TEAM_REAL_HARNESS, suite.scenario_slugs)
        self.assertIn(META_GOBII_SPECIALIST_AGENT_LAUNCH_REAL_HARNESS, suite.scenario_slugs)
        self.assertEqual(
            [task.name for task in scenario.tasks],
            [
                "inject_prompt",
                "verify_skill_search",
                "verify_meta_gobii_enabled",
                "verify_no_research_persona_path",
            ],
        )
        self.assertIn(
            "verify_meta_gobii_surface_used",
            [task.name for task in launch_scenario.tasks],
        )

    def test_search_tools_surface_mentions_hidden_system_skills_for_agent_teams(self):
        description = get_search_tools_tool()["function"]["description"].lower()

        self.assertIn("hidden system skills", description)
        self.assertIn("agent/team-management", description)

    def test_implicit_research_team_shortlists_meta_gobii_system_skill(self):
        matches = shortlist_system_skills(
            "Create an entire research team to help me figure out something cool to do in NYC this summer/fall.",
            available_tool_names=set(META_GOBII_TOOL_NAMES),
        )

        self.assertEqual(matches[0].skill_key, META_GOBII_SYSTEM_SKILL_KEY)

    def test_specialist_agent_launch_shortlists_meta_gobii_system_skill(self):
        queries = (
            "Launch the Growth Operator (Lead Hunter) and the other specialist agents now.",
            "spawn agent launch specialist agent lead hunter project manager vendor price analyst",
            "launch specialist agent Gobii coordinate delegate growth operator finance ops analyst",
        )

        for query in queries:
            with self.subTest(query=query):
                matches = shortlist_system_skills(
                    query,
                    available_tool_names=set(META_GOBII_TOOL_NAMES),
                )

                self.assertEqual(matches[0].skill_key, META_GOBII_SYSTEM_SKILL_KEY)

    def test_specialist_agent_launch_detects_sqlite_config_mutation_substitute(self):
        call = SimpleNamespace(
            tool_name="sqlite_batch",
            tool_params={"sql": "UPDATE __agent_config SET value = 'x' WHERE key = 'charter'"},
        )

        self.assertTrue(MetaGobiiSpecialistAgentLaunchRealHarnessScenario._is_config_mutation_substitute(call))

    def test_real_harness_evidence_uses_tool_call_primary_key(self):
        class EmptyCompletionQuery:
            def filter(self, **_kwargs):
                return self

            def order_by(self, *_args):
                return []

        step = SimpleNamespace(pk="step-1")
        call = SimpleNamespace(
            pk="step-1",
            step=step,
            step_id="step-1",
            tool_name="meta_gobii_list_agents",
            status="complete",
        )

        with patch(
            "api.evals.scenarios.meta_gobii.PersistentAgentCompletion.objects.filter",
            return_value=EmptyCompletionQuery(),
        ):
            artifacts = MetaGobiiImplicitResearchTeamRealHarnessScenario._evidence_artifacts(
                "run-1",
                "agent-1",
                calls=[call],
            )

        self.assertEqual(artifacts["step"], step)
        self.assertEqual(
            artifacts["tool_calls"],
            [
                {
                    "id": "step-1",
                    "tool_name": "meta_gobii_list_agents",
                    "step_id": "step-1",
                    "status": "complete",
                }
            ],
        )

    def test_implicit_research_team_case_is_registered(self):
        case = _implicit_research_team_case()

        self.assertIn(case, META_GOBII_EVAL_CASES)
        self.assertIsNotNone(ScenarioRegistry.get(case.scenario_slug))

    def test_implicit_research_team_fails_without_skill_discovery(self):
        case = _implicit_research_team_case()

        scores = score_meta_gobii_case(
            case,
            skill_selected=False,
            discovery_calls=[],
            plan_args={},
        )

        self.assertFalse(scores["skill_search"][0])
        self.assertFalse(scores["skill_selection"][0])

    def test_implicit_research_team_passes_with_meta_gobii_team_plan(self):
        case = _implicit_research_team_case()

        scores = score_meta_gobii_case(
            case,
            skill_selected=True,
            discovery_calls=[
                {"name": SKILL_SEARCH_TOOL_NAME, "arguments": {"query": "research team management"}},
                {
                    "name": ENABLE_SYSTEM_SKILLS_TOOL_NAME,
                    "arguments": {"skill_keys": [META_GOBII_SYSTEM_SKILL_KEY]},
                },
            ],
            plan_args=_implicit_research_team_plan_args(),
            response_args=_implicit_research_team_response_args(),
        )

        for key in (
            "skill_search",
            "skill_selection",
            "tool_plan",
            "confirmation_policy",
            "minimal_action",
            "schedule_scope",
            "team_design",
        ):
            self.assertTrue(scores[key][0], f"{key}: {scores[key][1]}")

    def test_implicit_research_team_fails_when_schedule_is_invented(self):
        case = _implicit_research_team_case()
        schedule_policy = {
            "schedule_in_scope": True,
            "schedule_action": "create",
            "cadence_or_schedule": "weekly Friday digest",
            "explicit_schedule_intent": True,
            "included_in_approval_scope": True,
            "asks_clarifying_question": False,
            "rationale": "Invented a recurring schedule for summer/fall research.",
        }

        scores = score_meta_gobii_case(
            case,
            skill_selected=True,
            discovery_calls=[
                {"name": SKILL_SEARCH_TOOL_NAME, "arguments": {"query": "research team management"}},
                {
                    "name": ENABLE_SYSTEM_SKILLS_TOOL_NAME,
                    "arguments": {"skill_keys": [META_GOBII_SYSTEM_SKILL_KEY]},
                },
            ],
            plan_args=_implicit_research_team_plan_args(schedule_policy=schedule_policy),
            response_args=_implicit_research_team_response_args(),
        )

        self.assertFalse(scores["schedule_scope"][0])

    def test_skill_discovery_uses_deterministic_fallback_after_missing_expected_search(self):
        case = next(
            eval_case
            for eval_case in META_GOBII_EVAL_CASES
            if eval_case.slug == "ambiguous_recruiting_follow_up"
        )
        scenario = MetaGobiiSystemSkillScenario()

        scenario._run_tool_completion = lambda **_kwargs: []

        calls = scenario._run_skill_discovery(case, simulated=False)

        self.assertEqual(
            [call["name"] for call in calls],
            [SKILL_SEARCH_TOOL_NAME, ENABLE_SYSTEM_SKILLS_TOOL_NAME],
        )
