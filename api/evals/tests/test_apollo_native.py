import os
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, tag

import api.evals.loader  # noqa: F401 - registers scenarios and suites
from api.agent.system_skills.defaults import _apollo_native_prompt_instructions
from api.agent.system_skills.native_api_cookbooks import render_native_api_cookbook
from api.evals.scenarios.apollo_native import (
    APOLLO_NATIVE_CASES,
    APOLLO_NATIVE_CREATE_CONTACT,
    APOLLO_NATIVE_MISSING_CONNECTION,
    APOLLO_NATIVE_PEOPLE_SEARCH,
    APOLLO_NATIVE_PERSON_ENRICHMENT,
    APOLLO_NATIVE_SCENARIO_SLUGS,
    APOLLO_NATIVE_SUITE_SLUG,
    FORBIDDEN_APOLLO_DISCOVERY_TOOL_NAMES,
    ApolloHttpRequestExpectation,
    _call_matches_expectation,
)
from api.evals.scenarios.native_http import (
    false_readiness_claims,
    false_readiness_claims_before_first_http,
    validate_http_call_set,
    validate_http_attempt_efficiency,
)
from api.evals.registry import ScenarioRegistry
from api.evals.suites import SuiteRegistry
from api.models import BrowserUseAgent, GlobalSecret, Organization, OrganizationMembership, PersistentAgent
from api.services.native_integrations import get_native_integration_secret, load_native_integration_credentials


User = get_user_model()


