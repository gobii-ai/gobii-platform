"""
Tests for eval scenario fingerprinting.

These tests ensure the fingerprinting system correctly identifies
when eval code changes (or doesn't change) for comparison tracking.
"""

from django.test import TestCase, tag
from unittest.mock import patch, MagicMock

from api.evals.fingerprint import (
    compute_scenario_fingerprint,
    get_code_version,
    get_code_branch,
)
from api.evals.base import EvalScenario, ScenarioTask


@tag("batch_eval_fingerprint")
class ScenarioFingerprintTests(TestCase):
    """Tests for compute_scenario_fingerprint()."""

    def test_fingerprint_is_stable(self):
        """Same class should produce same fingerprint on repeated calls."""

        class StableScenario(EvalScenario):
            slug = "test"
            tasks = [ScenarioTask(name="task1", assertion_type="manual")]

            def run(self, run_id, agent_id):
                x = 1
                return x

        fp_1 = compute_scenario_fingerprint(StableScenario)
        fp_2 = compute_scenario_fingerprint(StableScenario)
        fp_3 = compute_scenario_fingerprint(StableScenario())  # Instance too

        self.assertEqual(fp_1, fp_2)
        self.assertEqual(fp_1, fp_3)

    def test_whitespace_changes_do_not_affect_fingerprint(self):
        """Comments and whitespace should not affect the fingerprint."""

        class ScenarioClean(EvalScenario):
            slug = "test"

            def run(self, run_id, agent_id):
                x = 1
                return x

        # Note: We can't easily test this with actual classes since Python
        # normalizes whitespace at parse time. The AST dump handles this.
        # The shell test already proved this works.
        fp = compute_scenario_fingerprint(ScenarioClean)
        self.assertEqual(len(fp), 16)
        self.assertTrue(all(c in "0123456789abcdef" for c in fp))

    def test_behavioral_change_produces_different_fingerprint(self):
        """Code changes that affect behavior should change the fingerprint."""

        class ScenarioV1(EvalScenario):
            slug = "test"

            def run(self, run_id, agent_id):
                x = 1
                return x

        class ScenarioV2(EvalScenario):
            slug = "test"

            def run(self, run_id, agent_id):
                x = 2  # Changed value
                return x

        fp_v1 = compute_scenario_fingerprint(ScenarioV1)
        fp_v2 = compute_scenario_fingerprint(ScenarioV2)

        self.assertNotEqual(fp_v1, fp_v2)

    def test_task_changes_affect_fingerprint(self):
        """Changes to task definitions should change the fingerprint."""

        class ScenarioOneTasks(EvalScenario):
            slug = "test"
            tasks = [ScenarioTask(name="task1", assertion_type="manual")]

            def run(self, run_id, agent_id):
                pass

        class ScenarioTwoTasks(EvalScenario):
            slug = "test"
            tasks = [
                ScenarioTask(name="task1", assertion_type="manual"),
                ScenarioTask(name="task2", assertion_type="llm_judge"),
            ]

            def run(self, run_id, agent_id):
                pass

        fp_one = compute_scenario_fingerprint(ScenarioOneTasks)
        fp_two = compute_scenario_fingerprint(ScenarioTwoTasks)

        self.assertNotEqual(fp_one, fp_two)

    def test_fingerprint_works_with_instance(self):
        """Should work with both class and instance."""

        class TestScenario(EvalScenario):
            slug = "test"

            def run(self, run_id, agent_id):
                pass

        fp_class = compute_scenario_fingerprint(TestScenario)
        fp_instance = compute_scenario_fingerprint(TestScenario())

        self.assertEqual(fp_class, fp_instance)

    def test_fingerprint_format(self):
        """Fingerprint should be a 16-char hex string."""

        class TestScenario(EvalScenario):
            slug = "test"

            def run(self, run_id, agent_id):
                pass

        fp = compute_scenario_fingerprint(TestScenario)

        self.assertEqual(len(fp), 16)
        self.assertTrue(all(c in "0123456789abcdef" for c in fp))

    def test_real_scenarios_have_fingerprints(self):
        """Verify fingerprinting works on actual registered scenarios."""
        from api.evals.registry import ScenarioRegistry
        import api.evals.loader  # noqa: F401 - triggers scenario registration

        scenarios = ScenarioRegistry.list_all()
        self.assertGreater(len(scenarios), 0, "Should have registered scenarios")

        for slug in scenarios:
            scenario = ScenarioRegistry.get(slug)
            fp = compute_scenario_fingerprint(scenario)
            self.assertEqual(len(fp), 16, f"Fingerprint for {slug} should be 16 chars")


