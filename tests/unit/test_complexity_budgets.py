from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, tag

from scripts import check_complexity_budgets as budgets


@tag("complexity_guardrails_batch")
class ComplexityBudgetSourceFilterTests(SimpleTestCase):
    def assert_counted(self, path: str) -> None:
        self.assertTrue(budgets._is_counted_source(path), path)

    def assert_not_counted(self, path: str) -> None:
        self.assertFalse(budgets._is_counted_source(path), path)

    def test_source_inventory_includes_untracked_files(self):
        with patch(
            "scripts.check_complexity_budgets.subprocess.check_output",
            return_value=b"api/tracked.py\0api/new_untracked.py\0",
        ) as check_output:
            files = budgets._git_files()

        self.assertEqual(files, ["api/tracked.py", "api/new_untracked.py"])
        command = check_output.call_args.args[0]
        self.assertIn("--cached", command)
        self.assertIn("--others", command)
        self.assertIn("--exclude-standard", command)

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
        )
        for key in metadata_keys:
            with self.subTest(key=key):
                self.assertEqual(committed[key], generated[key])

    def test_check_source_loc_fails_when_current_source_exceeds_limit(self):
        measurement = budgets.SourceLocMeasurement(line_count=101, file_count=1)
        with (
            patch.object(budgets, "measure_source_loc", return_value=measurement),
            self.assertRaisesRegex(budgets.BudgetFailure, "current=101, limit=100"),
        ):
            budgets.check_source_loc({"source_loc": {"limit": 100}})


