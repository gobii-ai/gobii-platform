import json
import uuid
from contextlib import nullcontext
from types import SimpleNamespace

from django.test import TestCase, tag
from django.utils import timezone

import api.evals.loader  # noqa: F401 - registers scenarios and suites
from api.evals.registry import ScenarioRegistry
from api.evals.scenarios.custom_tool_result_contract import (
    CUSTOM_TOOL_RESULT_CONTRACT_CASES,
    CUSTOM_TOOL_RESULT_CONTRACT_SCENARIO_SLUGS,
    CUSTOM_TOOL_RESULT_CONTRACT_SUITE_SLUG,
    CustomToolResultContractScenario,
)
from api.evals.suites import SuiteRegistry
from api.models import EvalRunTask


def _case(slug):
    return next(case for case in CUSTOM_TOOL_RESULT_CONTRACT_CASES if case.slug == slug)


def _parameters_schema(case):
    properties = {}
    for param_name in case.required_param_names:
        properties[param_name] = {
            "type": "integer" if param_name in {"batch_size", "limit"} else "string"
        }
    return {
        "type": "object",
        "properties": properties,
        "required": list(case.required_param_names),
        "additionalProperties": False,
    }


def _source_code(extra_fields=""):
    return f"""
from _gobii_ctx import main


def run(params, ctx):
    batch_size = params.get("batch_size")
    return {{
        "status": "ok",
        "side_effects_completed": True,
        "summary": "Appended 20 rows and skipped 2 duplicates.",
        "side_effects": [{{"target": "signals", "items_written": 20}}],
        "source": {{"filters": {{"run_date": params.get("run_date")}}}},
        "verification": {{"mode": "read_only"}},
        "remaining_work": {{"count": 0, "next_cursor": None}},
        "next_action": "Use read-only verification; do not replay append/add/update calls.",
        "warnings": [],
        "batch_size": batch_size,
        {extra_fields}
    }}


if __name__ == "__main__":
    main(run)
"""


def _create_call(case, *, schema=None, source_code=None, name=None):
    params = {
        "name": name or case.slug,
        "description": "Build a helpful custom tool result contract.",
        "source_path": f"/tools/{case.slug}.py",
        "parameters_schema": schema or _parameters_schema(case),
    }
    if source_code is not None:
        params["source_code"] = source_code
    else:
        params["source_code"] = _source_code()
    return SimpleNamespace(
        tool_name="create_custom_tool",
        tool_params=params,
        result={"status": "created"},
        status="complete",
        step=None,
    )


def _custom_call(case, *, params=None):
    representative_values = {
        "batch_size": 25,
        "input_table": "raw_signals",
        "output_table": "normalized_signals",
        "run_date": "2026-07-10",
        "status_filter": "pending",
    }
    call_params = {}
    for param_name in case.required_param_names:
        call_params[param_name] = representative_values[param_name]
    if params is not None:
        call_params = params
    return SimpleNamespace(
        tool_name=CustomToolResultContractScenario._custom_tool_name(case),
        tool_params=call_params,
        result={
            "status": "ok",
            "side_effects_completed": True,
            "summary": "Completed representative work.",
            "side_effects": [{"target": "signals", "items_written": 20}],
            "source": {"filters": {"status_filter": "pending"}},
            "verification": {"mode": "read_only"},
            "remaining_work": {"count": 0, "next_cursor": None},
            "next_action": "Verify read-only.",
            "do_not_repeat_manually": True,
            "warnings": [],
        },
        status="complete",
        step=None,
    )


def _create_file_call(case):
    return SimpleNamespace(
        tool_name="create_file",
        tool_params={
            "file_path": f"/tools/{case.slug}.py",
            "mime_type": "text/x-python",
            "content": _source_code(),
        },
        result={"status": "ok", "file": f"$[/tools/{case.slug}.py]"},
        step=None,
    )


def _apply_patch_call(case, old_text, new_text):
    return SimpleNamespace(
        tool_name="apply_patch",
        tool_params={
            "patch": "\n".join([
                "*** Begin Patch",
                f"*** Update File: /tools/{case.slug}.py",
                "@@",
                *[f"-{line}" for line in old_text.splitlines()],
                *[f"+{line}" for line in new_text.splitlines()],
                "*** End Patch",
            ]),
        },
        result={"status": "ok", "updated": [f"/tools/{case.slug}.py"]},
        step=None,
    )


