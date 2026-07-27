from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from google.oauth2 import service_account as google_service_account

import api.evals.loader  # noqa: F401 - registers scenarios and suites
from api.agent.tools.eval_synthetic_tools import EVAL_SYNTHETIC_TOOL_DEFINITIONS
from api.evals.registry import ScenarioRegistry
from api.evals.scenarios.google_analytics_credential_retention import (
    GA4_DIRECT_CHARTER,
    GA4_DIRECT_AUTH_RULE,
    GA4_EXISTING_CHARTER,
    GA4_PIPEDREAM_TOOL,
    GA4_SERVICE_ACCOUNT_PATH,
    GOOGLE_ANALYTICS_CREDENTIAL_RETENTION_SCENARIO_SLUGS,
    GOOGLE_ANALYTICS_CREDENTIAL_RETENTION_SUITE_SLUG,
    GOOGLE_ANALYTICS_DIRECT_CORRECTION_PERSISTS_CHARTER,
    GOOGLE_ANALYTICS_DIRECT_ROUTE_SURVIVES_HISTORY_LOSS,
    GOOGLE_ANALYTICS_DIRECT_SETUP_PERSISTS_CHARTER,
    _eval_service_account,
    execution_uses_direct_google_analytics_route,
    google_analytics_charter_blocks_direct_route,
    google_analytics_charter_has_reusable_direct_route,
)
from api.evals.suites import SuiteRegistry
from api.models import (
    BrowserUseAgent,
    PersistentAgent,
    PersistentAgentCommsSnapshot,
    PersistentAgentStep,
    PersistentAgentStepSnapshot,
)


