from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase, tag

from scripts import check_complexity_budgets as budgets


@tag("complexity_guardrails_batch")
class ComplexityBudgetSourceFilterTests(SimpleTestCase):
    def assert_counted(self, path: str) -> None:
        self.assertTrue(budgets._is_counted_source(path), path)

    def assert_not_counted(self, path: str) -> None:
        self.assertFalse(budgets._is_counted_source(path), path)

    def test_counts_core_product_paths_even_with_eval_or_test_words(self):
        counted_paths = (
            "api/services/evaluation_summary.py",
            "api/services/contest_results.py",
            "api/services/latest_activity.py",
            "api/agent/tools/spawn_web_task.py",
            "api/templates/admin/test_sms_form.html",
            "console/usage_views.py",
            "frontend/src/components/mcp/McpServerTestModal.tsx",
            "frontend/src/api/llmConfig.ts",
            "frontend/src/components/agentChat/TestimonialPanel.tsx",
        )

        for path in counted_paths:
            with self.subTest(path=path):
                self.assert_counted(path)

    def test_excludes_unit_and_integration_test_assets(self):
        test_paths = (
            "tests/unit/test_agent.py",
            "api/agent/core/tests/test_event_processing.py",
            "api/agent/core/tests/__init__.py",
            "sandbox_server/tests/test_files.py",
            "frontend/src/api/agentChat.test.ts",
            "frontend/src/components/agentChat/AgentChatLayout.spec.tsx",
            "frontend/src/test/setup.ts",
            "config/minimal_test_settings.py",
            "config/test_settings.py",
        )

        for path in test_paths:
            with self.subTest(path=path):
                self.assert_not_counted(path)

    def test_excludes_dedicated_eval_assets(self):
        eval_paths = (
            "api/evals/runner.py",
            "api/evals/scenarios/weather_lookup.py",
            "api/agent/eval_agents.py",
            "api/agent/tools/eval_synthetic_tools.py",
            "api/management/commands/run_evals.py",
            "api/templates/evals/sim/weather.html",
            "console/evals/consumers.py",
            "console/templates/console/evals.html",
            "config/eval_local_settings.py",
            "config/eval_postgres_settings.py",
            "frontend/src/api/evals.ts",
            "frontend/src/components/evals/CompareModal.tsx",
            "frontend/src/screens/EvalsScreen.tsx",
        )

        for path in eval_paths:
            with self.subTest(path=path):
                self.assert_not_counted(path)

    def test_excludes_non_core_product_adjacent_paths(self):
        non_core_paths = (
            "api/agent/system_skills/defaults.py",
            "api/agent/system_skills/native_api_cookbooks.py",
            "marketing_events/views.py",
            "pages/templates/pages/home.html",
            "proprietary/enterprise_overlay.py",
            "setup/bootstrap.sh",
            "static/css/site.css",
            "static/js/onboarding.js",
            "templates/registration/login.html",
        )

        for path in non_core_paths:
            with self.subTest(path=path):
                self.assert_not_counted(path)

    def test_excludes_dedicated_pet_assets(self):
        pet_paths = (
            "api/services/user_pets.py",
            "console/user_pets_api.py",
            "frontend/src/api/userPets.ts",
            "frontend/src/components/pets/ImmersivePetLayer.tsx",
            "frontend/src/hooks/useUserPets.ts",
        )

        for path in pet_paths:
            with self.subTest(path=path):
                self.assert_not_counted(path)

    def test_named_regions_are_excluded_from_source_loc(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "source.py"
            path.write_text(
                "\n".join(
                    (
                        "counted = True",
                        "# complexity-budget: exclude-start pet",
                        "excluded = True",
                        "# complexity-budget: exclude-end pet",
                        "also_counted = True",
                        "{/* complexity-budget: exclude-start pet */}",
                        "also_excluded = True",
                        "{/* complexity-budget: exclude-end pet */}",
                    )
                ),
                encoding="utf-8",
            )

            self.assertEqual(budgets._count_nonblank_lines(path), 2)

    def test_budget_file_source_metadata_matches_filter_constants(self):
        committed = budgets._load_budget()["source_loc"]
        generated = budgets._budget_metadata("test-sha")["source_loc"]

        metadata_keys = (
            "description",
            "include_roots",
            "include_files",
            "include_suffixes",
            "exclude_prefixes",
            "exclude_parts",
            "exclude_filenames",
            "exclude_test_prefixes",
            "exclude_test_parts",
            "exclude_test_files",
            "exclude_test_file_suffixes",
            "exclude_eval_prefixes",
            "exclude_eval_files",
            "exclude_region_markers",
        )
        for key in metadata_keys:
            with self.subTest(key=key):
                self.assertEqual(committed[key], generated[key])