@tag("eval_sim")
class ApolloNativeScenarioTests(SimpleTestCase):
    def test_apollo_native_suite_contains_five_scenarios(self):
        suite = SuiteRegistry.get(APOLLO_NATIVE_SUITE_SLUG)

        self.assertIsNotNone(suite)
        self.assertEqual(tuple(suite.scenario_slugs), APOLLO_NATIVE_SCENARIO_SLUGS)
        self.assertEqual(len(suite.scenario_slugs), 5)

    def test_generated_scenarios_have_expected_metadata(self):
        registered = ScenarioRegistry.list_all()

        for slug in APOLLO_NATIVE_SCENARIO_SLUGS:
            scenario = registered[slug]
            metadata = scenario.get_metadata()
            self.assertEqual(metadata.category, "apollo_native")
            self.assertEqual(metadata.area, "system_skills")
            self.assertEqual(metadata.expected_runtime, "short")
            self.assertEqual(metadata.cost_class, "low")
            self.assertIn("apollo_native", metadata.tags)
            self.assertIn("system_skill", metadata.tags)
            self.assertIn("http_request", metadata.tags)

    def test_cases_mock_only_http_request_for_apollo_api_calls(self):
        for case in APOLLO_NATIVE_CASES:
            self.assertEqual(set(case.mock_config()), {"http_request"})
            mock = case.mock_config()["http_request"]
            self.assertTrue(mock["rules"])
            self.assertIn("default", mock)
            for rule in mock["rules"]:
                self.assertIn("url_contains", rule)
                self.assertIn("result", rule)

    def test_cases_expect_http_request_not_legacy_apollo_tools_or_enablement(self):
        for case in APOLLO_NATIVE_CASES:
            self.assertTrue(case.expected_http_requests)
            for expectation in case.expected_http_requests:
                self.assertEqual(expectation.name.startswith("apollo_io-"), False)
                self.assertTrue(expectation.url_terms)
                self.assertIn(expectation.method, {"GET", "POST", "PATCH", "PUT", "DELETE"})

            prompt_and_description = f"{case.prompt} {case.description}"
            self.assertNotIn("apollo_io-", prompt_and_description)
            for tool_name in FORBIDDEN_APOLLO_DISCOVERY_TOOL_NAMES:
                self.assertNotIn(tool_name, prompt_and_description)

    def test_people_search_prompt_is_bounded_to_returned_matches(self):
        case = next(case for case in APOLLO_NATIVE_CASES if case.slug == APOLLO_NATIVE_PEOPLE_SEARCH)

        self.assertIn("first page", case.prompt.lower())
        self.assertIn("Return the matches Apollo returns.", case.prompt)
        self.assertNotIn("top matches", case.prompt.lower())
        self.assertIn("contacts who live in Boston", case.prompt)

    def test_missing_connection_accepts_agent_specific_integration_settings(self):
        case = next(case for case in APOLLO_NATIVE_CASES if case.slug == APOLLO_NATIVE_MISSING_CONNECTION)

        self.assertIn("agent settings", case.response_term_groups[1])

    def test_apollo_cookbook_warns_about_obsolete_endpoints_and_bulk_limits(self):
        cookbook = render_native_api_cookbook("apollo")

        self.assertIn("https://api.apollo.io/api/v1/mixed_people/api_search", cookbook)
        self.assertIn("Never use `/mixed_people` or `/mixed_people/search`", cookbook)
        self.assertIn("`GET /email_accounts`", cookbook)
        self.assertIn("Do not call `/email_accounts/list`", cookbook)
        self.assertIn("`/usage_stats/api_usage_stats`", cookbook)
        self.assertIn("`/credit_usage`", cookbook)
        self.assertIn("`/auth/credit_usage_stats`", cookbook)
        self.assertIn("`/people/match` for one person", cookbook)
        self.assertIn("`/people/bulk_match` only for 2-10 people", cookbook)
        self.assertIn("do not retry the same malformed batch", cookbook)
        self.assertIn("pass the returned person `id`", cookbook)
        self.assertIn("do not invent legacy keys such as `personId`", cookbook)
        self.assertIn("400 or 422", cookbook)
        self.assertIn("row-level misses", cookbook)
        self.assertIn("`person_locations` (where the person lives)", cookbook)
        self.assertIn("`organization_locations` (employer HQ)", cookbook)
        self.assertIn("Use only the location dimension requested", cookbook)
        self.assertIn("string `q_keywords`", cookbook)
        self.assertIn("Never invent singular/industry keys", cookbook)
        self.assertIn("comma-join arrays", cookbook)

    @patch("api.agent.system_skills.defaults._native_integration_connected", return_value=True)
    def test_apollo_prompt_classifies_auth_plan_and_validation_errors(self, _mock_connected):
        instructions = _apollo_native_prompt_instructions(SimpleNamespace())

        self.assertIn("On any 401/not-connected", instructions)
        self.assertIn("make no other Apollo or discovery call", instructions)
        self.assertIn("returned reconnect guidance", instructions)
        self.assertIn("For 403, stop retrying", instructions)
        self.assertIn("master API key", instructions)
        self.assertIn("For 422, repair the request shape", instructions)
        self.assertIn("row-level miss", instructions)

    def test_eval_stop_policy_allows_sqlite_batch_for_result_shaping(self):
        scenario = ScenarioRegistry.get(APOLLO_NATIVE_SCENARIO_SLUGS[0])
        policy = scenario._eval_stop_policy()

        self.assertIn("sqlite_batch", policy["allowed_tool_names"])
        self.assertIn("http_request", policy["allowed_tool_names"])
        self.assertIn("send_chat_message", policy["allowed_tool_names"])
        for tool_name in FORBIDDEN_APOLLO_DISCOVERY_TOOL_NAMES:
            self.assertIn(tool_name, policy["stop_on_tool_names"])

    def test_only_missing_connection_scenario_scores_false_readiness_claims(self):
        missing = ScenarioRegistry.get(APOLLO_NATIVE_MISSING_CONNECTION)
        normal = ScenarioRegistry.get(APOLLO_NATIVE_PEOPLE_SEARCH)

        self.assertIn("verify_no_false_connection_claim", [task.name for task in missing.tasks])
        self.assertNotIn("verify_no_false_connection_claim", [task.name for task in normal.tasks])

    def test_false_readiness_check_inspects_skipped_pre_call_chat(self):
        false_claim = SimpleNamespace(
            tool_name="send_chat_message",
            tool_params={"body": "Apollo is connected and ready to go!"},
        )
        request = SimpleNamespace(tool_name="http_request", tool_params={})
        later_claim = SimpleNamespace(
            tool_name="send_chat_message",
            tool_params={"body": "Apollo appears connected."},
        )

        self.assertEqual(
            false_readiness_claims_before_first_http([false_claim, request, later_claim], "Apollo"),
            [false_claim],
        )
        self.assertEqual(
            false_readiness_claims([false_claim, request, later_claim], "Apollo"),
            [false_claim, later_claim],
        )

    def test_false_readiness_check_allows_neutral_connection_check(self):
        neutral = SimpleNamespace(
            tool_name="send_chat_message",
            tool_params={"body": "Let me check whether Apollo is connected before searching."},
        )
        request = SimpleNamespace(tool_name="http_request", tool_params={})

        self.assertEqual(false_readiness_claims_before_first_http([neutral, request], "Apollo"), [])

    def test_false_readiness_check_allows_explicit_status_discrepancy(self):
        discrepancy = SimpleNamespace(
            tool_name="send_chat_message",
            tool_params={
                "body": 'The skill config shows "Apollo is connected" but the actual API call disagrees: 401, not connected.'
            },
        )
        unsupported = SimpleNamespace(
            tool_name="send_chat_message",
            tool_params={"body": "Apollo is connected and ready, but I have not called it yet."},
        )

        self.assertEqual(false_readiness_claims([discrepancy], "Apollo"), [])
        self.assertEqual(false_readiness_claims([unsupported], "Apollo"), [unsupported])

    def test_expected_http_request_requires_completed_tool_call(self):
        expectation = ApolloHttpRequestExpectation(
            name="people_search",
            url_terms=("api.apollo.io/api/v1/mixed_people/api_search",),
        )
        pending_call = SimpleNamespace(
            status="pending",
            tool_params={
                "method": "POST",
                "url": "https://api.apollo.io/api/v1/mixed_people/api_search",
            },
        )
        complete_call = SimpleNamespace(
            status="complete",
            tool_params={
                "method": "POST",
                "url": "https://api.apollo.io/api/v1/mixed_people/api_search",
            },
        )

        self.assertFalse(_call_matches_expectation(pending_call, expectation))
        self.assertTrue(_call_matches_expectation(complete_call, expectation))

    def test_missing_connection_expected_http_request_accepts_error_tool_call(self):
        expectation = ApolloHttpRequestExpectation(
            name="apollo_search_attempt",
            url_terms=("api.apollo.io/api/v1/mixed_people/api_search",),
            allowed_statuses=("error",),
        )
        error_call = SimpleNamespace(
            status="error",
            tool_params={
                "method": "POST",
                "url": "https://api.apollo.io/api/v1/mixed_people/api_search",
            },
        )
        complete_call = SimpleNamespace(
            status="complete",
            tool_params={
                "method": "POST",
                "url": "https://api.apollo.io/api/v1/mixed_people/api_search",
            },
        )

        self.assertTrue(_call_matches_expectation(error_call, expectation))
        self.assertFalse(_call_matches_expectation(complete_call, expectation))

    def test_expected_http_request_can_require_body_terms(self):
        create_case = next(case for case in APOLLO_NATIVE_CASES if case.slug == APOLLO_NATIVE_CREATE_CONTACT)
        expectation = create_case.expected_http_requests[0]
        missing_body_call = SimpleNamespace(
            status="complete",
            tool_params={
                "method": "POST",
                "url": "https://api.apollo.io/api/v1/contacts",
                "body": {"first_name": "Alex"},
            },
        )
        complete_call = SimpleNamespace(
            status="complete",
            tool_params={
                "method": "POST",
                "url": "https://api.apollo.io/api/v1/contacts",
                "body": {
                    "first_name": "Alex",
                    "last_name": "Morgan",
                    "email": "alex@example.test",
                },
            },
        )

        self.assertFalse(_call_matches_expectation(missing_body_call, expectation))
        self.assertTrue(_call_matches_expectation(complete_call, expectation))

    def test_contact_write_requires_exact_structured_identity_fields(self):
        create_case = next(case for case in APOLLO_NATIVE_CASES if case.slug == APOLLO_NATIVE_CREATE_CONTACT)
        expectation = create_case.expected_http_requests[0]
        misleading_call = SimpleNamespace(
            status="complete",
            tool_params={
                "method": "POST",
                "url": "https://api.apollo.io/api/v1/contacts",
                "body": {
                    "first_name": "Wrong",
                    "last_name": "Person",
                    "email": "wrong@example.test",
                    "notes": "Alex Morgan alex@example.test",
                },
            },
        )

        self.assertFalse(_call_matches_expectation(misleading_call, expectation))

    def test_people_search_requires_structured_page_title_and_location(self):
        case = next(case for case in APOLLO_NATIVE_CASES if case.slug == APOLLO_NATIVE_PEOPLE_SEARCH)
        expectation = case.expected_http_requests[0]
        correct = SimpleNamespace(
            status="complete",
            tool_params={
                "method": "POST",
                "url": "https://api.apollo.io/api/v1/mixed_people/api_search",
                "body": {
                    "page": 1,
                    "per_page": 10,
                    "person_titles": ["VP Sales"],
                    "person_locations": ["Boston"],
                    "q_keywords": "healthcare SaaS",
                },
            },
        )
        wrong = SimpleNamespace(
            status="complete",
            tool_params={
                **correct.tool_params,
                "body": {"page": 1, "notes": "VP Sales in Boston"},
            },
        )
        qualified_location = SimpleNamespace(
            status="complete",
            tool_params={
                **correct.tool_params,
                "body": {**correct.tool_params["body"], "person_locations": ["Boston, Massachusetts"]},
            },
        )
        wrong_location = SimpleNamespace(
            status="complete",
            tool_params={
                **correct.tool_params,
                "body": {**correct.tool_params["body"], "person_locations": ["Cambridge, Massachusetts"]},
            },
        )
        wrong_types = SimpleNamespace(
            status="complete",
            tool_params={
                **correct.tool_params,
                "body": {
                    **correct.tool_params["body"],
                    "person_locations": "Boston, Massachusetts",
                    "q_keywords": ["healthcare", "SaaS"],
                },
            },
        )
        invented_fields = SimpleNamespace(
            status="complete",
            tool_params={
                **correct.tool_params,
                "body": {
                    **correct.tool_params["body"],
                    "organization_location": "Boston",
                    "q_organization_industry": "healthcare, SaaS",
                },
            },
        )
        employer_hq_instead = SimpleNamespace(
            status="complete",
            tool_params={
                **correct.tool_params,
                "body": {
                    **correct.tool_params["body"],
                    "person_locations": [],
                    "organization_locations": ["Boston"],
                },
            },
        )

        self.assertTrue(_call_matches_expectation(correct, expectation))
        self.assertTrue(_call_matches_expectation(qualified_location, expectation))
        self.assertFalse(_call_matches_expectation(wrong, expectation))
        self.assertFalse(_call_matches_expectation(wrong_location, expectation))
        self.assertFalse(_call_matches_expectation(wrong_types, expectation))
        self.assertFalse(_call_matches_expectation(invented_fields, expectation))
        self.assertFalse(_call_matches_expectation(employer_hq_instead, expectation))

    def test_single_person_enrichment_rejects_bulk_first_attempt(self):
        case = next(case for case in APOLLO_NATIVE_CASES if case.slug == APOLLO_NATIVE_PERSON_ENRICHMENT)
        expectation = case.expected_http_requests[0]
        single = SimpleNamespace(
            status="complete",
            tool_params={
                "method": "POST",
                "url": "https://api.apollo.io/api/v1/people/match",
                "body": {"email": "pat@example.test"},
            },
        )
        bulk = SimpleNamespace(
            status="error",
            tool_params={
                "method": "POST",
                "url": "https://api.apollo.io/api/v1/people/bulk_match",
                "body": {"details": [{"email": "pat@example.test"}]},
            },
        )

        self.assertTrue(_call_matches_expectation(single, expectation))
        violations, unmatched = validate_http_call_set([bulk, single], (expectation,))
        self.assertTrue(violations)
        self.assertEqual(unmatched[0]["url"], bulk.tool_params["url"])

    def test_expected_post_plus_undeclared_post_fails_exact_call_set(self):
        expectation = ApolloHttpRequestExpectation(
            name="create_contact",
            url_terms=("api.apollo.io/api/v1/contacts",),
            body_terms=("alex",),
        )
        expected_call = SimpleNamespace(
            status="complete",
            tool_params={
                "method": "POST",
                "url": "https://api.apollo.io/api/v1/contacts",
                "body": {"first_name": "Alex"},
            },
        )
        extra_call = SimpleNamespace(
            status="complete",
            tool_params={
                "method": "POST",
                "url": "https://api.apollo.io/api/v1/contacts/bulk_create",
                "body": {"first_name": "Alex"},
            },
        )

        violations, unmatched = validate_http_call_set(
            [expected_call, extra_call],
            (expectation,),
        )

        self.assertTrue(violations)
        self.assertEqual(len(unmatched), 1)
        self.assertIn("bulk_create", unmatched[0]["url"])

        duplicate_violations, _ = validate_http_call_set(
            [expected_call, expected_call],
            (expectation,),
        )
        self.assertTrue(any("at most 1" in violation for violation in duplicate_violations))

    def test_skipped_http_attempt_is_an_efficiency_failure_not_an_execution(self):
        complete_call = SimpleNamespace(
            status="complete",
            tool_params={"method": "GET", "url": "https://api.apollo.io/api/v1/people/1"},
        )
        skipped_call = SimpleNamespace(
            status="skipped",
            tool_params={"method": "GET", "url": "https://api.apollo.io/api/v1/people/1"},
        )

        self.assertEqual(validate_http_attempt_efficiency([complete_call]), [])
        violations = validate_http_attempt_efficiency([complete_call, skipped_call])
        self.assertEqual(len(violations), 1)
        self.assertIn("runtime deduplication", violations[0])