@tag("complexity_guardrails_batch")
class ComplexityBudgetPromptTests(SimpleTestCase):
    def test_representative_prompt_scenarios_are_guarded(self):
        expected_scenarios = {
            "billing_catalog_request",
            "normal_explicit_send",
            "normal_first_run",
            "web_chat_implied_send",
            "web_chat_first_run",
            "planning_first_run",
            "planning_continuation",
            "enabled_system_skills",
            "builtin_tool_rich",
            "mature_agent_state",
        }
        committed = budgets._load_budget()["prompt_size"]

        self.assertEqual({scenario.name for scenario in budgets.PROMPT_SCENARIOS}, expected_scenarios)
        self.assertEqual(set(budgets.PROMPT_SCENARIO_DESCRIPTIONS), expected_scenarios)
        self.assertEqual(set(committed["limits"]), expected_scenarios)
        self.assertEqual(set(committed["observed"]), expected_scenarios)
        self.assertEqual(committed["limits"]["normal_explicit_send"]["total_bytes"], 33000)
        self.assertEqual(committed["limits"]["billing_catalog_request"]["total_bytes"], 34000)
        self.assertEqual(committed["limits"]["web_chat_implied_send"]["total_bytes"], 33000)
        self.assertEqual(committed["limits"]["planning_first_run"]["total_bytes"], 16500)
        self.assertEqual(committed["limits"]["planning_continuation"]["total_bytes"], 16000)
        self.assertEqual(committed["limits"]["normal_first_run"]["total_bytes"], 34000)
        self.assertEqual(committed["limits"]["web_chat_first_run"]["total_bytes"], 34500)
        self.assertEqual(committed["limits"]["mature_agent_state"]["total_bytes"], 46000)
        for scenario in expected_scenarios:
            with self.subTest(scenario=scenario):
                self.assertEqual(set(committed["limits"][scenario]), set(budgets.PROMPT_BYTE_METRICS))
                self.assertEqual(set(committed["observed"][scenario]), set(budgets.PROMPT_BYTE_METRICS))

    def test_skill_envelope_uses_largest_skills_with_their_tools(self):
        scenario = next(
            scenario for scenario in budgets.PROMPT_SCENARIOS if scenario.name == "enabled_system_skills"
        )
        self.assertEqual(scenario.enabled_system_skill_keys, ())

        def skill(skill_key, prompt, tool_names=()):
            return SimpleNamespace(
                skill_key=skill_key,
                tool_names=tool_names,
                tools_to_enable=lambda names=tool_names: names,
                render_prompt_instructions=lambda _agent, text=prompt: text,
                render_prompt_context=lambda _agent: "",
            )

        registry = {
            "prompt_heavy": skill("prompt_heavy", "p" * 400),
            "tool_heavy": skill("tool_heavy", "short", ("large_tool",)),
            "medium": skill("medium", "m" * 200),
            "small": skill("small", "s" * 20),
        }
        builtin_tools = {
            "large_tool": {
                "definition": lambda: {
                    "type": "function",
                    "function": {
                        "name": "large_tool",
                        "description": "t" * 800,
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            }
        }

        with (
            patch("api.agent.system_skills.registry.SYSTEM_SKILL_REGISTRY", registry),
            patch("api.agent.tools.tool_manager.BUILTIN_TOOL_REGISTRY", builtin_tools),
        ):
            selected = budgets._largest_system_skill_keys(SimpleNamespace(), limit=3)

        self.assertEqual(selected, ("tool_heavy", "prompt_heavy", "medium"))
        self.assertIn("largest registered", budgets.PROMPT_SCENARIO_DESCRIPTIONS[scenario.name])

    def test_tool_envelope_uses_largest_available_builtins(self):
        def tool(name, size):
            return {"definition": lambda: {"name": name, "description": "x" * size}}

        registry = {
            "large": tool("large", 400),
            "unavailable": tool("unavailable", 800),
            "medium": tool("medium", 200),
            "small": tool("small", 20),
        }
        availability = lambda name, _agent, include_hidden: name != "unavailable"
        with (
            patch("api.agent.tools.tool_manager.BUILTIN_TOOL_REGISTRY", registry),
            patch("api.agent.tools.tool_manager._is_builtin_tool_available", side_effect=availability),
        ):
            selected = budgets._largest_builtin_tool_names(SimpleNamespace(), limit=2)

        self.assertEqual(selected, ("large", "medium"))

    def test_budget_file_prompt_metadata_matches_generated_metadata(self):
        committed = budgets._load_budget()["prompt_size"]
        generated = budgets._budget_metadata("test-sha")["prompt_size"]

        for key in ("description", "limit_policy", "scenarios", "unit"):
            with self.subTest(key=key):
                self.assertEqual(committed[key], generated[key])

    def test_prompt_coverage_rejects_unbudgeted_scenarios_and_metrics(self):
        measurements = {
            "covered": {
                "system_bytes": 10,
                "user_bytes": 20,
                "tools_bytes": 30,
                "total_bytes": 60,
            },
            "new_path": {
                "system_bytes": 10,
                "user_bytes": 20,
                "tools_bytes": 30,
                "total_bytes": 60,
            },
        }
        limits = {
            "covered": {
                "system_bytes": 100,
                "user_bytes": 100,
                "tools_bytes": 100,
            }
        }

        failures = budgets._validate_prompt_scenario_coverage(measurements, limits)

        self.assertIn("covered.total_bytes: missing approved limit", failures)
        self.assertIn("new_path: missing approved limits", failures)

    def test_update_baselines_refreshes_observed_without_relaxing_limits(self):
        limits = {
            "normal": {
                "system_bytes": 100,
                "user_bytes": 100,
                "tools_bytes": 100,
                "total_bytes": 250,
            }
        }
        measurements = {
            "normal": {
                "system_bytes": 90,
                "user_bytes": 90,
                "tools_bytes": 90,
                "total_bytes": 270,
            }
        }
        existing = {
            "baseline_sha": "old",
            "source_loc": {"limit": 1, "file_count": 1},
            "prompt_size": {
                "limits": deepcopy(limits),
                "observed": {},
            },
        }

        with (
            patch.object(budgets, "_load_budget", return_value=existing),
            patch.object(budgets, "measure_prompt_sizes", return_value=measurements),
            patch.object(budgets, "_write_budget") as write_budget,
        ):
            updated = budgets.update_baselines(
                baseline_sha="new",
                loc_only=False,
                prompt_only=True,
            )

        self.assertEqual(updated["prompt_size"]["limits"], limits)
        self.assertEqual(updated["prompt_size"]["observed"], measurements)
        write_budget.assert_called_once_with(updated)

    def test_update_baselines_requires_approved_limits_for_new_scenario(self):
        existing = {
            "source_loc": {"limit": 1, "file_count": 1},
            "prompt_size": {
                "limits": {
                    "existing": {
                        "system_bytes": 100,
                        "user_bytes": 100,
                        "tools_bytes": 100,
                        "total_bytes": 300,
                    }
                }
            },
        }
        measurements = {
            "existing": {
                "system_bytes": 10,
                "user_bytes": 10,
                "tools_bytes": 10,
                "total_bytes": 30,
            },
            "new_path": {
                "system_bytes": 10,
                "user_bytes": 10,
                "tools_bytes": 10,
                "total_bytes": 30,
            },
        }

        with (
            patch.object(budgets, "_load_budget", return_value=existing),
            patch.object(budgets, "measure_prompt_sizes", return_value=measurements),
            patch.object(budgets, "_write_budget") as write_budget,
            self.assertRaisesRegex(budgets.BudgetFailure, "new_path: missing approved limits"),
        ):
            budgets.update_baselines(
                baseline_sha="new",
                loc_only=False,
                prompt_only=True,
            )

        write_budget.assert_not_called()

    def test_check_prompt_sizes_fails_for_unbudgeted_current_scenario(self):
        measurements = {
            "new_path": {
                "system_bytes": 10,
                "user_bytes": 10,
                "tools_bytes": 10,
                "total_bytes": 30,
            }
        }
        budget = {"prompt_size": {"limits": {}}}

        with (
            patch.object(budgets, "measure_prompt_sizes", return_value=measurements),
            self.assertRaisesRegex(budgets.BudgetFailure, "new_path: missing approved limits"),
        ):
            budgets.check_prompt_sizes(budget)

    def test_check_prompt_sizes_fails_when_a_metric_exceeds_its_limit(self):
        measurements = {
            "normal": dict(system_bytes=101, user_bytes=20, tools_bytes=30, total_bytes=151)
        }
        limits = {
            "normal": dict(system_bytes=100, user_bytes=100, tools_bytes=100, total_bytes=300)
        }
        with (
            patch.object(budgets, "measure_prompt_sizes", return_value=measurements),
            self.assertRaisesRegex(budgets.BudgetFailure, "normal.system_bytes: current=101"),
        ):
            budgets.check_prompt_sizes({"prompt_size": {"limits": limits}})


@tag("complexity_guardrails_batch")
class ComplexityBudgetPromptIntegrationTests(TestCase):
    @patch("api.agent.tools.tool_manager.sandbox_compute_enabled_for_agent", return_value=True)
    def test_enabled_skill_fixture_renders_every_associated_tool(self, _sandbox_mock):
        from api.agent.core.prompt_context import get_agent_tools
        from api.agent.system_skills.registry import get_system_skill_definition

        scenario = next(
            scenario for scenario in budgets.PROMPT_SCENARIOS if scenario.name == "enabled_system_skills"
        )
        agent, _user, _endpoint = budgets._create_agent(scenario=scenario.name, planning=False)
        scenario = replace(
            scenario,
            enabled_system_skill_keys=budgets._largest_system_skill_keys(agent, limit=3),
        )
        budgets._configure_prompt_scenario(agent, scenario)

        rendered_names = {
            tool["function"]["name"]
            for tool in get_agent_tools(agent)
            if isinstance(tool.get("function"), dict)
        }
        expected_names = {
            tool_name
            for skill_key in scenario.enabled_system_skill_keys
            for tool_name in get_system_skill_definition(skill_key).tools_to_enable()
        }
        self.assertTrue(expected_names)
        self.assertTrue(expected_names.issubset(rendered_names))
