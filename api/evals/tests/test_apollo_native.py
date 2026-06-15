import os
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, tag

import api.evals.loader  # noqa: F401 - registers scenarios and suites
from api.agent.system_skills.native_api_cookbooks import render_native_api_cookbook
from api.evals.scenarios.apollo_native import (
    APOLLO_NATIVE_CASES,
    APOLLO_NATIVE_CREATE_CONTACT,
    APOLLO_NATIVE_MISSING_CONNECTION,
    APOLLO_NATIVE_PEOPLE_SEARCH,
    APOLLO_NATIVE_SCENARIO_SLUGS,
    APOLLO_NATIVE_SUITE_SLUG,
    FORBIDDEN_APOLLO_DISCOVERY_TOOL_NAMES,
    ApolloHttpRequestExpectation,
    _call_matches_expectation,
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

    def test_apollo_cookbook_warns_about_obsolete_endpoints_and_bulk_limits(self):
        cookbook = render_native_api_cookbook("apollo")

        self.assertIn("`/mixed_people/api_search`", cookbook)
        self.assertIn("do not use `/mixed_people` or `/mixed_people/search`", cookbook)
        self.assertIn("`GET /email_accounts`", cookbook)
        self.assertIn("Do not call `/email_accounts/list`", cookbook)
        self.assertIn("`/usage_stats/api_usage_stats`", cookbook)
        self.assertIn("`/credit_usage`", cookbook)
        self.assertIn("`/auth/credit_usage_stats`", cookbook)
        self.assertIn("at most 10 person objects", cookbook)
        self.assertIn("do not retry the same malformed batch", cookbook)

    def test_eval_stop_policy_allows_sqlite_batch_for_result_shaping(self):
        scenario = ScenarioRegistry.get(APOLLO_NATIVE_SCENARIO_SLUGS[0])
        policy = scenario._eval_stop_policy()

        self.assertIn("sqlite_batch", policy["allowed_tool_names"])
        self.assertIn("http_request", policy["allowed_tool_names"])
        self.assertIn("send_chat_message", policy["allowed_tool_names"])
        for tool_name in FORBIDDEN_APOLLO_DISCOVERY_TOOL_NAMES:
            self.assertIn(tool_name, policy["stop_on_tool_names"])

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

    def test_missing_connection_eval_does_not_seed_integration_secret(self):
        scenario = ScenarioRegistry.get(APOLLO_NATIVE_MISSING_CONNECTION)

        scenario._prepare_agent(str(self.agent.id))

        self.assertIsNone(get_native_integration_secret("apollo", None, self.org))
