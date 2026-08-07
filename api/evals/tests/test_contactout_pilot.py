from django.test import SimpleTestCase, tag

import api.evals.loader  # noqa: F401 - registers scenarios and suites
from api.agent.system_skills.defaults import CONTACTOUT_SYSTEM_SKILL, RECRUITMENT_SOURCING_SYSTEM_SKILL
from api.agent.tools.brightdata import BRIGHTDATA_LINKEDIN_PERSON_PROFILE_TOOL_NAME
from api.agent.tools.contactout import (
    CONTACTOUT_TOOL_NAME,
    ENRICH_LINKEDIN_PROFILE,
    SEARCH_COMPANIES,
    SEARCH_PEOPLE,
)
from api.evals.registry import ScenarioRegistry
from api.evals.scenarios.contactout_pilot import (
    CONTACTOUT_BRIGHTDATA_FALLBACK,
    CONTACTOUT_COMPANY_SEARCH,
    CONTACTOUT_EXPLICIT_CONTACT_REVEAL,
    CONTACTOUT_LINKEDIN_PROFILE_ONLY,
    CONTACTOUT_PEOPLE_SEARCH_PROFILE_ONLY,
    CONTACTOUT_PILOT_CASES,
    CONTACTOUT_PILOT_SCENARIO_SLUGS,
    CONTACTOUT_PILOT_SUITE_SLUG,
)
from api.evals.suites import SuiteRegistry


@tag("batch_contactout", "eval_sim")
class ContactOutPilotScenarioTests(SimpleTestCase):
    def test_suite_registers_five_real_harness_scenarios(self):
        suite = SuiteRegistry.get(CONTACTOUT_PILOT_SUITE_SLUG)

        self.assertIsNotNone(suite)
        self.assertEqual(tuple(suite.scenario_slugs), CONTACTOUT_PILOT_SCENARIO_SLUGS)
        self.assertEqual(len(suite.scenario_slugs), 5)
        for slug in CONTACTOUT_PILOT_SCENARIO_SLUGS:
            metadata = ScenarioRegistry.list_all()[slug].get_metadata()
            self.assertEqual(metadata.category, "contactout_pilot")
            self.assertEqual(metadata.area, "system_skills")
            self.assertIn("real_harness", metadata.tags)

    def test_cases_cover_people_profile_contacts_company_and_fallback(self):
        self.assertEqual(
            {case.slug for case in CONTACTOUT_PILOT_CASES},
            {
                CONTACTOUT_PEOPLE_SEARCH_PROFILE_ONLY,
                CONTACTOUT_LINKEDIN_PROFILE_ONLY,
                CONTACTOUT_EXPLICIT_CONTACT_REVEAL,
                CONTACTOUT_COMPANY_SEARCH,
                CONTACTOUT_BRIGHTDATA_FALLBACK,
            },
        )
        expected_operations = {
            CONTACTOUT_PEOPLE_SEARCH_PROFILE_ONLY: (SEARCH_PEOPLE,),
            CONTACTOUT_LINKEDIN_PROFILE_ONLY: (ENRICH_LINKEDIN_PROFILE,),
            CONTACTOUT_EXPLICIT_CONTACT_REVEAL: (SEARCH_PEOPLE,),
            CONTACTOUT_COMPANY_SEARCH: (SEARCH_COMPANIES,),
            CONTACTOUT_BRIGHTDATA_FALLBACK: (ENRICH_LINKEDIN_PROFILE,),
        }
        self.assertEqual(
            {case.slug: case.expected_operations for case in CONTACTOUT_PILOT_CASES},
            expected_operations,
        )

    def test_only_explicit_contact_case_allows_reveal(self):
        revealing = [case for case in CONTACTOUT_PILOT_CASES if case.explicit_contact_reveal]

        self.assertEqual([case.slug for case in revealing], [CONTACTOUT_EXPLICIT_CONTACT_REVEAL])
        self.assertEqual(set(revealing[0].expected_contact_types), {"work_email", "phone"})
        for case in CONTACTOUT_PILOT_CASES:
            if case.slug != CONTACTOUT_EXPLICIT_CONTACT_REVEAL:
                self.assertFalse(case.explicit_contact_reveal)

    def test_brightdata_is_allowed_only_in_the_error_fallback_case(self):
        fallback = next(case for case in CONTACTOUT_PILOT_CASES if case.slug == CONTACTOUT_BRIGHTDATA_FALLBACK)

        self.assertTrue(fallback.expect_brightdata_fallback)
        self.assertEqual(fallback.mock_config[CONTACTOUT_TOOL_NAME]["status"], "error")
        self.assertIn(BRIGHTDATA_LINKEDIN_PERSON_PROFILE_TOOL_NAME, fallback.mock_config)
        self.assertIn(BRIGHTDATA_LINKEDIN_PERSON_PROFILE_TOOL_NAME, fallback.eval_stop_policy()["allowed_tool_names"])
        for case in CONTACTOUT_PILOT_CASES:
            if case.slug != CONTACTOUT_BRIGHTDATA_FALLBACK:
                self.assertFalse(case.expect_brightdata_fallback)
                self.assertNotIn(
                    BRIGHTDATA_LINKEDIN_PERSON_PROFILE_TOOL_NAME,
                    case.eval_stop_policy()["allowed_tool_names"],
                )

    def test_skill_contract_prefers_native_contactout_and_protects_contact_credits(self):
        instructions = CONTACTOUT_SYSTEM_SKILL.prompt_instructions
        recruitment_instructions = RECRUITMENT_SOURCING_SYSTEM_SKILL.prompt_instructions

        self.assertIn("never a ContactOut MCP tool", instructions)
        self.assertIn("include_contact_info=false", instructions)
        self.assertIn("does not itself authorize revealing contact data", instructions)
        self.assertIn("prefer ContactOut over BrightData", instructions)
        self.assertIn("Do not call both routinely", instructions)
        self.assertIn("use it first", recruitment_instructions)
        self.assertIn("do not run routine duplicate lookups", recruitment_instructions)
