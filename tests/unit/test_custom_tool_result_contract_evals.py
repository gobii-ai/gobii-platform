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
            "type": "integer" if param_name in {"batch_size", "limit", "min_posts"} else "string"
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
        step=None,
    )


def _custom_call(case, *, params=None):
    call_params = {}
    for param_name in case.required_param_names:
        call_params[param_name] = 25 if param_name in {"batch_size", "limit", "min_posts"} else "sample"
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
            "warnings": [],
        },
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


def _file_str_replace_call(case, old_text, new_text):
    return SimpleNamespace(
        tool_name="file_str_replace",
        tool_params={
            "path": f"/tools/{case.slug}.py",
            "old_text": old_text,
            "new_text": new_text,
            "replace_all": False,
        },
        result={"status": "ok", "replacements": 1},
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

        self.assertIn("Build a reusable Python custom tool", prompt)
        self.assertIn("custom_sheets_final_sync", prompt)
        self.assertIn(case.real_world_basis, prompt)
        self.assertIn(case.user_task, prompt)
        self.assertIn(case.custom_tool_job, prompt)
        self.assertIn("representative sample data", prompt)
        self.assertIn("do not perform real external writes", prompt)
        self.assertIn("runtime parameters for source inputs/tables", prompt)
        self.assertIn("pass concrete representative values", prompt)
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
                "sync_date": {"type": "string"},
                "dry_run": {"type": "boolean"},
            },
            "required": ["sync_date"],
        }

        create_ok, create_reason = CustomToolResultContractScenario._local_create_tool_check(
            case,
            _create_call(case, schema=schema),
            "custom_sheets_final_sync",
        )
        invoke_ok, invoke_reason = CustomToolResultContractScenario._local_custom_call_check(
            case,
            _custom_call(case, params={"date_filter": "2026-05-19", "dry_run": False}),
        )

        self.assertTrue(create_ok, create_reason)
        self.assertTrue(invoke_ok, invoke_reason)

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

    def test_source_lookup_applies_file_str_replace_before_registration(self):
        case = _case("linkedin_post_urls")
        create_call = _create_call(case)
        create_call.tool_params.pop("source_code")
        old_text = "if __name__ == \"__main__\":\n    main(run)"
        new_text = "if __name__ == \"__main__\": main(run)"
        tool_calls = [_create_file_call(case), _file_str_replace_call(case, old_text, new_text), create_call]

        source_code = CustomToolResultContractScenario._source_code_for_create_call(
            tool_calls,
            create_call,
        )

        self.assertIn(new_text, source_code)
        self.assertNotIn(old_text, source_code)

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

    def test_local_create_tool_check_accepts_structured_manual_replay_prevention(self):
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