@tag("batch_eval_fingerprint")
class CustomToolResultContractEvalTests(TestCase):
    def test_suite_and_scenarios_are_registered_with_agent_processing_tasks(self):
        registered = ScenarioRegistry.list_all()
        suite = SuiteRegistry.get(CUSTOM_TOOL_RESULT_CONTRACT_SUITE_SLUG)

        self.assertIsNotNone(suite)
        self.assertEqual(len(CUSTOM_TOOL_RESULT_CONTRACT_SCENARIO_SLUGS), 6)
        self.assertEqual(suite.scenario_slugs, CUSTOM_TOOL_RESULT_CONTRACT_SCENARIO_SLUGS)
        for slug in CUSTOM_TOOL_RESULT_CONTRACT_SCENARIO_SLUGS:
            self.assertIn(slug, registered)
            self.assertEqual(
                [task.name for task in registered[slug].tasks],
                [
                    "inject_prompt",
                    "propose_result_contract",
                    "invoke_custom_tool",
                    "judge_result_helpfulness",
                ],
            )

    def test_agent_prompt_is_minimal_and_scenario_specific(self):
        case = _case("sheets_final_sync")
        prompt = CustomToolResultContractScenario._agent_prompt(case)

        self.assertIn("custom_sheets_final_sync", prompt)
        self.assertIn(case.real_world_basis, prompt)
        self.assertIn(case.user_task, prompt)
        self.assertIn(case.custom_tool_job, prompt)
        self.assertNotIn("create_custom_tool", prompt)
        self.assertNotIn("source_code", prompt)
        self.assertNotIn("parameters_schema", prompt)
        self.assertNotIn("def run(params, ctx)", prompt)
        self.assertNotIn("Do not call sqlite_batch", prompt)
        self.assertNotIn("spreadsheet_id, run_date", prompt)
        self.assertNotIn("do_not_repeat_manually=true", prompt)

    def test_batching_stop_policy_waits_for_bounded_invocation(self):
        case = _case("sheets_backlog_sync")
        policy = CustomToolResultContractScenario._eval_stop_policy(
            case,
            CustomToolResultContractScenario._custom_tool_name(case),
        )

        self.assertEqual(policy["max_relevant_tool_calls"], 24)
        self.assertNotIn("stop_on_tool_names_after_execution", policy)
        self.assertEqual(
            policy["stop_when_all_seen"][0]["required_params_any"],
            list(("batch_size", "batch_limit", "limit", "max_items", "max_rows", "row_limit")),
        )

    def test_local_create_tool_check_requires_good_params_and_result_fields(self):
        case = _case("sheets_final_sync")
        ok, reason = CustomToolResultContractScenario._local_create_tool_check(
            case,
            _create_call(case),
            "custom_sheets_final_sync",
        )

        self.assertTrue(ok, reason)

    def test_param_checks_accept_semantic_aliases_from_minimal_prompts(self):
        case = _case("sheets_final_sync")
        schema = {
            "type": "object",
            "properties": {
                "source_signal_table": {"type": "string"},
                "dest_signal_table": {"type": "string"},
                "sync_date": {"type": "string"},
                "dry_run": {"type": "boolean"},
            },
            "required": ["source_signal_table", "dest_signal_table"],
        }

        create_ok, create_reason = CustomToolResultContractScenario._local_create_tool_check(
            case,
            _create_call(case, schema=schema),
            "custom_sheets_final_sync",
        )
        invoke_ok, invoke_reason = CustomToolResultContractScenario._local_custom_call_check(
            case,
            _custom_call(
                case,
                params={
                    "source_signal_table": "signals",
                    "dest_signal_table": "sheet_signals",
                    "date_filter": "2026-05-19",
                    "dry_run": False,
                },
            ),
        )

        self.assertTrue(create_ok, create_reason)
        self.assertTrue(invoke_ok, invoke_reason)

    def test_param_checks_accept_common_destination_and_pending_aliases(self):
        for case_slug, properties, invocation in (
            (
                "dedupe_format_signals",
                {"source_table": {"type": "string"}, "dest_table": {"type": "string"}},
                {"source_table": "raw_signals", "dest_table": "formatted_signals"},
            ),
            (
                "sheets_backlog_sync",
                {
                    "source_table": {"type": "string"},
                    "dest_table": {"type": "string"},
                    "batch_limit": {"type": "integer"},
                    "pending_value": {"type": "string"},
                },
                {
                    "source_table": "pending_signals",
                    "dest_table": "sheet_rows",
                    "batch_limit": 25,
                    "pending_value": "pending",
                },
            ),
        ):
            case = _case(case_slug)
            schema = {"type": "object", "properties": properties, "required": list(properties)}
            create_ok, create_reason = CustomToolResultContractScenario._local_create_tool_check(
                case,
                _create_call(case, schema=schema),
                CustomToolResultContractScenario._custom_tool_name(case),
            )
            invoke_ok, invoke_reason = CustomToolResultContractScenario._local_custom_call_check(
                case,
                _custom_call(case, params=invocation),
            )

            self.assertTrue(create_ok, create_reason)
            self.assertTrue(invoke_ok, invoke_reason)

    def test_param_checks_accept_case_specific_semantic_groups(self):
        examples = (
            (
                "sheets_final_sync",
                {
                    "signal_table": {"type": "string"},
                    "run_log_table": {"type": "string"},
                    "signal_worksheet": {"type": "string"},
                    "run_log_worksheet": {"type": "string"},
                    "date_from": {"type": "string"},
                },
                {
                    "signal_table": "signals",
                    "run_log_table": "run_log",
                    "signal_worksheet": "signals",
                    "run_log_worksheet": "Run Log",
                    "date_from": "2026-07-03T00:00:00Z",
                },
            ),
            (
                "dedupe_format_signals",
                {"source_table": {"type": "string"}, "result_table": {"type": "string"}},
                {"source_table": "raw_signals", "result_table": "deduped_signals"},
            ),
            (
                "scrape_url_normalization",
                {"domains": {"type": "array"}, "result_table": {"type": "string"}},
                {"domains": ["example.com", "openai.com"], "result_table": "scrape_ready"},
            ),
            (
                "linkedin_post_urls",
                {"urls": {"type": "array"}, "result_table": {"type": "string"}},
                {"urls": ["https://linkedin.com/posts/example"], "result_table": "direct_posts"},
            ),
        )
        for case_slug, properties, invocation in examples:
            with self.subTest(case=case_slug):
                case = _case(case_slug)
                schema = json.dumps({"type": "object", "properties": properties})
                create_ok, create_reason = CustomToolResultContractScenario._local_create_tool_check(
                    case,
                    _create_call(case, schema=schema),
                    CustomToolResultContractScenario._custom_tool_name(case),
                )
                invoke_ok, invoke_reason = CustomToolResultContractScenario._local_custom_call_check(
                    case,
                    _custom_call(case, params=invocation),
                )
                self.assertTrue(create_ok, create_reason)
                self.assertTrue(invoke_ok, invoke_reason)

    def test_scrape_semantic_inputs_must_be_explicit_and_nonempty(self):
        case = _case("scrape_url_normalization")
        ok, reason = CustomToolResultContractScenario._local_custom_call_check(
            case,
            _custom_call(case, params={"urls": [], "result_table": "scrape_ready"}),
        )

        self.assertFalse(ok)
        self.assertIn("input_table", reason)

    def test_case_specific_aliases_do_not_leak_to_unrelated_cases(self):
        case = _case("dedupe_format_signals")
        schema = {
            "type": "object",
            "properties": {"domains": {"type": "array"}, "result_table": {"type": "string"}},
        }

        ok, reason = CustomToolResultContractScenario._local_create_tool_check(
            case,
            _create_call(case, schema=schema),
            CustomToolResultContractScenario._custom_tool_name(case),
        )

        self.assertFalse(ok)
        self.assertIn("input_table", reason)

    def test_create_tool_check_accepts_source_written_to_filespace_before_registration(self):
        case = _case("linkedin_post_urls")
        create_call = _create_call(case)
        create_call.tool_params.pop("source_code")
        tool_calls = [_create_file_call(case), create_call]

        source_code = CustomToolResultContractScenario._source_code_for_create_call(
            tool_calls,
            create_call,
        )
        ok, reason = CustomToolResultContractScenario._local_create_tool_check(
            case,
            create_call,
            CustomToolResultContractScenario._custom_tool_name(case),
            source_code_override=source_code,
        )

        self.assertTrue(ok, reason)
        self.assertEqual(source_code, _source_code())

    def test_source_lookup_accepts_json_string_create_file_result(self):
        case = _case("linkedin_post_urls")
        create_call = _create_call(case)
        create_call.tool_params.pop("source_code")
        create_file_call = _create_file_call(case)
        create_file_call.result = json.dumps(create_file_call.result)

        source_code = CustomToolResultContractScenario._source_code_for_create_call(
            [create_file_call, create_call],
            create_call,
        )

        self.assertEqual(source_code, _source_code())

    def test_source_lookup_applies_apply_patch_before_registration(self):
        case = _case("linkedin_post_urls")
        create_call = _create_call(case)
        create_call.tool_params.pop("source_code")
        old_text = "if __name__ == \"__main__\":\n    main(run)"
        new_text = "if __name__ == \"__main__\": main(run)"
        tool_calls = [_create_file_call(case), _apply_patch_call(case, old_text, new_text), create_call]

        source_code = CustomToolResultContractScenario._source_code_for_create_call(
            tool_calls,
            create_call,
        )

        self.assertIn(new_text, source_code)
        self.assertNotIn(old_text, source_code)

    def test_eval_apply_patch_helper_treats_empty_lines_as_context(self):
        patch_text = "\n".join([
            "*** Begin Patch",
            "*** Update File: /tools/example.py",
            "@@",
            " first",
            "",
            "-third",
            "+fourth",
            "*** End Patch",
        ])

        content = CustomToolResultContractScenario._apply_patch_to_content(
            "first\n\nthird",
            patch_text,
            "/tools/example.py",
        )

        self.assertEqual(content, "first\n\nfourth")

    def test_local_create_tool_check_catches_missing_params(self):
        case = _case("scrape_url_normalization")
        schema = {"type": "object", "properties": {}, "required": []}

        ok, reason = CustomToolResultContractScenario._local_create_tool_check(
            case,
            _create_call(case, schema=schema),
            "custom_scrape_url_normalization",
        )

        self.assertFalse(ok)
        self.assertIn("useful runtime params", reason)

    def test_local_create_tool_check_rejects_unrelated_nonempty_params(self):
        case = _case("scrape_url_normalization")
        schema = {
            "type": "object",
            "properties": {"dry_run": {"type": "boolean"}},
            "required": ["dry_run"],
        }

        ok, reason = CustomToolResultContractScenario._local_create_tool_check(
            case,
            _create_call(case, schema=schema),
            "custom_scrape_url_normalization",
        )

        self.assertFalse(ok)
        self.assertIn("input_table", reason)
        self.assertIn("output_table", reason)

    def test_local_create_tool_check_allows_optional_schema_fields_when_invocation_is_checked(self):
        case = _case("sheets_final_sync")
        schema = _parameters_schema(case)
        schema["required"] = []

        ok, reason = CustomToolResultContractScenario._local_create_tool_check(
            case,
            _create_call(case, schema=schema),
            "custom_sheets_final_sync",
        )

        self.assertTrue(ok, reason)

    def test_local_create_tool_check_catches_missing_result_fields(self):
        case = _case("sheets_final_sync")
        sparse_source = """
from _gobii_ctx import main

def run(params, ctx):
    return {"status": "ok"}

if __name__ == "__main__":
    main(run)
"""
        ok, reason = CustomToolResultContractScenario._local_create_tool_check(
            case,
            _create_call(case, source_code=sparse_source),
            "custom_sheets_final_sync",
        )

        self.assertFalse(ok)
        self.assertIn("helpful result signal", reason)

    def test_local_create_tool_check_accepts_structured_side_effect_summary(self):
        case = _case("sheets_backlog_sync")
        source = _source_code(extra_fields='"do_not_repeat_manually": True,').replace(
            "Use read-only verification; do not replay append/add/update calls.",
            "Verify completed rows.",
        )

        ok, reason = CustomToolResultContractScenario._local_create_tool_check(
            case,
            _create_call(case, source_code=source),
            "custom_sheets_backlog_sync",
        )

        self.assertTrue(ok, reason)

    def test_local_create_tool_check_does_not_require_replay_prevention_in_source(self):
        case = _case("sheets_backlog_sync")
        source = _source_code().replace(
            "Use read-only verification; do not replay append/add/update calls.",
            "Verify completed rows.",
        )

        ok, reason = CustomToolResultContractScenario._local_create_tool_check(
            case,
            _create_call(case, source_code=source),
            "custom_sheets_backlog_sync",
        )

        self.assertTrue(ok, reason)

    def test_local_create_tool_check_accepts_ready_outputs_without_next_action_field(self):
        case = _case("scrape_url_normalization")
        source = """
from _gobii_ctx import main

def run(params, ctx):
    accepted = ["https://stripe.com"]
    rejected = [{"input": "not a domain", "reason": "not a valid domain"}]
    return {
        "status": "ok",
        "summary": "1 accepted, 1 rejected.",
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
        "source": {"input": params.get("input_table"), "output": params.get("output_table")},
        "scrape_ready_urls": accepted,
        "rejected_urls": rejected,
    }

if __name__ == "__main__":
    main(run)
"""

        ok, reason = CustomToolResultContractScenario._local_create_tool_check(
            case,
            _create_call(case, source_code=source),
            "custom_scrape_url_normalization",
        )

        self.assertTrue(ok, reason)

    def test_local_create_tool_check_accepts_accepted_url_outputs_without_next_action_field(self):
        case = _case("linkedin_post_urls")
        source = """
from _gobii_ctx import main

def run(params, ctx):
    urls = params.get("urls", [])
    valid_post_urls = [url for url in urls if "/posts/" in url]
    rejected_urls = [url for url in urls if url not in valid_post_urls]
    return {
        "status": "ok",
        "summary": f"{len(valid_post_urls)} valid posts, {len(rejected_urls)} rejected.",
        "total": len(urls),
        "accepted_count": len(valid_post_urls),
        "rejected_count": len(rejected_urls),
        "source": {"input": "urls param"},
        "valid_post_urls": valid_post_urls,
        "rejected_urls": rejected_urls,
    }

if __name__ == "__main__":
    main(run)
"""

        ok, reason = CustomToolResultContractScenario._local_create_tool_check(
            case,
            _create_call(case, source_code=source),
            "custom_linkedin_post_urls",
        )

        self.assertTrue(ok, reason)

    def test_batching_cases_require_batch_handling_in_create_and_invoke(self):
        for case_slug in ("sheets_backlog_sync", "chunked_mcp_fanout"):
            case = _case(case_slug)
            create_ok, create_reason = CustomToolResultContractScenario._local_create_tool_check(
                case,
                _create_call(case),
                CustomToolResultContractScenario._custom_tool_name(case),
            )
            invoke_ok, invoke_reason = CustomToolResultContractScenario._local_custom_call_check(
                case,
                _custom_call(case),
            )

            self.assertTrue(create_ok, create_reason)
            self.assertTrue(invoke_ok, invoke_reason)

    def test_local_custom_call_check_catches_missing_batching_param(self):
        case = _case("sheets_backlog_sync")
        ok, reason = CustomToolResultContractScenario._local_custom_call_check(
            case,
            _custom_call(case, params={"status_filter": "pending"}),
        )

        self.assertFalse(ok)
        self.assertIn("batch_size", reason)

    def test_local_custom_call_check_accepts_semantic_status_default(self):
        case = _case("chunked_mcp_fanout")
        schema = _parameters_schema(case)
        schema["properties"]["status_filter"]["default"] = "pending"
        schema["required"].remove("status_filter")
        params = _custom_call(case).tool_params
        params.pop("status_filter")

        ok, reason = CustomToolResultContractScenario._local_custom_call_check(
            case,
            _custom_call(case, params=params),
            create_call=_create_call(case, schema=schema),
        )

        self.assertTrue(ok, reason)

    def test_status_default_does_not_replace_required_or_invalid_value(self):
        case = _case("chunked_mcp_fanout")
        schema = _parameters_schema(case)
        schema["properties"]["status_filter"]["default"] = "pending"
        schema["properties"]["status"] = {"type": "string", "default": "pending"}
        params = _custom_call(case).tool_params
        params.pop("status_filter")

        required_ok, required_reason = CustomToolResultContractScenario._local_custom_call_check(
            case,
            _custom_call(case, params=params),
            create_call=_create_call(case, schema=schema),
        )
        schema["required"].remove("status_filter")
        params["status"] = ""
        invalid_ok, invalid_reason = CustomToolResultContractScenario._local_custom_call_check(
            case,
            _custom_call(case, params=params),
            create_call=_create_call(case, schema=schema),
        )

        self.assertFalse(required_ok)
        self.assertIn("status_filter", required_reason)
        self.assertFalse(invalid_ok)
        self.assertIn("invalid runtime value", invalid_reason)

    def test_run_date_alias_accepts_iso_datetime(self):
        case = _case("sheets_final_sync")
        params = _custom_call(case).tool_params
        params.pop("run_date")
        params["since_date"] = "2026-07-03T00:00:00Z"

        ok, reason = CustomToolResultContractScenario._local_custom_call_check(
            case,
            _custom_call(case, params=params),
        )

        self.assertTrue(ok, reason)

    def test_local_custom_call_check_rejects_unrelated_nonempty_params(self):
        case = _case("dedupe_format_signals")

        ok, reason = CustomToolResultContractScenario._local_custom_call_check(
            case,
            _custom_call(case, params={"dry_run": True}),
        )

        self.assertFalse(ok)
        self.assertIn("input_table", reason)
        self.assertIn("output_table", reason)

    def test_local_custom_call_check_rejects_placeholder_or_invalid_values(self):
        case = _case("scrape_url_normalization")
        custom_call = _custom_call(
            case,
            params={
                "input_table": "sample",
                "output_table": "sample",
            },
        )

        ok, reason = CustomToolResultContractScenario._local_custom_call_check(case, custom_call)

        self.assertFalse(ok)
        self.assertIn("invalid runtime value", reason)

    def test_sheet_case_rejects_invalid_generic_output_table_identifier(self):
        case = _case("sheets_final_sync")
        params = _custom_call(case).tool_params
        params["output_table"] = "!!!"

        ok, reason = CustomToolResultContractScenario._local_custom_call_check(
            case,
            _custom_call(case, params=params),
        )

        self.assertFalse(ok)
        self.assertIn("output_table", reason)

    def test_collection_case_rejects_invalid_generic_input_table_identifier(self):
        case = _case("scrape_url_normalization")
        params = _custom_call(case).tool_params
        params["input_table"] = "!!!"

        ok, reason = CustomToolResultContractScenario._local_custom_call_check(
            case,
            _custom_call(case, params=params),
        )

        self.assertFalse(ok)
        self.assertIn("input_table", reason)

    def test_tool_call_success_rejects_nonterminal_warning_and_nested_failure(self):
        complete = _create_call(_case("sheets_final_sync"))
        self.assertTrue(CustomToolResultContractScenario._tool_call_succeeded(complete))

        for status, result in (
            ("pending", {"status": "created"}),
            ("error", {"status": "created"}),
            ("complete", {"status": "warning"}),
            ("complete", {"status": "ok", "error": "bad input"}),
            ("complete", {"status": "ok", "result": {"status": "failed"}}),
        ):
            with self.subTest(status=status, result=result):
                call = _create_call(_case("sheets_final_sync"))
                call.status = status
                call.result = result
                self.assertFalse(CustomToolResultContractScenario._tool_call_succeeded(call))

    def test_local_custom_call_check_catches_runtime_error_results(self):
        case = _case("sheets_backlog_sync")
        custom_call = _custom_call(case)
        custom_call.result = json.dumps({"status": "error", "message": "TypeError: bad row conversion"})

        ok, reason = CustomToolResultContractScenario._local_custom_call_check(case, custom_call)

        self.assertFalse(ok)
        self.assertIn("errored", reason)

    def test_local_custom_call_check_catches_persisted_error_status(self):
        case = _case("sheets_backlog_sync")
        custom_call = _custom_call(case)
        custom_call.status = "error"
        custom_call.result = json.dumps({"message": "sandbox failed"})

        ok, reason = CustomToolResultContractScenario._local_custom_call_check(case, custom_call)

        self.assertFalse(ok)
        self.assertIn("sandbox failed", reason)

    def test_local_custom_call_check_allows_dry_run_without_replay_prevention(self):
        case = _case("sheets_final_sync")
        custom_call = _custom_call(case)
        custom_call.result = {
            "status": "ok",
            "result": {
                "status": "dry_run_complete",
                "dry_run": True,
                "signals_to_sync": 5,
                "next_action": "Call again with dry_run=false to perform the actual sync",
            },
        }

        ok, reason = CustomToolResultContractScenario._local_custom_call_check(case, custom_call)

        self.assertTrue(ok, reason)

    def test_local_custom_call_check_accepts_completed_writes_without_replay_prevention(self):
        case = _case("sheets_final_sync")
        custom_call = _custom_call(case)
        custom_call.result = {
            "status": "ok",
            "result": {
                "status": "sync_complete",
                "rows_written": 5,
                "remaining_work": False,
                "next_action": "Verify destination tables.",
            },
        }

        ok, reason = CustomToolResultContractScenario._local_custom_call_check(case, custom_call)

        self.assertTrue(ok, reason)

    def test_agent_judge_context_includes_actual_tool_calls_and_rubric(self):
        case = _case("chunked_mcp_fanout")
        context = CustomToolResultContractScenario._agent_judge_context(
            case,
            _create_call(case),
            _custom_call(case),
        )

        self.assertIn("create_custom_tool", context)
        self.assertIn("custom_tool_invocation", context)
        self.assertIn("batching", context)
        self.assertIn("helpful_param_concepts", context)
        self.assertIn("must include bounded batch params", context)
        self.assertIn("The agent must use create_custom_tool", context)

    def test_agent_judge_context_decodes_json_string_custom_result(self):
        case = _case("chunked_mcp_fanout")
        custom_call = _custom_call(case)
        custom_call.result = json.dumps(custom_call.result)

        context = json.loads(
            CustomToolResultContractScenario._agent_judge_context(
                case,
                _create_call(case),
                custom_call,
            )
        )

        self.assertIsInstance(context["custom_tool_invocation"]["result"], dict)
        self.assertEqual(context["custom_tool_invocation"]["result"]["status"], "ok")

    def test_run_skips_llm_judge_when_local_checks_fail(self):
        case = _case("dedupe_format_signals")
        scenario = CustomToolResultContractScenario()
        scenario.case = case
        create_call = _create_call(case)
        custom_call = _custom_call(case, params={})
        records = []

        def record_task_result(run_id, step, status, *, task_name, observed_summary="", **kwargs):
            records.append(
                {
                    "task_name": task_name,
                    "status": status,
                    "observed_summary": observed_summary,
                }
            )

        def fail_judge(**kwargs):
            self.fail("LLM judge should not run after prerequisite local checks fail.")

        scenario.wait_for_agent_idle = lambda *args, **kwargs: nullcontext()
        scenario.inject_message = lambda *args, **kwargs: SimpleNamespace(timestamp=timezone.now())
        scenario._tool_calls_for_run = lambda *args, **kwargs: [create_call, custom_call]
        scenario.record_task_result = record_task_result
        scenario.llm_judge = fail_judge

        scenario.run(str(uuid.uuid4()), str(uuid.uuid4()))

        judge_records = [
            record for record in records if record["task_name"] == "judge_result_helpfulness"
        ]
        self.assertEqual(judge_records[-1]["status"], EvalRunTask.Status.SKIPPED)
        self.assertIn("prerequisite local checks failed", judge_records[-1]["observed_summary"])
        self.assertIn("invoke_custom_tool", judge_records[-1]["observed_summary"])

    def test_run_scores_latest_valid_repaired_custom_tool(self):
        case = _case("chunked_mcp_fanout")
        scenario = CustomToolResultContractScenario()
        scenario.case = case
        first_create = _create_call(
            case,
            source_code=(
                "from _gobii_ctx import main\n\n"
                "def run(params, ctx):\n"
                "    return {'status': 'ok'}\n\n"
                "main(run)\n"
            ),
        )
        second_create = _create_call(case, source_code=first_create.tool_params["source_code"])
        final_create = _create_call(case)
        final_create.tool_params["description"] = "Repaired custom tool with a complete result contract."
        custom_call = _custom_call(case)
        records = []

        def record_task_result(run_id, step, status, *, task_name, observed_summary="", **kwargs):
            records.append(
                {
                    "task_name": task_name,
                    "status": status,
                    "observed_summary": observed_summary,
                    "artifacts": kwargs.get("artifacts") or {},
                }
            )

        scenario.wait_for_agent_idle = lambda *args, **kwargs: nullcontext()
        scenario.inject_message = lambda *args, **kwargs: SimpleNamespace(timestamp=timezone.now())
        scenario._tool_calls_for_run = lambda *args, **kwargs: [first_create, second_create, final_create, custom_call]
        scenario.record_task_result = record_task_result
        scenario.llm_judge = lambda **kwargs: ("Yes", "The repaired result contract is useful.")

        scenario.run(str(uuid.uuid4()), str(uuid.uuid4()))

        proposal = [record for record in records if record["task_name"] == "propose_result_contract"][-1]
        invoke = [record for record in records if record["task_name"] == "invoke_custom_tool"][-1]
        judge = [record for record in records if record["task_name"] == "judge_result_helpfulness"][-1]
        self.assertEqual(proposal["status"], EvalRunTask.Status.PASSED)
        self.assertEqual(
            proposal["artifacts"]["create_params"]["description"],
            "Repaired custom tool with a complete result contract.",
        )
        self.assertEqual(proposal["artifacts"]["create_tool_call_count"], 3)
        self.assertEqual(proposal["artifacts"]["create_tool_repair_count"], 2)
        self.assertEqual(invoke["status"], EvalRunTask.Status.PASSED)
        self.assertEqual(judge["status"], EvalRunTask.Status.PASSED)

    def test_run_rejects_more_than_two_create_tool_repairs(self):
        case = _case("chunked_mcp_fanout")
        scenario = CustomToolResultContractScenario()
        scenario.case = case
        create_calls = [_create_call(case) for _index in range(4)]
        custom_call = _custom_call(case)
        records = []

        def record_task_result(run_id, step, status, *, task_name, observed_summary="", **kwargs):
            records.append(
                {
                    "task_name": task_name,
                    "status": status,
                    "observed_summary": observed_summary,
                }
            )

        scenario.wait_for_agent_idle = lambda *args, **kwargs: nullcontext()
        scenario.inject_message = lambda *args, **kwargs: SimpleNamespace(timestamp=timezone.now())
        scenario._tool_calls_for_run = lambda *args, **kwargs: [*create_calls, custom_call]
        scenario.record_task_result = record_task_result
        scenario.llm_judge = lambda **kwargs: self.fail("LLM judge should not run after excess repairs.")

        scenario.run(str(uuid.uuid4()), str(uuid.uuid4()))

        proposal = [record for record in records if record["task_name"] == "propose_result_contract"][-1]
        judge = [record for record in records if record["task_name"] == "judge_result_helpfulness"][-1]
        self.assertEqual(proposal["status"], EvalRunTask.Status.FAILED)
        self.assertIn("3-attempt repair limit", proposal["observed_summary"])
        self.assertEqual(judge["status"], EvalRunTask.Status.SKIPPED)

    def test_run_does_not_fall_back_past_latest_successful_invalid_definition(self):
        case = _case("chunked_mcp_fanout")
        scenario = CustomToolResultContractScenario()
        scenario.case = case
        valid_create = _create_call(case)
        invalid_create = _create_call(
            case,
            source_code=(
                "from _gobii_ctx import main\n\n"
                "def run(params, ctx):\n"
                "    return {'status': 'ok'}\n\n"
                "main(run)\n"
            ),
        )
        invalid_create.tool_params["description"] = "Latest completed but incomplete definition."
        custom_call = _custom_call(case)
        records = []

        def record_task_result(run_id, step, status, *, task_name, observed_summary="", **kwargs):
            records.append(
                {
                    "task_name": task_name,
                    "status": status,
                    "observed_summary": observed_summary,
                    "artifacts": kwargs.get("artifacts") or {},
                }
            )

        scenario.wait_for_agent_idle = lambda *args, **kwargs: nullcontext()
        scenario.inject_message = lambda *args, **kwargs: SimpleNamespace(timestamp=timezone.now())
        scenario._tool_calls_for_run = lambda *args, **kwargs: [valid_create, invalid_create, custom_call]
        scenario.record_task_result = record_task_result
        scenario.llm_judge = lambda **kwargs: self.fail("LLM judge should not run after an invalid active definition.")

        scenario.run(str(uuid.uuid4()), str(uuid.uuid4()))

        proposal = [record for record in records if record["task_name"] == "propose_result_contract"][-1]
        judge = [record for record in records if record["task_name"] == "judge_result_helpfulness"][-1]
        self.assertEqual(proposal["status"], EvalRunTask.Status.FAILED)
        self.assertEqual(
            proposal["artifacts"]["create_params"]["description"],
            "Latest completed but incomplete definition.",
        )
        self.assertEqual(judge["status"], EvalRunTask.Status.SKIPPED)

    def test_run_requires_invocation_after_latest_successful_definition(self):
        case = _case("sheets_final_sync")
        scenario = CustomToolResultContractScenario()
        scenario.case = case
        first_create = _create_call(case)
        early_custom_call = _custom_call(case)
        latest_create = _create_call(case)
        latest_create.tool_params["description"] = "Latest active definition."
        records = []

        def record_task_result(run_id, step, status, *, task_name, observed_summary="", **kwargs):
            records.append({"task_name": task_name, "status": status, "observed_summary": observed_summary})

        scenario.wait_for_agent_idle = lambda *args, **kwargs: nullcontext()
        scenario.inject_message = lambda *args, **kwargs: SimpleNamespace(timestamp=timezone.now())
        scenario._tool_calls_for_run = lambda *args, **kwargs: [first_create, early_custom_call, latest_create]
        scenario.record_task_result = record_task_result
        scenario.llm_judge = lambda **kwargs: self.fail("LLM judge should not run without a post-definition invocation.")

        scenario.run(str(uuid.uuid4()), str(uuid.uuid4()))

        invoke = [record for record in records if record["task_name"] == "invoke_custom_tool"][-1]
        judge = [record for record in records if record["task_name"] == "judge_result_helpfulness"][-1]
        self.assertEqual(invoke["status"], EvalRunTask.Status.FAILED)
        self.assertIn("did not invoke", invoke["observed_summary"])
        self.assertEqual(judge["status"], EvalRunTask.Status.FAILED)