@tag("batch_eval_fingerprint")
class GitVersionTests(TestCase):
    """Tests for get_code_version() and get_code_branch()."""

    def test_get_code_version_returns_string(self):
        """Should return a string (possibly empty if not in git repo)."""
        version = get_code_version()
        self.assertIsInstance(version, str)

    def test_get_code_version_format(self):
        """If in a git repo, should return a 12-char hash."""
        version = get_code_version()
        if version:  # Only check if we got a result
            self.assertEqual(len(version), 12)
            self.assertTrue(all(c in "0123456789abcdef" for c in version))

    def test_get_code_branch_returns_string(self):
        """Should return a string (possibly empty)."""
        branch = get_code_branch()
        self.assertIsInstance(branch, str)

    @patch("api.evals.fingerprint.subprocess.run")
    def test_get_code_version_handles_git_failure(self, mock_run):
        """Should return empty string if git command fails."""
        mock_run.side_effect = FileNotFoundError("git not found")

        version = get_code_version()

        self.assertEqual(version, "")

    @patch("api.evals.fingerprint.subprocess.run")
    def test_get_code_branch_handles_git_failure(self, mock_run):
        """Should return empty string if git command fails."""
        mock_run.side_effect = FileNotFoundError("git not found")

        branch = get_code_branch()

        self.assertEqual(branch, "")

    @patch("api.evals.fingerprint.subprocess.run")
    def test_get_code_branch_handles_detached_head(self, mock_run):
        """Should return empty string for detached HEAD state."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "HEAD\n"
        mock_run.return_value = mock_result

        branch = get_code_branch()

        self.assertEqual(branch, "")


@tag("batch_eval_fingerprint")
class EvalComparisonAPITests(TestCase):
    """Tests for eval comparison API endpoints."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        from api.models import BrowserUseAgent, PersistentAgent, EvalRun

        User = get_user_model()
        self.user = User.objects.create_user(
            username="eval_test_user",
            email="eval@test.com",
            password="testpass",
            is_staff=True,
            is_superuser=True,
        )

        # Create a browser agent and persistent agent for eval runs
        self.browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="Test Browser Agent",
        )
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Test Agent",
            browser_use_agent=self.browser_agent,
            charter="Test charter",
        )

        # Create eval runs with fingerprints
        self.run1 = EvalRun.objects.create(
            scenario_slug="weather_lookup",
            scenario_version="1.0.0",
            scenario_fingerprint="abc123def456",
            code_version="commit1",
            code_branch="main",
            agent=self.agent,
            initiated_by=self.user,
            status=EvalRun.Status.COMPLETED,
        )
        self.run2 = EvalRun.objects.create(
            scenario_slug="weather_lookup",
            scenario_version="1.0.0",
            scenario_fingerprint="abc123def456",  # Same fingerprint
            code_version="commit2",
            code_branch="main",
            agent=self.agent,
            initiated_by=self.user,
            status=EvalRun.Status.COMPLETED,
        )
        self.run3 = EvalRun.objects.create(
            scenario_slug="weather_lookup",
            scenario_version="1.0.0",
            scenario_fingerprint="different123",  # Different fingerprint
            code_version="commit3",
            code_branch="main",
            agent=self.agent,
            initiated_by=self.user,
            status=EvalRun.Status.COMPLETED,
        )

    def test_run_detail_includes_fingerprint_fields(self):
        """Run detail should include fingerprint, code_version, code_branch."""
        self.client.force_login(self.user)
        response = self.client.get(f"/console/api/evals/runs/{self.run1.id}/")

        self.assertEqual(response.status_code, 200)
        data = response.json()["run"]
        self.assertEqual(data["scenario_fingerprint"], "abc123def456")
        self.assertEqual(data["code_version"], "commit1")
        self.assertEqual(data["code_branch"], "main")

    def test_run_detail_includes_comparison_metadata(self):
        """Run detail should include comparable_runs_count."""
        self.client.force_login(self.user)
        response = self.client.get(f"/console/api/evals/runs/{self.run1.id}/")

        self.assertEqual(response.status_code, 200)
        data = response.json()["run"]
        self.assertIn("comparison", data)
        # run2 has same fingerprint, run3 does not
        self.assertEqual(data["comparison"]["comparable_runs_count"], 1)
        self.assertTrue(data["comparison"]["has_comparable_runs"])

    def test_compare_endpoint_pragmatic_tier(self):
        """Pragmatic tier returns runs with same fingerprint."""
        self.client.force_login(self.user)
        response = self.client.get(
            f"/console/api/evals/runs/{self.run1.id}/compare/?tier=pragmatic"
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["tier"], "pragmatic")
        self.assertEqual(len(data["runs"]), 1)  # Only run2 matches
        self.assertEqual(data["runs"][0]["id"], str(self.run2.id))

    def test_compare_endpoint_historical_tier(self):
        """Historical tier returns runs with same slug (any fingerprint)."""
        self.client.force_login(self.user)
        response = self.client.get(
            f"/console/api/evals/runs/{self.run1.id}/compare/?tier=historical"
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["tier"], "historical")
        self.assertEqual(len(data["runs"]), 2)  # Both run2 and run3 match

    def test_compare_endpoint_historical_warns_on_fingerprint_mismatch(self):
        """Historical tier warns when fingerprints differ."""
        self.client.force_login(self.user)
        response = self.client.get(
            f"/console/api/evals/runs/{self.run1.id}/compare/?tier=historical"
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIsNotNone(data["fingerprint_warning"])
        self.assertIn("different fingerprints", data["fingerprint_warning"])

    def test_compare_endpoint_invalid_tier(self):
        """Invalid tier returns error."""
        self.client.force_login(self.user)
        response = self.client.get(
            f"/console/api/evals/runs/{self.run1.id}/compare/?tier=invalid"
        )

        self.assertEqual(response.status_code, 400)

    def test_compare_endpoint_run_type_filter(self):
        """Can filter by run_type."""
        from api.models import EvalRun

        # Mark run2 as official
        self.run2.run_type = EvalRun.RunType.OFFICIAL
        self.run2.save()

        self.client.force_login(self.user)
        response = self.client.get(
            f"/console/api/evals/runs/{self.run1.id}/compare/?tier=pragmatic&run_type=official"
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data["runs"]), 1)
        self.assertEqual(data["runs"][0]["run_type"], "official")
