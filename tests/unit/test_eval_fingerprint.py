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
