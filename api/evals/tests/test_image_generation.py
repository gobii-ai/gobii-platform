from types import SimpleNamespace

from django.test import SimpleTestCase, tag

import api.evals.loader  # noqa: F401 - registers scenarios and suites
from api.agent.core.event_processing import _resolve_eval_mock_result
from api.agent.tools.create_image import get_create_image_tool
from api.agent.tools.eval_synthetic_tools import (
    EVAL_SYNTHETIC_TOOL_DEFINITIONS,
    get_eval_synthetic_tool_fallback_result,
)
from api.agent.system_skills.image_generation import IMAGE_GENERATION_SYSTEM_SKILL_KEY
from api.evals.registry import ScenarioRegistry
from api.evals.scenarios.image_generation import (
    IMAGE_GENERATION_AVOIDS_ANALYSIS,
    IMAGE_GENERATION_CASES,
    IMAGE_GENERATION_EXACT_TEXT,
    IMAGE_GENERATION_MULTI_ASSET,
    IMAGE_GENERATION_NEW_ASSET,
    IMAGE_GENERATION_SCENARIO_SLUGS,
    IMAGE_GENERATION_SOURCE_EDIT,
    IMAGE_GENERATION_SUITE_SLUG,
)
from api.evals.suites import SuiteRegistry


@tag("batch_image_generation_skill", "eval_sim")
class ImageGenerationScenarioTests(SimpleTestCase):
    def test_image_generation_suite_contains_five_scenarios(self):
        suite = SuiteRegistry.get(IMAGE_GENERATION_SUITE_SLUG)

        self.assertIsNotNone(suite)
        self.assertEqual(tuple(suite.scenario_slugs), IMAGE_GENERATION_SCENARIO_SLUGS)
        self.assertEqual(len(suite.scenario_slugs), 5)

    def test_generated_scenarios_have_expected_metadata(self):
        registered = ScenarioRegistry.list_all()

        for slug in IMAGE_GENERATION_SCENARIO_SLUGS:
            metadata = registered[slug].get_metadata()
            self.assertEqual(metadata.category, "image_generation")
            self.assertEqual(metadata.area, "system_skills")
            self.assertEqual(metadata.expected_runtime, "short")
            self.assertEqual(metadata.cost_class, "low")
            self.assertIn("image_generation", metadata.tags)
            self.assertIn("system_skill", metadata.tags)
            self.assertIn("real_harness", metadata.tags)

    def test_cases_cover_the_five_requested_behaviors(self):
        self.assertEqual(
            {case.slug for case in IMAGE_GENERATION_CASES},
            {
                IMAGE_GENERATION_NEW_ASSET,
                IMAGE_GENERATION_SOURCE_EDIT,
                IMAGE_GENERATION_EXACT_TEXT,
                IMAGE_GENERATION_MULTI_ASSET,
                IMAGE_GENERATION_AVOIDS_ANALYSIS,
            },
        )

    def test_eval_create_image_definition_matches_production_contract(self):
        production = get_create_image_tool()["function"]
        synthetic = EVAL_SYNTHETIC_TOOL_DEFINITIONS["create_image"]

        self.assertEqual(synthetic["description"], production["description"])
        self.assertEqual(synthetic["parameters"], production["parameters"])
        self.assertEqual(synthetic["system_skill_key"], IMAGE_GENERATION_SYSTEM_SKILL_KEY)
        self.assertIn(
            "require verbatim rendering",
            production["parameters"]["properties"]["prompt"]["description"],
        )

    def test_cases_encode_source_text_multi_asset_and_negative_constraints(self):
        by_slug = {case.slug: case for case in IMAGE_GENERATION_CASES}

        new_asset = by_slug[IMAGE_GENERATION_NEW_ASSET]
        self.assertEqual(new_asset.expected_call_count, 1)
        self.assertEqual(new_asset.expected_aspect_ratio, "16:9")

        source_edit = by_slug[IMAGE_GENERATION_SOURCE_EDIT]
        self.assertEqual(source_edit.required_source_images, ("/Inbox/product.png",))
        self.assertIn(("change only",), source_edit.required_prompt_groups)
        self.assertEqual(set(source_edit.mock_config), {"read_file", "create_image"})

        exact_text = by_slug[IMAGE_GENERATION_EXACT_TEXT]
        self.assertIn(("Built for the Long Run.",), exact_text.required_prompt_groups)
        self.assertEqual(exact_text.expected_aspect_ratio, "4:5")

        multi_asset = by_slug[IMAGE_GENERATION_MULTI_ASSET]
        self.assertEqual(multi_asset.expected_call_count, 3)
        self.assertEqual(multi_asset.required_prompt_terms_across_calls, ("morning", "afternoon", "evening"))

        negative = by_slug[IMAGE_GENERATION_AVOIDS_ANALYSIS]
        self.assertTrue(negative.forbid_create_image)
        self.assertIn("create_image", negative.eval_stop_policy()["stop_on_tool_names"])
        self.assertIn("sqlite_batch", negative.eval_stop_policy()["allowed_tool_names"])
        self.assertIn(("Northstar Market",), negative.required_response_groups)
        self.assertIn(("2026-07-08", "July 8, 2026", "Jul 8, 2026"), negative.required_response_groups)
        self.assertIn(("$42.17", "42.17"), negative.required_response_groups)

    def test_file_dependent_cases_supply_deterministic_read_results(self):
        by_slug = {case.slug: case for case in IMAGE_GENERATION_CASES}

        source_result = _resolve_eval_mock_result(
            by_slug[IMAGE_GENERATION_SOURCE_EDIT].mock_config,
            "read_file",
            {"path": "$[/Inbox/product.png]"},
        )
        receipt_result = _resolve_eval_mock_result(
            by_slug[IMAGE_GENERATION_AVOIDS_ANALYSIS].mock_config,
            "read_file",
            {"path": "$[/Inbox/receipt.png]"},
        )
        receipt_ledger_result = _resolve_eval_mock_result(
            by_slug[IMAGE_GENERATION_AVOIDS_ANALYSIS].mock_config,
            "sqlite_batch",
            {"sql": "SELECT * FROM __files"},
        )

        self.assertEqual(source_result["status"], "ok")
        self.assertEqual(source_result["path"], "/Inbox/product.png")
        self.assertEqual(receipt_result["status"], "ok")
        self.assertIn("Northstar Market", receipt_result["content"])
        self.assertIn("$42.17", receipt_result["content"])
        self.assertEqual(receipt_ledger_result["results"][0]["result"][0]["path"], "/Inbox/receipt.png")

    def test_multi_asset_mock_returns_distinct_placeholders(self):
        case = next(case for case in IMAGE_GENERATION_CASES if case.slug == IMAGE_GENERATION_MULTI_ASSET)

        refs = []
        for prompt_term in case.required_prompt_terms_across_calls:
            result = _resolve_eval_mock_result(
                case.mock_config,
                "create_image",
                {"prompt": f"A {prompt_term} coffee scene", "file_path": f"/exports/{prompt_term}.png"},
            )
            refs.append(result["file"])

        self.assertEqual(len(set(refs)), 3)
        self.assertTrue(all(ref.startswith("$[/exports/eval-coffee-") for ref in refs))

    def test_fallback_counts_string_source_image_as_one(self):
        string_result = get_eval_synthetic_tool_fallback_result(
            "create_image",
            {"source_images": "$[/Inbox/product.png]"},
        )
        list_result = get_eval_synthetic_tool_fallback_result(
            "create_image",
            {"source_images": ["$[/Inbox/a.png]", "$[/Inbox/b.png]"]},
        )

        self.assertEqual(string_result["source_image_count"], 1)
        self.assertEqual(list_result["source_image_count"], 2)

    def test_prompt_contract_scorer_accepts_representative_source_edit(self):
        scenario = ScenarioRegistry.get(IMAGE_GENERATION_SOURCE_EDIT)
        calls = [
            SimpleNamespace(
                tool_params={
                    "prompt": (
                        "Change only the background to deep navy; preserve the bottle, label text, proportions, "
                        "lighting, and camera angle unchanged."
                    ),
                    "file_path": "/exports/product-navy.png",
                    "source_images": "$[/Inbox/product.png]",
                }
            )
        ]

        self.assertEqual(scenario._prompt_contract_errors(calls), [])
