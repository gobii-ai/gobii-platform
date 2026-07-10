from contextlib import nullcontext
import sqlite3
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, tag

import api.evals.loader  # noqa: F401 - registers scenarios and suites
from api.agent.core.event_processing import _resolve_eval_mock_result
from api.agent.system_skills.defaults import RECRUITMENT_SOURCING_SYSTEM_SKILL
from api.agent.tools.eval_synthetic_tools import EVAL_SYNTHETIC_TOOL_DEFINITIONS
from api.evals.registry import ScenarioRegistry
from api.evals.scenarios.recruitment_sourcing import (
    RECRUITMENT_SOURCING_CASES,
    RECRUITMENT_SOURCING_CRITERIA_FIDELITY,
    RECRUITMENT_SOURCING_DEDUPE_LEDGER,
    RECRUITMENT_SOURCING_INTAKE_GATES_SOURCING,
    RECRUITMENT_SOURCING_PARTIAL_VERIFICATION,
    RECRUITMENT_RETRIEVAL_TOOL_NAMES,
    RECRUITMENT_SOURCING_SCENARIO_SLUGS,
    RECRUITMENT_SOURCING_SOURCE_FALLBACK,
    RECRUITMENT_SOURCING_SUITE_SLUG,
    SOURCE_FALLBACK_PEOPLE,
    DEDUPE_LEDGER_ROWS,
    DEDUPE_QUEUE_ROWS,
    _candidate_link_pairs_in_body,
    _contains_proximate_terms,
    _group_human_input_requests,
    _has_anchor_without_proximate_context,
    _human_input_request_body,
    _is_bounded_missing_material_request,
    _ledger_query_is_substantive,
    _partial_resume_state_tables,
    _partial_response_has_correct_polarity,
    _duplicate_retrieval_signatures,
    _retrieval_call_signature,
    _tool_call_has_usable_result,
)
from api.evals.suites import SuiteRegistry


