from django.test import SimpleTestCase, tag

import api.evals.loader  # noqa: F401 - registers scenarios and suites
from api.agent.core.event_processing import _resolve_eval_mock_result
from api.agent.system_skills.defaults import RECRUITMENT_SOURCING_SYSTEM_SKILL
from api.evals.registry import ScenarioRegistry
from api.evals.scenarios.recruitment_sourcing import (
    RECRUITMENT_SOURCING_CASES,
    RECRUITMENT_SOURCING_CRITERIA_FIDELITY,
    RECRUITMENT_SOURCING_DEDUPE_LEDGER,
    RECRUITMENT_SOURCING_INTAKE_GATES_SOURCING,
    RECRUITMENT_SOURCING_PARTIAL_VERIFICATION,
    RECRUITMENT_SOURCING_SCENARIO_SLUGS,
    RECRUITMENT_SOURCING_SKILL_DISCOVERY,
    RECRUITMENT_SOURCING_SOURCE_FALLBACK,
    RECRUITMENT_SOURCING_SUITE_SLUG,
    _contains_unseparated_decoy,
)
from api.evals.suites import SuiteRegistry


@tag("batch_recruitment_sourcing", "eval_sim")
class RecruitmentSourcingScenarioTests(SimpleTestCase):
    def test_recruitment_sourcing_suite_contains_six_scenarios(self):
        suite = SuiteRegistry.get(RECRUITMENT_SOURCING_SUITE_SLUG)

        self.assertIsNotNone(suite)
        self.assertEqual(tuple(suite.scenario_slugs), RECRUITMENT_SOURCING_SCENARIO_SLUGS)
        self.assertEqual(len(suite.scenario_slugs), 6)

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

    def test_skill_is_provider_neutral_and_preserves_requirements(self):
        instructions = RECRUITMENT_SOURCING_SYSTEM_SKILL.prompt_instructions

        self.assertIn("finding candidates worth recruiter review", instructions)
        self.assertIn("hard requirements", instructions)
        self.assertIn("preferred signals", instructions)
        self.assertIn("Choose sources based on the tools and permissions actually available", instructions)
        self.assertIn("If a specific source is unavailable or blocked", instructions)
        self.assertIn("Dedupe by profile URL first", instructions)
        self.assertIn("Quality and criteria fidelity beat volume", instructions)
        self.assertIn("equivalent constraints as gates", instructions)
        self.assertIn("Return fewer or zero qualified candidates", instructions)
        self.assertIn("never label them as qualified", instructions)
        self.assertIn("Do not treat phrases like 'start today'", instructions)
        self.assertIn("Those phrases only express urgency", instructions)
        self.assertIn("the failed question attempt is not a user answer", instructions)
        self.assertNotIn("Crisp Recruit", instructions)

    def test_cases_cover_core_recruitment_sourcing_behaviors(self):
        self.assertEqual(
            {case.slug for case in RECRUITMENT_SOURCING_CASES},
            {
                RECRUITMENT_SOURCING_SKILL_DISCOVERY,
                RECRUITMENT_SOURCING_INTAKE_GATES_SOURCING,
                RECRUITMENT_SOURCING_CRITERIA_FIDELITY,
                RECRUITMENT_SOURCING_SOURCE_FALLBACK,
                RECRUITMENT_SOURCING_DEDUPE_LEDGER,
                RECRUITMENT_SOURCING_PARTIAL_VERIFICATION,
            },
        )

    def test_discovery_case_starts_without_skill_and_stops_after_search(self):
        case = next(case for case in RECRUITMENT_SOURCING_CASES if case.slug == RECRUITMENT_SOURCING_SKILL_DISCOVERY)
        policy = case.eval_stop_policy()

        self.assertFalse(case.pre_enable_system_skill)
        self.assertTrue(case.expect_skill_discovery)
        self.assertEqual(case.expected_tool_names, ("search_tools",))
        self.assertEqual(policy["stop_on_tool_names_after_execution"], ["search_tools"])

    def test_intake_case_forbids_sourcing_tools_before_requirements(self):
        case = next(case for case in RECRUITMENT_SOURCING_CASES if case.slug == RECRUITMENT_SOURCING_INTAKE_GATES_SOURCING)
        policy = case.eval_stop_policy()

        self.assertEqual(case.expected_tool_names, ())
        self.assertIn("search_tools", policy["stop_on_tool_names"])
        self.assertNotIn("search_tools", policy["allowed_tool_names"])
        self.assertIn("request_human_input", policy["allowed_tool_names"])
        self.assertTrue(policy["stop_on_human_input_request"])

    def test_intake_response_accepts_must_have_skills_wording(self):
        case = next(case for case in RECRUITMENT_SOURCING_CASES if case.slug == RECRUITMENT_SOURCING_INTAKE_GATES_SOURCING)

        self.assertIn("must-have skills", case.response_term_groups[0])

    def test_criteria_case_includes_decoys_and_requires_exclusion_language(self):
        case = next(case for case in RECRUITMENT_SOURCING_CASES if case.slug == RECRUITMENT_SOURCING_CRITERIA_FIDELITY)
        people_result = case.mock_config["mcp_brightdata_web_data_linkedin_people_search"]["content"]
        apollo_result = case.mock_config["apollo_io-search-contacts"]["content"]
        names = {item["name"] for item in people_result["items"]}
        apollo_names = {item["name"] for item in apollo_result["contacts"]}

        self.assertEqual(case.expected_tool_names, ("mcp_brightdata_web_data_linkedin_people_search",))
        self.assertEqual(
            case.accepted_tool_alternatives["mcp_brightdata_web_data_linkedin_people_search"],
            ("apollo_io-search-contacts",),
        )
        self.assertIn("Mina Patel", names)
        self.assertIn("Evan Brooks", names)
        self.assertIn("Dana Lee", names)
        self.assertEqual(names, apollo_names)
        self.assertIn(("2 qualified", "two qualified", "only 2", "only two", "two candidates meet"), case.response_term_groups)
        self.assertEqual(case.forbidden_response_terms, ())
        self.assertEqual(case.required_proximate_response_terms, ())
        self.assertEqual(
            case.excluded_or_separated_response_terms,
            (
                (
                    ("Evan Brooks", "evan-brooks-eval"),
                    ("excluded", "not qualified", "does not meet", "fails"),
                ),
                (
                    ("Dana Lee", "dana-lee-eval"),
                    ("outside approved geography", "outside geography", "not approved geography", "outside approved"),
                ),
            ),
        )

    def test_decoy_assertion_allows_omission_or_clear_separation(self):
        anchors = ("Evan Brooks", "evan-brooks-eval")
        failed_gate_terms = ("excluded", "not qualified", "does not meet", "fails")

        self.assertFalse(_contains_unseparated_decoy("Only Mina and Priya qualify.", anchors, failed_gate_terms))
        self.assertFalse(
            _contains_unseparated_decoy(
                "Near matches: Evan Brooks is excluded because his Estimator title fails the title gate.",
                anchors,
                failed_gate_terms,
            )
        )
        self.assertTrue(
            _contains_unseparated_decoy(
                "Qualified candidates include Mina Patel, Priya Shah, and Evan Brooks.",
                anchors,
                failed_gate_terms,
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
        self.assertIn("mcp_brightdata_linkedin_person_profile", case.allowed_extra_tool_names)
        self.assertIn("mcp_brightdata_web_data_linkedin_job_listings", case.allowed_extra_tool_names)
        self.assertIn("mcp_brightdata_scrape_as_markdown", case.allowed_extra_tool_names)
        self.assertIn("http_request", case.allowed_extra_tool_names)
        self.assertIn("mcp_brightdata_web_data_linkedin_people_search", case.mock_config)
        self.assertIn("mcp_brightdata_web_data_linkedin_company_profile", case.mock_config)
        self.assertIn("mcp_brightdata_web_data_linkedin_person_profile", case.mock_config)
        self.assertIn("mcp_brightdata_linkedin_person_profile", case.mock_config)
        self.assertIn("mcp_brightdata_web_data_linkedin_job_listings", case.mock_config)
        self.assertIn("mcp_brightdata_scrape_as_markdown", case.mock_config)
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

    def test_dedupe_case_uses_ledger_not_new_sourcing(self):
        case = next(case for case in RECRUITMENT_SOURCING_CASES if case.slug == RECRUITMENT_SOURCING_DEDUPE_LEDGER)
        statuses = {
            row["candidate_name"]: row["status"]
            for row in case.mock_config["sqlite_batch"]["results"][0]["result"]
        }

        self.assertEqual(case.expected_tool_names, ("sqlite_batch",))
        self.assertEqual(statuses["Harper Nguyen"], "NEW")
        self.assertEqual(statuses["Jordan Blake"], "DUPLICATE")
        self.assertEqual(statuses["Riley Chen"], "REJECTED")
        self.assertIn("mcp_brightdata_search_engine", case.forbidden_tool_names)

    def test_partial_verification_case_preserves_remaining_work(self):
        case = next(case for case in RECRUITMENT_SOURCING_CASES if case.slug == RECRUITMENT_SOURCING_PARTIAL_VERIFICATION)
        result = case.mock_config["eval_verify_candidate_batch"]

        self.assertEqual(case.expected_tool_names, ("eval_verify_candidate_batch",))
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["remaining_work"], 13)
        self.assertIn("next_cursor", result)
        self.assertIn(("partial", "remaining", "source limitation", "could not verify"), case.response_term_groups)