@tag("eval_sim")
class ApolloNativeScenarioConnectionSeedTests(TestCase):
    def setUp(self):
        os.environ.setdefault("GOBII_ENCRYPTION_KEY", "test-key-for-native-eval-seeds")
        self.user = User.objects.create_user(
            username="apollo-native-eval",
            email="apollo-native-eval@example.com",
            password="password123",
        )
        self.org = Organization.objects.create(
            name="Apollo Native Eval Org",
            slug="apollo-native-eval-org",
            created_by=self.user,
        )
        OrganizationMembership.objects.create(
            org=self.org,
            user=self.user,
            role=OrganizationMembership.OrgRole.OWNER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        billing = self.org.billing
        billing.purchased_seats = 1
        billing.save(update_fields=["purchased_seats", "updated_at"])
        browser_agent = BrowserUseAgent.objects.create(user=self.user, name="Apollo Native Eval Browser")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            organization=self.org,
            name="Apollo Native Eval Agent",
            charter="native eval",
            browser_use_agent=browser_agent,
        )

    def test_connected_native_eval_seeds_integration_secret_before_prompt(self):
        scenario = ScenarioRegistry.get(APOLLO_NATIVE_PEOPLE_SEARCH)

        scenario._prepare_agent(str(self.agent.id))

        secret = get_native_integration_secret("apollo", None, self.org)
        self.assertIsNotNone(secret)
        self.assertEqual(secret.secret_type, GlobalSecret.SecretType.INTEGRATION)
        credentials = load_native_integration_credentials(secret)
        self.assertEqual(credentials["provider_key"], "apollo")
        self.assertIn("access_token", credentials)
        self.assertIn("scope", credentials)

    def test_missing_connection_eval_seeds_stale_connection_before_runtime_auth_failure(self):
        scenario = ScenarioRegistry.get(APOLLO_NATIVE_MISSING_CONNECTION)

        scenario._prepare_agent(str(self.agent.id))

        self.assertIsNotNone(get_native_integration_secret("apollo", None, self.org))
