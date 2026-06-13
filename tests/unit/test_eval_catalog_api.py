from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.urls import reverse

import api.evals.loader  # noqa: F401 - registers scenarios and suites
from api.evals.owner import EVAL_RUNNER_ORG_SLUG, EVAL_RUNNER_USERNAME
from api.models import EvalRun, EvalSuiteRun


@tag("batch_eval_fingerprint")
class EvalCatalogAPITests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="eval-catalog-admin",
            email="eval-catalog-admin@example.com",
            password="testpass123",
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_login(self.user)

    def test_suite_catalog_includes_scenario_metadata(self):
        response = self.client.get(reverse("console_evals_suites"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("suites", payload)
        self.assertIn("scenarios", payload)
        scenarios_by_slug = {scenario["slug"]: scenario for scenario in payload["scenarios"]}
        self.assertIn("echo_response", scenarios_by_slug)
        echo = scenarios_by_slug["echo_response"]
        self.assertEqual(echo["metadata"]["tier"], "smoke")
        self.assertEqual(echo["metadata"]["category"], "conversation")
        self.assertIn("smoke", echo["metadata"]["tags"])
        self.assertGreaterEqual(echo["task_count"], 1)

    @patch("console.api_views.gc_eval_runs_task.delay")
    @patch("console.api_views.run_eval_task.delay")
    def test_create_suite_run_accepts_single_scenario_slug(self, mock_run_eval_delay, mock_gc_delay):
        response = self.client.post(
            reverse("console_evals_suite_runs_create"),
            data={
                "scenario_slugs": ["echo_response"],
                "n_runs": 1,
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        suite_run = EvalSuiteRun.objects.get()
        self.assertEqual(suite_run.suite_slug, "single::echo_response")
        self.assertEqual(suite_run.requested_runs, 1)
        run = EvalRun.objects.select_related("agent__user", "agent__organization").get()
        self.assertEqual(run.scenario_slug, "echo_response")
        self.assertEqual(run.initiated_by, self.user)
        self.assertEqual(run.agent.user.username, EVAL_RUNNER_USERNAME)
        self.assertEqual(run.agent.organization.slug, EVAL_RUNNER_ORG_SLUG)
        self.assertEqual(run.agent.execution_environment, "eval")
        self.assertGreaterEqual(run.agent.organization.billing.purchased_seats, 1)
        mock_run_eval_delay.assert_called_once_with(str(run.id))
        mock_gc_delay.assert_called_once()

    @patch("console.api_views.gc_eval_runs_task.delay")
    @patch("console.api_views.run_eval_task.delay")
    def test_create_suite_run_uses_personal_agent_for_sms_scenario(self, mock_run_eval_delay, mock_gc_delay):
        response = self.client.post(
            reverse("console_evals_suite_runs_create"),
            data={
                "scenario_slugs": ["permit_followup_single_reply"],
                "n_runs": 1,
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        run = EvalRun.objects.select_related("agent__user", "agent__organization").get()
        self.assertEqual(run.scenario_slug, "permit_followup_single_reply")
        self.assertEqual(run.agent.user.username, EVAL_RUNNER_USERNAME)
        self.assertIsNone(run.agent.organization_id)
        self.assertEqual(run.agent.execution_environment, "eval")
        mock_run_eval_delay.assert_called_once_with(str(run.id))
        mock_gc_delay.assert_called_once()