@tag("batch_recruitment_sourcing", "eval_sim")
class RecruitmentSourcingScenarioTests(SimpleTestCase):
    def test_recruitment_sourcing_suite_contains_five_scenarios(self):
        suite = SuiteRegistry.get(RECRUITMENT_SOURCING_SUITE_SLUG)

        self.assertIsNotNone(suite)
        self.assertEqual(tuple(suite.scenario_slugs), RECRUITMENT_SOURCING_SCENARIO_SLUGS)
        self.assertEqual(len(suite.scenario_slugs), 5)

    def test_generated_scenarios_have_expected_metadata(self):
        registered = ScenarioRegistry.list_all()

        for slug in RECRUITMENT_SOURCING_SCENARIO_SLUGS:
            scenario = registered[slug]
            metadata = scenario.get_metadata()
            self.assertEqual(metadata.category, "recruitment_sourcing")
            self.assertEqual(metadata.area, "system_skills")
            self.assertEqual(metadata.expected_runtime, "short")
            self.assertEqual(metadata.cost_class, "low")
            self.assertIn("recruitment_sourcing", metadata.tags)
            self.assertIn("system_skill", metadata.tags)
            self.assertIn("verify_efficient_retrieval", [task.name for task in scenario.tasks])

    def test_skill_is_provider_neutral_and_preserves_requirements(self):
        instructions = RECRUITMENT_SOURCING_SYSTEM_SKILL.prompt_instructions

        self.assertIn("finding candidates worth recruiter review", instructions)
        self.assertIn("hard requirements", instructions)
        self.assertIn("preferred signals", instructions)
        self.assertIn("Choose sources based on the tools and permissions actually available", instructions)
        self.assertIn("If a source is unavailable or blocked", instructions)
        self.assertIn("dedupe by profile url first", instructions.lower())
        self.assertIn("Quality and criteria fidelity beat volume", instructions)
        self.assertIn("Do not treat phrases like 'start today'", instructions)
        self.assertIn("those phrases only express urgency", instructions.lower())
        self.assertIn("failed intake-question call is not a user answer", instructions)
        self.assertNotIn("Crisp Recruit", instructions)

    def test_cases_cover_core_recruitment_sourcing_behaviors(self):
        self.assertEqual(
            {case.slug for case in RECRUITMENT_SOURCING_CASES},
            {
                RECRUITMENT_SOURCING_INTAKE_GATES_SOURCING,
                RECRUITMENT_SOURCING_CRITERIA_FIDELITY,
                RECRUITMENT_SOURCING_SOURCE_FALLBACK,
                RECRUITMENT_SOURCING_DEDUPE_LEDGER,
                RECRUITMENT_SOURCING_PARTIAL_VERIFICATION,
            },
        )

    def test_intake_case_forbids_sourcing_tools_before_requirements(self):
        case = next(case for case in RECRUITMENT_SOURCING_CASES if case.slug == RECRUITMENT_SOURCING_INTAKE_GATES_SOURCING)
        policy = case.eval_stop_policy()

        self.assertEqual(case.expected_tool_names, ())
        self.assertIn("search_tools", policy["stop_on_tool_names"])
        self.assertNotIn("search_tools", policy["allowed_tool_names"])
        self.assertIn("request_human_input", policy["allowed_tool_names"])
        self.assertTrue(policy["stop_on_human_input_request"])
        self.assertTrue(case.require_bounded_missing_material_request)
        self.assertEqual(
            case.response_term_groups,
            (("job posting", "requirements", "required skills", "screening criteria", "dealbreakers"),),
        )

    def test_intake_response_requires_a_bounded_request_not_a_keyword_echo(self):
        self.assertFalse(
            _is_bounded_missing_material_request(
                "I will proceed without the job posting or screening criteria.",
                response_count=1,
                tracked_request_count=0,
            )
        )
        self.assertFalse(
            _is_bounded_missing_material_request(
                "The job posting and requirements are missing.",
                response_count=4,
                tracked_request_count=0,
            )
        )
        self.assertTrue(
            _is_bounded_missing_material_request(
                "Could you share the job posting, required skills, and dealbreakers?",
                response_count=1,
                tracked_request_count=0,
            )
        )
        self.assertTrue(
            _is_bounded_missing_material_request(
                "Please provide the screening criteria before I source candidates.",
                response_count=2,
                tracked_request_count=1,
            )
        )

    def test_human_input_response_text_includes_visible_option_copy(self):
        request = SimpleNamespace(
            question="What can you share?",
            options_json=[
                {
                    "title": "Gather details",
                    "description": "I'll get the job posting or hiring-manager feedback before you search.",
                }
            ],
        )

        body = _human_input_request_body(request)

        self.assertIn("What can you share?", body)
        self.assertIn("Gather details", body)
        self.assertIn("job posting", body)

    def test_child_human_input_questions_from_one_tool_step_form_one_interaction(self):
        requests = [
            SimpleNamespace(
                id="request-1",
                originating_step_id="step-1",
                created_at=1,
                question="What required skills should candidates have?",
                options_json=[],
            ),
            SimpleNamespace(
                id="request-2",
                originating_step_id="step-1",
                created_at=2,
                question="What are the dealbreakers?",
                options_json=[],
            ),
            SimpleNamespace(
                id="request-3",
                originating_step_id="step-1",
                created_at=3,
                question="What seniority or years of experience are required?",
                options_json=[],
            ),
            SimpleNamespace(
                id="request-4",
                originating_step_id="step-1",
                created_at=4,
                question="Do you have a preferred source?",
                options_json=[],
            ),
        ]

        interactions = _group_human_input_requests(requests)

        self.assertEqual(len(interactions), 1)
        _created_at, body, artifact = interactions[0]
        self.assertIs(artifact, requests[0])
        self.assertIn("required skills", body)
        self.assertIn("dealbreakers", body)
        self.assertIn("seniority", body)
        self.assertIn("preferred source", body)
        self.assertTrue(
            _is_bounded_missing_material_request(
                body,
                response_count=len(interactions),
                tracked_request_count=len(interactions),
            )
        )

    def test_criteria_case_includes_decoys_but_requires_only_valid_candidates(self):
        case = next(case for case in RECRUITMENT_SOURCING_CASES if case.slug == RECRUITMENT_SOURCING_CRITERIA_FIDELITY)
        people_result = case.mock_config["mcp_brightdata_web_data_linkedin_people_search"]["content"]
        names = {item["name"] for item in people_result["items"]}

        self.assertEqual(case.expected_tool_names, ("mcp_brightdata_web_data_linkedin_people_search",))
        self.assertEqual(case.accepted_tool_alternatives, {})
        self.assertNotIn("apollo_io-search-contacts", case.mock_config)
        self.assertIn("Mina Patel", names)
        self.assertIn("Evan Brooks", names)
        self.assertIn("Dana Lee", names)
        self.assertEqual(case.response_term_groups, (("Mina Patel",), ("Priya Shah",)))
        self.assertEqual(
            case.forbidden_response_terms,
            ("evan-brooks-eval", "dana-lee-eval"),
        )
        self.assertEqual(
            tuple(group[0][0] for group in case.forbidden_without_proximate_response_terms),
            ("Evan Brooks", "Dana Lee"),
        )
        self.assertEqual(case.required_proximate_response_terms, ())
        self.assertEqual(case.max_retrieval_tool_calls, 4)

    def test_disqualified_candidate_names_must_be_contextually_excluded(self):
        anchors = ("Evan Brooks",)
        contexts = ("excluded", "not qualified")

        self.assertFalse(
            _has_anchor_without_proximate_context(
                "Excluded: Evan Brooks — Estimator is outside the requested title set.",
                anchors,
                contexts,
            )
        )
        self.assertTrue(
            _has_anchor_without_proximate_context(
                "Qualified candidates: Evan Brooks — Estimator at BuildRight GC.",
                anchors,
                contexts,
            )
        )
        self.assertTrue(
            _has_anchor_without_proximate_context(
                "Excluded roles: Estimator. Evan Brooks is a strong recommended match.",
                anchors,
                contexts,
            )
        )
        self.assertTrue(
            _has_anchor_without_proximate_context(
                "Dana Lee is a recommended match.\nEvan Brooks — excluded as an Estimator.",
                ("Dana Lee",),
                ("excluded", "outside", "not qualified"),
            )
        )
        self.assertTrue(
            _has_anchor_without_proximate_context(
                "Evan Brooks was excluded, but is actually our strongest recommended match.",
                anchors,
                contexts,
            )
        )

    def test_source_fallback_blocks_apollo_paths(self):
        case = next(case for case in RECRUITMENT_SOURCING_CASES if case.slug == RECRUITMENT_SOURCING_SOURCE_FALLBACK)
        policy = case.eval_stop_policy()

        self.assertEqual(case.expected_tool_names, ("mcp_brightdata_search_engine",))
        self.assertEqual(
            case.accepted_tool_alternatives["mcp_brightdata_search_engine"],
            ("mcp_brightdata_web_data_linkedin_people_search",),
        )
        self.assertIn("mcp_brightdata_web_data_linkedin_company_profile", case.allowed_extra_tool_names)
        self.assertIn("mcp_brightdata_web_data_linkedin_person_profile", case.allowed_extra_tool_names)
        self.assertIn("mcp_brightdata_web_data_linkedin_job_listings", case.allowed_extra_tool_names)
        self.assertIn("http_request", case.allowed_extra_tool_names)
        self.assertIn("mcp_brightdata_web_data_linkedin_people_search", case.mock_config)
        self.assertIn("mcp_brightdata_web_data_linkedin_company_profile", case.mock_config)
        self.assertIn("mcp_brightdata_web_data_linkedin_person_profile", case.mock_config)
        self.assertIn("mcp_brightdata_web_data_linkedin_job_listings", case.mock_config)
        self.assertIn("apollo_io-search-contacts", case.forbidden_tool_names)
        self.assertIn("apollo_io-search-contacts", policy["stop_on_tool_names"])
        self.assertIn("http_request", policy["allowed_tool_names"])

        directory_result = _resolve_eval_mock_result(
            case.mock_config,
            "http_request",
            {"url": "https://www.nalsc.org/eval-directory"},
        )
        apollo_result = _resolve_eval_mock_result(
            case.mock_config,
            "http_request",
            {"url": "https://api.apollo.io/v1/people/search"},
        )
        self.assertEqual(directory_result["status"], "success")
        self.assertEqual(apollo_result["status"], "error")
        for person in SOURCE_FALLBACK_PEOPLE:
            profile_result = _resolve_eval_mock_result(
                case.mock_config,
                "mcp_brightdata_web_data_linkedin_person_profile",
                {"url": person["url"]},
            )
            self.assertEqual(profile_result["result"]["name"], person["name"])
            self.assertEqual(profile_result["result"]["url"], person["url"])
        self.assertEqual(case.max_retrieval_tool_calls, 6)
        self.assertIn("up to two strong profiles", case.prompt)
        self.assertEqual(case.min_candidate_link_pairs, 1)

    def test_source_fallback_requires_a_named_profile_with_its_own_link(self):
        case = next(
            case
            for case in RECRUITMENT_SOURCING_CASES
            if case.slug == RECRUITMENT_SOURCING_SOURCE_FALLBACK
        )
        generic_company_answer = (
            "NorthStar Legal Search is a strong legal recruiting archetype. "
            "https://northstarlegal.example.test/team"
        )
        mismatched_profile_answer = (
            "Carolina Vega is a legal recruiter: "
            "https://www.linkedin.com/in/jordan-kim-eval"
        )
        linked_profile_answer = (
            "Carolina Vega — Legal Recruiter — "
            "https://www.linkedin.com/in/carolina-vega-eval"
        )

        self.assertEqual(
            _candidate_link_pairs_in_body(
                generic_company_answer,
                case.required_candidate_link_pairs,
            ),
            set(),
        )
        self.assertEqual(
            _candidate_link_pairs_in_body(
                mismatched_profile_answer,
                case.required_candidate_link_pairs,
            ),
            set(),
        )
        self.assertEqual(
            _candidate_link_pairs_in_body(
                linked_profile_answer,
                case.required_candidate_link_pairs,
            ),
            {"Carolina Vega"},
        )

    def test_retrieval_efficiency_caps_allow_bounded_work_not_search_loops(self):
        cases = {case.slug: case for case in RECRUITMENT_SOURCING_CASES}

        self.assertEqual(cases[RECRUITMENT_SOURCING_INTAKE_GATES_SOURCING].max_retrieval_tool_calls, 0)
        self.assertEqual(cases[RECRUITMENT_SOURCING_CRITERIA_FIDELITY].max_retrieval_tool_calls, 4)
        self.assertEqual(cases[RECRUITMENT_SOURCING_SOURCE_FALLBACK].max_retrieval_tool_calls, 6)
        self.assertEqual(cases[RECRUITMENT_SOURCING_DEDUPE_LEDGER].max_retrieval_tool_calls, 0)
        self.assertEqual(cases[RECRUITMENT_SOURCING_PARTIAL_VERIFICATION].max_retrieval_tool_calls, 1)
        self.assertIn("mcp_brightdata_search_engine", RECRUITMENT_RETRIEVAL_TOOL_NAMES)
        self.assertIn("mcp_brightdata_web_data_linkedin_people_search", RECRUITMENT_RETRIEVAL_TOOL_NAMES)
        self.assertNotIn("search_tools", RECRUITMENT_RETRIEVAL_TOOL_NAMES)
        self.assertNotIn("sqlite_batch", RECRUITMENT_RETRIEVAL_TOOL_NAMES)

    def test_retrieval_signatures_detect_duplicates_without_prescribing_queries(self):
        first = SimpleNamespace(
            tool_name="mcp_brightdata_web_data_linkedin_people_search",
            tool_params={
                "keywords": ["project manager", "assistant project manager"],
                "location": "United States",
                "will_continue_work": True,
            },
        )
        reordered_duplicate = SimpleNamespace(
            tool_name="mcp_brightdata_web_data_linkedin_people_search",
            tool_params={
                "location": "United States",
                "keywords": ["assistant project manager", "project manager"],
                "will_continue_work": False,
            },
        )
        distinct_query = SimpleNamespace(
            tool_name="mcp_brightdata_web_data_linkedin_people_search",
            tool_params={"keywords": ["legal recruiter"]},
        )

        self.assertEqual(_retrieval_call_signature(first), _retrieval_call_signature(reordered_duplicate))
        self.assertEqual(
            _duplicate_retrieval_signatures([first, reordered_duplicate, distinct_query]),
            (_retrieval_call_signature(first),),
        )

    def test_expected_tool_result_rejects_failed_warning_and_pending_outcomes(self):
        self.assertTrue(
            _tool_call_has_usable_result(
                SimpleNamespace(status="complete", result='{"status":"partial","remaining_work":13}')
            )
        )
        for status, result in (
            ("pending", '{"status":"success"}'),
            ("error", '{"status":"success"}'),
            ("complete", '{"status":"warning"}'),
            ("complete", '{"status":"error"}'),
            ("complete", '{"status":"ok","error":"bad source"}'),
        ):
            with self.subTest(status=status, result=result):
                self.assertFalse(
                    _tool_call_has_usable_result(SimpleNamespace(status=status, result=result))
                )

    def test_dedupe_case_uses_ledger_not_new_sourcing(self):
        case = next(case for case in RECRUITMENT_SOURCING_CASES if case.slug == RECRUITMENT_SOURCING_DEDUPE_LEDGER)

        self.assertEqual(case.expected_tool_names, ("sqlite_batch",))
        self.assertEqual(case.mock_config, {})
        self.assertEqual({row[0] for row in DEDUPE_QUEUE_ROWS}, {"Harper Nguyen", "Jordan Blake", "Riley Chen"})
        self.assertEqual({row[1] for row in DEDUPE_LEDGER_ROWS}, {"DUPLICATE", "REJECTED"})
        self.assertIn("mcp_brightdata_search_engine", case.forbidden_tool_names)

        valid = SimpleNamespace(
            tool_name="sqlite_batch",
            status="complete",
            tool_params={
                "sql": (
                    "SELECT q.candidate_name, COALESCE(l.status, 'NEW') AS status "
                    "FROM candidate_queue q LEFT JOIN candidate_ledger l USING(profile_url)"
                )
            },
            result="Harper Nguyen NEW; Jordan Blake DUPLICATE; Riley Chen REJECTED",
        )
        trivial = SimpleNamespace(
            tool_name="sqlite_batch",
            status="complete",
            tool_params={"sql": "SELECT 1"},
            result="Harper Nguyen NEW; Jordan Blake DUPLICATE; Riley Chen REJECTED",
        )
        self.assertTrue(_ledger_query_is_substantive(valid))
        self.assertFalse(_ledger_query_is_substantive(trivial))

        keyword_only_answer = "Harper Nguyen is new. Jordan Blake. Riley Chen. Duplicate. Rejected."
        mapped_answer = (
            "Harper Nguyen — NEW\n"
            "Jordan Blake — DUPLICATE\n"
            "Riley Chen — REJECTED"
        )
        self.assertFalse(
            _contains_proximate_terms(keyword_only_answer, ("Jordan Blake",), ("duplicate",))
        )
        self.assertFalse(
            _contains_proximate_terms(keyword_only_answer, ("Riley Chen",), ("rejected",))
        )
        for anchors, context in case.required_proximate_response_terms:
            self.assertTrue(_contains_proximate_terms(mapped_answer, anchors, context))

    def test_disqualified_candidate_accepts_negative_marker_before_full_explanation(self):
        body = "| Dana Lee | Charlotte, NC | ❌ Geography |\nExcluded: Dana Lee — outside approved geography."

        self.assertFalse(
            _has_anchor_without_proximate_context(
                body,
                ("Dana Lee",),
                ("excluded", "outside", "not qualified", "ineligible"),
            )
        )

    def test_partial_verification_case_preserves_remaining_work(self):
        case = next(case for case in RECRUITMENT_SOURCING_CASES if case.slug == RECRUITMENT_SOURCING_PARTIAL_VERIFICATION)
        result = case.mock_config["eval_verify_candidate_batch"]

        self.assertEqual(case.expected_tool_names, ("eval_verify_candidate_batch",))
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["remaining_work"], 13)
        self.assertIn("next_cursor", result)
        self.assertIn(("partial", "remaining", "source limitation", "could not verify"), case.response_term_groups)

        description = EVAL_SYNTHETIC_TOOL_DEFINITIONS["eval_verify_candidate_batch"]["description"]
        self.assertIn("owns and returns the queued candidate records", description)
        self.assertIn("instead of looking for the queue elsewhere", description)
        self.assertIn("instead of repeating the same batch", description)
        self.assertIn("do not rerun the current batch", result["next_action"])
        self.assertTrue(case.require_partial_resume_state)
        self.assertIn(
            "verify_partial_resume_state",
            [task.name for task in ScenarioRegistry.get(case.slug).tasks],
        )

    def test_partial_resume_state_finds_normal_tables_but_ignores_internal_tables(self):
        with tempfile.NamedTemporaryFile(suffix=".sqlite3") as database, patch(
            "api.evals.scenarios.recruitment_sourcing.agent_sqlite_db",
            return_value=nullcontext(database.name),
        ):
            connection = sqlite3.connect(database.name)
            connection.executescript(
                "CREATE TABLE resume_state (remaining_work INTEGER, next_cursor TEXT);"
                "INSERT INTO resume_state VALUES (13, 'candidate-offset-2');"
                "CREATE TABLE __internal_resume (remaining_work INTEGER, next_cursor TEXT);"
                "INSERT INTO __internal_resume VALUES (13, 'candidate-offset-2');"
            )
            connection.close()

            self.assertEqual(
                _partial_resume_state_tables("agent-id", cursor="candidate-offset-2", remaining_work=13),
                ["resume_state"],
            )

    def test_partial_response_requires_positive_exact_remaining_work(self):
        self.assertTrue(
            _partial_response_has_correct_polarity(
                "Partial verification: 2 of 15 verified; 13 candidates remain due to a source limitation.",
                remaining_work=13,
            )
        )
        self.assertFalse(
            _partial_response_has_correct_polarity(
                "This is not a partial result and no work is remaining.",
                remaining_work=13,
            )
        )
        self.assertFalse(
            _partial_response_has_correct_polarity(
                "Partial source limitation; 3 candidates remain.",
                remaining_work=13,
            )
        )
        self.assertTrue(
            _partial_response_has_correct_polarity(
                "Verified 2 of 15. Limitation: source pages are blocking verification of the remaining 13 candidates.",
                remaining_work=13,
            )
        )
        self.assertTrue(
            _partial_response_has_correct_polarity(
                "Partial. ⚠️ Limitation: source pages blocked verification. Remaining: 13 candidates still queued.",
                remaining_work=13,
            )
        )