@tag("batch_google_analytics_credential_retention_evals")
class GoogleAnalyticsCredentialRetentionEvalTests(TestCase):
    @staticmethod
    def _charter_patch_call(old, new):
        escaped_old = old.replace("'", "''")
        escaped_new = new.replace("'", "''")
        return SimpleNamespace(
            tool_params={
                "sql": (
                    "UPDATE __agent_config SET charter=patch_text("
                    f"charter, '{escaped_old}', '{escaped_new}') WHERE id=1"
                )
            }
        )

    def test_scenarios_and_dedicated_suite_are_registered(self):
        registered = ScenarioRegistry.list_all()
        suite = SuiteRegistry.get(GOOGLE_ANALYTICS_CREDENTIAL_RETENTION_SUITE_SLUG)

        self.assertEqual(
            suite.scenario_slugs,
            GOOGLE_ANALYTICS_CREDENTIAL_RETENTION_SCENARIO_SLUGS,
        )
        for slug in GOOGLE_ANALYTICS_CREDENTIAL_RETENTION_SCENARIO_SLUGS:
            self.assertIn(slug, registered)

    def test_eval_prompts_recreate_behavior_without_prescribing_charter_patch(self):
        for slug in (
            GOOGLE_ANALYTICS_DIRECT_SETUP_PERSISTS_CHARTER,
            GOOGLE_ANALYTICS_DIRECT_CORRECTION_PERSISTS_CHARTER,
        ):
            prompt = ScenarioRegistry.get(slug).prompt.casefold()
            for forbidden in (
                "charter",
                "sqlite",
                "patch_text",
                "save this",
                "update your instructions",
            ):
                self.assertNotIn(forbidden, prompt)

        history_scenario = ScenarioRegistry.get(
            GOOGLE_ANALYTICS_DIRECT_ROUTE_SURVIVES_HISTORY_LOSS
        )
        self.assertEqual(
            [task.name for task in history_scenario.tasks],
            [
                "inject_setup_prompt",
                "verify_setup_charter_persisted",
                "compact_setup_history",
                "inject_report_request",
                "verify_direct_route_used",
                "verify_report_response",
                "verify_charter_unchanged",
            ],
        )
        self.assertEqual(history_scenario.existing_charter, GA4_EXISTING_CHARTER)
        self.assertNotEqual(history_scenario.existing_charter, GA4_DIRECT_CHARTER)

    def test_history_loss_compaction_preserves_model_written_charter(self):
        user = get_user_model().objects.create_user(username="ga4-history-eval")
        browser_agent = BrowserUseAgent.objects.create(user=user, name="GA4 history browser")
        model_written_charter = f"{GA4_DIRECT_CHARTER} Use concise comparison reports."
        agent = PersistentAgent.objects.create(
            user=user,
            browser_use_agent=browser_agent,
            name="GA4 history agent",
            charter=model_written_charter,
            execution_environment="eval",
        )
        scenario = ScenarioRegistry.get(GOOGLE_ANALYTICS_DIRECT_ROUTE_SURVIVES_HISTORY_LOSS)
        message = scenario.inject_message(
            agent.id,
            "Earlier direct-auth setup details that must disappear from raw history.",
            trigger_processing=False,
        )
        step = PersistentAgentStep.objects.create(
            agent=agent,
            description="Earlier direct-auth tool details that must disappear from raw history.",
        )

        scenario._compact_setup_history(agent.id)

        agent.refresh_from_db()
        comms_snapshot = PersistentAgentCommsSnapshot.objects.get(agent=agent)
        step_snapshot = PersistentAgentStepSnapshot.objects.get(agent=agent)
        self.assertEqual(agent.charter, model_written_charter)
        self.assertEqual(comms_snapshot.snapshot_until, message.timestamp)
        self.assertEqual(step_snapshot.snapshot_until, step.created_at)
        for summary in (comms_snapshot.summary, step_snapshot.summary):
            self.assertNotIn("service account", summary.casefold())
            self.assertNotIn("pipedream", summary.casefold())
            self.assertNotIn(GA4_SERVICE_ACCOUNT_PATH.casefold(), summary.casefold())

    def test_expected_charter_contains_safe_reusable_direct_route(self):
        self.assertTrue(google_analytics_charter_has_reusable_direct_route(GA4_DIRECT_CHARTER))
        self.assertFalse(google_analytics_charter_blocks_direct_route(GA4_DIRECT_CHARTER))
        self.assertIn(GA4_SERVICE_ACCOUNT_PATH, GA4_DIRECT_AUTH_RULE)

        reconnect_only = (
            f"{GA4_EXISTING_CHARTER} Google Analytics requires Pipedream. "
            "You must reconnect Pipedream and wait for authorization."
        )
        missing_secret = (
            f"{GA4_DIRECT_CHARTER} The service account is missing and must be provided again."
        )
        self.assertFalse(google_analytics_charter_has_reusable_direct_route(reconnect_only))
        self.assertTrue(google_analytics_charter_blocks_direct_route(reconnect_only))
        self.assertFalse(google_analytics_charter_has_reusable_direct_route(missing_secret))
        self.assertTrue(google_analytics_charter_blocks_direct_route(missing_secret))

    def test_eval_service_account_fixture_is_parseable(self):
        fixture = _eval_service_account()

        credentials = google_service_account.Credentials.from_service_account_info(
            fixture,
            scopes=["https://www.googleapis.com/auth/analytics.readonly"],
        )

        self.assertEqual(credentials.service_account_email, fixture["client_email"])
        self.assertTrue(fixture["private_key"].startswith("-----BEGIN PRIVATE KEY-----"))

    def test_charter_scenarios_require_one_focused_patch_with_direct_recipe(self):
        for slug in (
            GOOGLE_ANALYTICS_DIRECT_SETUP_PERSISTS_CHARTER,
            GOOGLE_ANALYTICS_DIRECT_CORRECTION_PERSISTS_CHARTER,
        ):
            scenario = ScenarioRegistry.get(slug)
            good_agent = SimpleNamespace(charter=GA4_DIRECT_CHARTER)
            patch_call = self._charter_patch_call("", GA4_DIRECT_AUTH_RULE)

            passed, detail = scenario._charter_check(good_agent, [patch_call])
            no_patch_passed, no_patch_detail = scenario._charter_check(good_agent, [])
            pipedream_agent = SimpleNamespace(
                charter=(
                    f"{GA4_DIRECT_CHARTER} You must reconnect Pipedream and wait for authorization."
                )
            )
            harmful_passed, harmful_detail = scenario._charter_check(
                pipedream_agent,
                [patch_call],
            )

            self.assertTrue(passed, detail)
            self.assertFalse(no_patch_passed, no_patch_detail)
            self.assertFalse(harmful_passed, harmful_detail)

    def test_direct_execution_checker_accepts_observed_service_account_shapes(self):
        run_command = SimpleNamespace(
            tool_name="run_command",
            tool_params={
                "command": (
                    "python -c \"from google.oauth2 import service_account; "
                    f"open('/workspace{GA4_SERVICE_ACCOUNT_PATH}'); "
                    "scope='https://www.googleapis.com/auth/analytics.readonly'; "
                    "url='https://analyticsdata.googleapis.com/v1beta/properties/"
                    "489999846:runReport'\""
                )
            },
        )
        python_exec = SimpleNamespace(
            tool_name="python_exec",
            tool_params={
                "code": (
                    f"with open('/workspace{GA4_SERVICE_ACCOUNT_PATH}') as f: info=f.read()\n"
                    "assert 'service_account' in info\n"
                    "token_url='https://oauth2.googleapis.com/token'\n"
                    "scope='analytics.readonly'\n"
                    "report_url='https://analyticsdata.googleapis.com/v1beta/properties/"
                    "489999846:runReport'"
                )
            },
        )
        pipedream_call = SimpleNamespace(
            tool_name=GA4_PIPEDREAM_TOOL,
            tool_params={"property": "properties/489999846"},
        )

        self.assertTrue(
            execution_uses_direct_google_analytics_route(
                run_command,
                require_property=True,
            )
        )
        self.assertTrue(
            execution_uses_direct_google_analytics_route(
                python_exec,
                require_property=True,
            )
        )
        self.assertFalse(
            execution_uses_direct_google_analytics_route(
                pipedream_call,
                require_property=True,
            )
        )

    def test_pipedream_ga4_tool_is_a_deterministic_eval_fixture(self):
        definition = EVAL_SYNTHETIC_TOOL_DEFINITIONS[GA4_PIPEDREAM_TOOL]

        self.assertIn("Pipedream", definition["description"])
        self.assertIn("property", definition["parameters"]["properties"])
        self.assertIn("startDate", definition["parameters"]["required"])
