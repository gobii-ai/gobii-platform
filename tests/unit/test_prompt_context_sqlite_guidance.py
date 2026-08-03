from decimal import Decimal
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, tag

from api.agent.core import prompt_context
from api.models import (
    BrowserUseAgent,
    CommsAllowlistEntry,
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    UserPhoneNumber,
)


@tag("batch_promptree")
class PromptContextSqliteGuidanceTests(SimpleTestCase):
    def test_successful_terminal_sqlite_result_suppresses_immediate_model_guidance(self):
        started_at = datetime(2026, 8, 3, 1, 0, tzinfo=timezone.utc)
        inbound = SimpleNamespace(timestamp=started_at)
        result = prompt_context.ToolCallResultRecord(
            step_id="sqlite-terminal",
            tool_name="sqlite_batch",
            created_at=started_at + timedelta(seconds=1),
            result_text='{"status": "ok", "results": [{"result": [{"id": 1}]}]}',
            will_continue_work=False,
        )

        self.assertTrue(
            prompt_context._is_terminal_sqlite_handoff([result], [inbound])
        )

    def test_config_only_terminal_sqlite_write_keeps_model_guidance(self):
        result = prompt_context.ToolCallResultRecord(
            step_id="sqlite-config",
            tool_name="sqlite_batch",
            created_at=datetime(2026, 8, 3, 1, 0, tzinfo=timezone.utc),
            result_text=(
                '{"status": "ok", "results": [{"message": "Query 0 affected 1 rows."}], '
                '"agent_config_update": {"updated_fields": ["charter"]}}'
            ),
            will_continue_work=False,
        )

        self.assertFalse(prompt_context._is_terminal_sqlite_handoff([result], []))

    def test_terminal_sqlite_handoff_requires_the_newest_successful_event(self):
        started_at = datetime(2026, 8, 3, 1, 0, tzinfo=timezone.utc)
        terminal = prompt_context.ToolCallResultRecord(
            step_id="sqlite-terminal",
            tool_name="sqlite_batch",
            created_at=started_at + timedelta(seconds=1),
            result_text='{"status": "ok"}',
            will_continue_work=False,
        )
        continuing = prompt_context.ToolCallResultRecord(
            step_id="sqlite-continuing",
            tool_name="sqlite_batch",
            created_at=started_at + timedelta(seconds=2),
            result_text='{"status": "ok"}',
            will_continue_work=True,
        )
        failed = prompt_context.ToolCallResultRecord(
            step_id="sqlite-failed",
            tool_name="sqlite_batch",
            created_at=started_at + timedelta(seconds=2),
            result_text='{"status": "error"}',
            will_continue_work=False,
        )
        source = prompt_context.ToolCallResultRecord(
            step_id="source",
            tool_name="http_request",
            created_at=started_at + timedelta(seconds=2),
            result_text='{"status": "ok"}',
            will_continue_work=False,
        )
        newer_message = SimpleNamespace(timestamp=started_at + timedelta(seconds=2))

        self.assertFalse(
            prompt_context._is_terminal_sqlite_handoff(
                [terminal, continuing],
                [],
            )
        )
        self.assertFalse(
            prompt_context._is_terminal_sqlite_handoff([terminal, failed], [])
        )
        self.assertFalse(
            prompt_context._is_terminal_sqlite_handoff([terminal, source], [])
        )
        self.assertFalse(
            prompt_context._is_terminal_sqlite_handoff(
                [terminal],
                [newer_message],
            )
        )

    def test_active_source_batch_spans_completions_for_one_request(self):
        process_started = datetime(2026, 7, 27, 9, 0, tzinfo=timezone.utc)
        process_step = SimpleNamespace(
            id=uuid4(),
            created_at=process_started,
            system_step=SimpleNamespace(code="PROCESS_EVENTS"),
        )
        inbound = SimpleNamespace(
            id=uuid4(),
            timestamp=process_started + timedelta(seconds=1),
            is_outbound=False,
        )
        later_outbound = SimpleNamespace(
            id=uuid4(),
            timestamp=process_started + timedelta(seconds=2),
            is_outbound=True,
        )

        batch_id, started_at = prompt_context._active_source_batch(
            [process_step],
            [inbound, later_outbound],
        )

        self.assertEqual(batch_id, str(inbound.id))
        self.assertEqual(started_at, inbound.timestamp)

    def test_new_inbound_message_starts_a_new_source_batch(self):
        process_started = datetime(2026, 7, 27, 9, 0, tzinfo=timezone.utc)
        process_step = SimpleNamespace(
            id=uuid4(),
            created_at=process_started,
            system_step=SimpleNamespace(code="PROCESS_EVENTS"),
        )
        first_inbound = SimpleNamespace(
            id=uuid4(),
            timestamp=process_started + timedelta(seconds=1),
            is_outbound=False,
        )
        correction = SimpleNamespace(
            id=uuid4(),
            timestamp=process_started + timedelta(seconds=8),
            is_outbound=False,
        )

        batch_id, started_at = prompt_context._active_source_batch(
            [process_step],
            [correction, first_inbound],
        )

        self.assertEqual(batch_id, str(correction.id))
        self.assertEqual(started_at, correction.timestamp)

    def test_current_request_sources_share_batch_across_completions(self):
        started_at = datetime(2026, 7, 27, 9, 0, tzinfo=timezone.utc)
        source_time = started_at + timedelta(seconds=2)

        first = prompt_context._source_batch_id_for_tool_result(
            tool_name="mcp_brightdata_scrape_as_markdown",
            created_at=source_time,
            completion_id="completion-one",
            active_batch_id="request-one",
            active_started_at=started_at,
        )
        second = prompt_context._source_batch_id_for_tool_result(
            tool_name="mcp_brightdata_scrape_as_markdown",
            created_at=source_time + timedelta(seconds=2),
            completion_id="completion-two",
            active_batch_id="request-one",
            active_started_at=started_at,
        )
        non_source = prompt_context._source_batch_id_for_tool_result(
            tool_name="send_chat_message",
            created_at=source_time,
            completion_id="completion-one",
            active_batch_id="request-one",
            active_started_at=started_at,
        )

        self.assertEqual((first, second), ("request-one", "request-one"))
        self.assertEqual(non_source, "completion-one")

    def test_http_mutations_are_not_source_bearing_results(self):
        self.assertTrue(
            prompt_context._tool_result_is_source_bearing(
                "http_request",
                {"method": "GET"},
            )
        )
        self.assertTrue(
            prompt_context._tool_result_is_source_bearing(
                "http_request",
                {"method": "POST"},
            )
        )
        for method in ("PATCH", "PUT", "DELETE"):
            self.assertFalse(
                prompt_context._tool_result_is_source_bearing(
                    "http_request",
                    {"method": method},
                )
            )

    def test_source_url_metadata_uses_one_exact_source_request_url(self):
        self.assertEqual(
            prompt_context._source_url_from_tool_params(
                None,
                "mcp_brightdata_scrape_as_markdown",
                {"url": "https://research.example.test/interviews/one"},
            ),
            "https://research.example.test/interviews/one",
        )
        self.assertIsNone(
            prompt_context._source_url_from_tool_params(
                None,
                "send_chat_message",
                {"url": "https://research.example.test/interviews/one"},
            )
        )
        self.assertIsNone(
            prompt_context._source_url_from_tool_params(
                None,
                "http_request",
                {"url": "compare https://one.example.test and https://two.example.test"},
            )
        )

    def test_sqlite_guidance_tracks_bounded_set_coverage(self):
        guidance = prompt_context._get_sqlite_guidance()
        self.assertIn("Never transcribe visible preview facts into SQL", guidance)
        self.assertIn("Submit no draft/superseded statements", guidance)

        self.assertIn("Named tables hold keyed entities", guidance)
        self.assertIn("tool results never update them", guidance)
        self.assertIn("Ready routes", guidance)
        self.assertIn("opaque auth refs only for the requested operation", guidance)
        self.assertIn("no preflight", guidance)
        self.assertIn("current sources", guidance)
        self.assertIn("one sqlite_batch upserts all relevant rows", guidance)
        self.assertIn("exact answer SELECTs", guidance)
        self.assertIn("never combine separate results in prose", guidance)
        self.assertIn("Follow-ups query named tables", guidance)
        self.assertIn(
            "is_current_batch=1 AND tool_name='exact visible name'",
            guidance,
        )
        self.assertIn("with no result_id/URL filter and no pre-read", guidance)
        self.assertIn("keep non-key fields nullable", guidance)
        self.assertIn("Parent fields come from result_json", guidance)
        self.assertIn("children from json_each(actual array)", guidance)
        self.assertIn("every supported field in one top-level row per result_id", guidance)
        self.assertIn("join rows to __tool_results", guidance)
        self.assertIn("Never type sourced facts/URLs/classifications into SQL", guidance)
        self.assertIn("Bound interpretations only transcribe evidence", guidance)
        self.assertIn("INSERT SELECT directly from the latest __messages payload", guidance)
        self.assertIn("derive every field plus message_id", guidance)
        self.assertIn("never pre-read or quote state/status", guidance)
        self.assertIn("never import siblings singly", guidance)
        self.assertIn("Upsert stable keys and mutable provenance", guidance)
        self.assertIn("Affected 0 plus empty readback is failure", guidance)
        self.assertIn("Bind authored/messy values as :name", guidance)
        self.assertIn("INSERT SELECT needs WHERE 1=1 before ON CONFLICT", guidance)
        self.assertIn("UNION top-one needs a scalar subquery/CTE", guidance)
        self.assertIn("Reads that may trigger another tool use will_continue_work=true", guidance)
        self.assertIn("LIVE SCHEMA is authoritative", guidance)
        self.assertIn("do not rediscover them", guidance)
        self.assertIn("shown durable domain table", guidance)
        self.assertIn("compute task filters/grouping/ranking", guidance)
        self.assertIn("do not SELECT whole tables and assemble the answer yourself", guidance)
        self.assertIn("first sqlite_batch", guidance)
        self.assertIn("call 1 only targeted sqlite_master", guidance)
        self.assertIn("meaningful domain noun from the request", guidance)
        self.assertIn("call 2 PRAGMA table_info alone", guidance)
        self.assertIn("columns are unavailable until that returns", guidance)
        self.assertIn("call 3 uses only returned columns/keys", guidance)
        self.assertIn("`_` is a LIKE wildcard", guidance)
        self.assertIn("json_each aliases expose key/value, not seq", guidance)
        self.assertIn("group_concat(DISTINCT x)", guidance)
        self.assertNotIn("Copy names/paths/values/URLs", guidance)

    def test_low_iteration_warning_keeps_unfinished_work_active(self):
        collector = _NestedPromptSectionCollector()
        with (
            patch("api.agent.core.prompt_context.get_budget_context", return_value=None),
            patch("api.agent.core.prompt_context.get_browser_daily_task_limit", return_value=None),
            patch(
                "api.agent.core.prompt_context.get_tool_cost_overview",
                return_value=(Decimal("1"), {}),
            ),
        ):
            added = prompt_context.add_budget_awareness_sections(
                collector,
                current_iteration=9,
                max_iterations=10,
            )

        warning = collector.sections["iteration_warning"]
        self.assertTrue(added)
        self.assertIn("never false-complete", warning)
        self.assertIn("unfinished scope", warning)
        self.assertIn("next cycle", warning)
        self.assertNotIn("set a schedule", warning)

    def test_sqlite_retry_warning_flags_repeated_empty_probes(self):
        warning = prompt_context._build_sqlite_retry_warning(
            [
                (
                    {"sql": "SELECT * FROM __tool_results WHERE result_id='73b1fa'"},
                    '{"results":[{"message":"Query 0 returned 0 rows."}]}',
                ),
                (
                    {"sql": "SELECT grep_context_all(result_text, 'Tomorrow') FROM __tool_results WHERE result_id='73b1fa'"},
                    '{"results":[{"message":"Query 0 returned 0 rows."}]}',
                ),
                (
                    {"sql": "SELECT csv_headers(result_text) FROM __tool_results WHERE result_id='73b1fa'"},
                    '{"results":[{"result":[{"headers":"[\\"New York\\",\\"Forecast\\"]"}]}]}',
                ),
                (
                    {"sql": "SELECT regexp_extract(result_text, 'Hi: (\\\\d+)') FROM __tool_results WHERE result_id='73b1fa'"},
                    '{"results":[{"message":"Query 0 returned 0 rows."}]}',
                ),
            ]
        )

        self.assertIn("Loop warning", warning)
        self.assertIn("73b1fa", warning)

    def test_sqlite_retry_warning_flags_blob_fetch_loops(self):
        warning = prompt_context._build_sqlite_retry_warning(
            [
                ({"sql": "SELECT result_text FROM __tool_results WHERE result_id='a1'"}, "{}"),
                ({"sql": "SELECT result_text FROM __tool_results WHERE result_id='b2'"}, "{}"),
            ]
        )

        self.assertIn("SQLite efficiency warning", warning)
        self.assertIn("one shaped query", warning)

    def test_sqlite_retry_warning_flags_imports_split_across_calls(self):
        warning = prompt_context._build_sqlite_retry_warning(
            [
                (
                    {
                        "sql": "INSERT INTO items SELECT result_json FROM __tool_results "
                        "WHERE result_id='a1'"
                    },
                    "{}",
                ),
                (
                    {
                        "sql": "INSERT INTO items SELECT result_json FROM __tool_results "
                        "WHERE result_id='b2'"
                    },
                    "{}",
                ),
            ]
        )

        self.assertIn("SQLite efficiency warning", warning)
        self.assertIn("one result_id at a time", warning)

    def test_sqlite_retry_warning_allows_multi_entity_import_in_one_batch(self):
        warning = prompt_context._build_sqlite_retry_warning(
            [
                (
                    {
                        "sql": "INSERT INTO accounts SELECT value FROM __tool_results, "
                        "json_each(result_json, '$.content.accounts') WHERE result_id='a1'; "
                        "INSERT INTO workstreams SELECT value FROM __tool_results, "
                        "json_each(result_json, '$.content.workstreams') WHERE result_id='a1'; "
                        "SELECT * FROM accounts; SELECT * FROM workstreams"
                    },
                    '{"status":"ok"}',
                ),
            ]
        )

        self.assertEqual(warning, "")

    def test_sqlite_retry_warning_recovers_from_rejected_singleton_queries(self):
        rejection = (
            "Query not executed: do not read __tool_results or a staging table derived from it one result_id at a "
            "time. A one-item IN (...) is still one-at-a-time."
        )
        warning = prompt_context._build_sqlite_retry_warning(
            [
                (
                    {"sql": "SELECT result_json FROM __tool_results WHERE result_id IN ('a1')"},
                    rejection,
                ),
                (
                    {"sql": "SELECT result_json FROM __tool_results WHERE result_id IN ('b2')"},
                    rejection,
                ),
            ]
        )

        self.assertIn("SQLite recovery", warning)
        self.assertIn("Do not retry that shape", warning)
        self.assertIn("upsert by stable key", warning)
        self.assertIn("otherwise answer the shaped result", warning)
        self.assertIn("Refetch only if evidence is stale or missing", warning)

    def test_source_model_warning_targets_only_unreconciled_named_model_reads(self):
        source = ("http_request", {"url": "https://crm.example.test/account"}, "complete")
        stale_read = ("sqlite_batch", {"sql": "SELECT * FROM accounts WHERE account_id='acct-1'"}, "complete")

        warning = prompt_context._build_unreconciled_source_model_warning([source, stale_read])

        self.assertIn("Fresh source evidence is not reconciled", warning)
        self.assertIn("must use INSERT ... SELECT or UPDATE ... FROM __tool_results/json_each", warning)
        self.assertIn("Every sourced field, including IDs", warning)
        self.assertIn("only JSON paths and current result_id/tool_name may be literals", warning)
        self.assertIn("Otherwise answer it directly", warning)
        self.assertEqual(
            prompt_context._build_unreconciled_source_model_warning([
                ("http_request", {}, "error"), stale_read,
            ]),
            "",
        )
        self.assertEqual(
            prompt_context._build_unreconciled_source_model_warning([
                source,
                ("sqlite_batch", {"sql": "SELECT * FROM __tool_results"}, "complete"),
                ("sqlite_batch", {"sql": "SELECT * FROM _csv_abc123"}, "complete"),
            ]),
            "",
        )

    def test_source_model_warning_clears_only_after_source_derived_durable_dml(self):
        source = ("mcp_crm_get_account", {}, "complete")
        stale_read = ("sqlite_batch", {"sql": "SELECT * FROM accounts"}, "complete")
        copied_update = (
            "sqlite_batch",
            {"sql": "SELECT result_json FROM __tool_results; UPDATE accounts SET stage='contracting'"},
            "complete",
        )
        staged_update = (
            "sqlite_batch",
            {"sql": "INSERT INTO staging_accounts SELECT result_json FROM __tool_results"},
            "complete",
        )
        derived_update = (
            "sqlite_batch",
            {"sql": "UPDATE accounts SET stage=(SELECT json_extract(result_json,'$.stage') FROM __tool_results)"},
            "complete",
        )

        self.assertTrue(prompt_context._build_unreconciled_source_model_warning([source, stale_read, copied_update]))
        self.assertTrue(prompt_context._build_unreconciled_source_model_warning([source, stale_read, staged_update]))
        self.assertTrue(
            prompt_context._build_unreconciled_source_model_warning([source, stale_read, derived_update])
        )
        self.assertTrue(prompt_context._build_unreconciled_source_model_warning([source, derived_update]))
        post_update_read = (
            "sqlite_batch",
            {"sql": "SELECT stage FROM accounts WHERE account_id='acct-1'"},
            "complete",
        )
        self.assertEqual(
            prompt_context._build_unreconciled_source_model_warning([
                source, stale_read, derived_update, post_update_read,
            ]),
            "",
        )
        self.assertTrue(
            prompt_context._build_unreconciled_source_model_warning([
                source,
                stale_read,
                (
                    "sqlite_batch",
                    {
                        "sql": "UPDATE accounts SET stage=(SELECT json_extract(result_json,'$.stage') "
                        "FROM __tool_results) WHERE account_id IN (SELECT account_id FROM accounts)"
                    },
                    "complete",
                ),
            ])
        )
        self.assertTrue(
            prompt_context._build_unreconciled_source_model_warning([
                source, stale_read, derived_update, post_update_read, derived_update,
            ])
        )

        child_update = (
            "sqlite_batch",
            {"sql": "INSERT INTO workstreams(workstream_id) SELECT json_extract(value,'$.id') "
                    "FROM __tool_results,json_each(result_json,'$.workstreams')"},
            "complete",
        )
        child_read = ("sqlite_batch", {"sql": "SELECT * FROM workstreams"}, "complete")
        self.assertTrue(prompt_context._build_unreconciled_source_model_warning([
            source, stale_read, derived_update, child_update, post_update_read,
        ]))
        self.assertEqual(prompt_context._build_unreconciled_source_model_warning([
            source, stale_read, derived_update, child_update, post_update_read, child_read,
        ]), "")

    def test_source_model_warning_handles_model_first_and_unrelated_mutations(self):
        model_read = ("sqlite_batch", {"sql": "SELECT * FROM accounts"}, "complete")
        source = ("http_request", {}, "complete")
        unrelated_write = (
            "sqlite_batch",
            {"sql": "INSERT INTO audit_log(event) SELECT result_text FROM __tool_results"},
            "complete",
        )
        later_source = ("mcp_crm_get_account", {}, "complete")

        self.assertTrue(prompt_context._build_unreconciled_source_model_warning([model_read, source]))
        self.assertTrue(
            prompt_context._build_unreconciled_source_model_warning([source, model_read, later_source])
        )
        self.assertTrue(
            prompt_context._build_unreconciled_source_model_warning([source, model_read, unrelated_write])
        )

    def test_multi_source_work_gets_an_incremental_model_checkpoint(self):
        first_source = ("mcp_brightdata_search_engine", {"query": "company roster"}, "complete")
        second_source = ("mcp_brightdata_search_engine", {"query": "founder roster"}, "complete")

        self.assertEqual(
            prompt_context._build_unreconciled_source_model_warning([first_source]),
            "",
        )
        warning = prompt_context._build_unreconciled_source_model_warning([
            first_source,
            second_source,
        ])

        self.assertIn("may form a reusable working set", warning)
        self.assertIn("bounded small report", warning)
        self.assertIn("Otherwise the next action is sqlite_batch", warning)
        self.assertIn("durable named entity/relationship tables", warning)
        self.assertIn("PRIMARY KEY/UNIQUE and provenance (not TEMP/CTAS)", warning)
        self.assertIn("reconcile this source batch", warning)
        self.assertIn("query coverage gaps", warning)
        self.assertIn("Import same-shaped siblings with `is_current_batch=1`", warning)
        self.assertIn("never filter result_id, source_url, or link handles", warning)
        self.assertIn("separate statements only for different entity shapes", warning)
        self.assertIn("Do not answer or act from a reusable transient work set", warning)
        self.assertNotIn("FIRST-RUN GUIDED INTAKE", warning)

        inspection = (
            "sqlite_batch",
            {"sql": "SELECT result_id, substr(result_text,1,500) FROM __tool_results ORDER BY created_at"},
            "complete",
        )
        post_inspection = prompt_context._build_unreconciled_source_model_warning([
            first_source,
            second_source,
            inspection,
        ])
        self.assertIn("already inspected this complete source set", post_inspection)
        self.assertIn("Do not query raw __tool_results again", post_inspection)
        self.assertIn("non-empty top-level `rows`", post_inspection)
        self.assertIn("json_each(:rows)", post_inspection)
        self.assertIn("r has no named fields", post_inspection)
        self.assertIn("Empty rows", post_inspection)
        self.assertIn("another inspection are invalid strategies", post_inspection)

        modeled_without_read = (
            "sqlite_batch",
            {
                "sql": (
                    "CREATE TABLE companies(company_id TEXT PRIMARY KEY, name TEXT);"
                    "INSERT INTO companies(company_id,name) "
                    "SELECT json_extract(value,'$.company_id'),json_extract(value,'$.name') "
                    "FROM __tool_results,json_each(result_json,'$.companies')"
                )
            },
            "complete",
        )
        read_checkpoint = prompt_context._build_unreconciled_source_model_warning([
            first_source,
            second_source,
            modeled_without_read,
        ])
        self.assertIn("Fresh source evidence is reconciled", read_checkpoint)
        self.assertIn("still-unread updated table(s): companies", read_checkpoint)
        self.assertIn("instead of rereading transient results or repeating the write", read_checkpoint)

        modeled = (
            "sqlite_batch",
            {
                "sql": (
                    "CREATE TABLE companies(company_id TEXT PRIMARY KEY, name TEXT);"
                    "INSERT INTO companies(company_id,name) "
                    "SELECT json_extract(value,'$.company_id'),json_extract(value,'$.name') "
                    "FROM __tool_results,json_each(result_json,'$.companies');"
                    "SELECT * FROM companies"
                )
            },
            "complete",
        )
        self.assertEqual(
            prompt_context._build_unreconciled_source_model_warning([
                first_source,
                second_source,
                modeled,
            ]),
            "",
        )

    def test_http_mutation_receipts_do_not_create_multi_source_warning(self):
        source_read = (
            "http_request",
            {"method": "GET", "url": "https://crm.example.test/accounts"},
            "complete",
        )
        mutation_receipt = (
            "http_request",
            {"method": "PATCH", "url": "https://crm.example.test/accounts/acct-1"},
            "complete",
        )

        self.assertEqual(
            prompt_context._build_unreconciled_source_model_warning([
                source_read,
                mutation_receipt,
            ]),
            "",
        )
        self.assertEqual(
            prompt_context._build_unreconciled_source_model_warning([mutation_receipt]),
            "",
        )


class _PromptSectionCollector:
    def __init__(self):
        self.sections = {}

    def section_text(self, name, text, **_kwargs):
        self.sections[name] = text


class _NestedPromptSectionCollector(_PromptSectionCollector):
    def group(self, *_args, **_kwargs):
        return self


class _NoopSpan:
    def set_attribute(self, *_args, **_kwargs):
        return None


@tag("batch_promptree")
class PromptContextContactsGuidanceTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="owner",
            email="owner@example.com",
        )
        EmailAddress.objects.create(
            user=self.user,
            email=self.user.email,
            verified=True,
            primary=True,
        )
        self.browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="Prompt Contacts Browser",
        )
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Prompt Contacts Agent",
            charter="Test contacts guidance.",
            browser_use_agent=self.browser_agent,
        )

    def test_source_url_metadata_registers_a_stable_link_reference(self):
        source_url = "https://research.example.test/interviews/one"

        extracted = prompt_context._source_url_from_tool_params(
            self.agent,
            "mcp_brightdata_scrape_as_markdown",
            {"url": source_url},
        )
        prompt_context._register_source_url_references(
            self.agent,
            [
                prompt_context.ToolCallResultRecord(
                    step_id="source-one",
                    tool_name="mcp_brightdata_scrape_as_markdown",
                    created_at=prompt_context.dj_timezone.now(),
                    result_text="Source one",
                    source_url=extracted,
                )
            ],
        )

        self.assertEqual(extracted, source_url)
        rendered = prompt_context.rewrite_prompt_urls(source_url, self.agent, create=False)
        self.assertRegex(rendered, r"^\$\[link:L[0-9A-Z]{16}\]$")

    def test_runtime_config_note_does_not_direct_one_off_feedback_into_config(self):
        with patch("api.agent.core.prompt_context.ensure_steps_compacted"), patch(
            "api.agent.core.prompt_context.ensure_comms_compacted"
        ):
            context, _, _ = prompt_context.build_prompt_context(self.agent, is_first_run=False)

        content = "\n".join(message["content"] for message in context)
        self.assertIn("patch_text=lasting owner rules", content)
        self.assertIn(
            "appearance=full person after authorized changes: age/skin/hair/eyes/style, not scene/vibe; preserve unspecified; confirm briefly",
            content,
        )
        self.assertIn("temporary feedback/ordinary tasks never config", content)
        self.assertIn(
            "For clear ongoing/monitoring intent, first write one safe default __agent_schedules cadence",
            content,
        )
        self.assertIn("Clear ongoing requests such as monitor", content)
        self.assertIn("Emotion is one SQLite update", content)
        self.assertNotIn("Without a schedule, you die", content)

    def test_runtime_schedule_note_keeps_temporary_scope_from_changing_cadence(self):
        self.agent.schedule = "0 9 * * *"
        self.agent.save(update_fields=["schedule", "updated_at"])
        with patch("api.agent.core.prompt_context.ensure_steps_compacted"), patch(
            "api.agent.core.prompt_context.ensure_comms_compacted"
        ):
            context, _, _ = prompt_context.build_prompt_context(self.agent, is_first_run=False)

        content = "\n".join(message["content"] for message in context)
        self.assertIn("never temporary task scope", content.lower())
        self.assertIn(
            "__agent_schedules only columns: schedule_key,name,kind,schedule,timezone,run_at,instruction,enabled",
            content,
        )
        self.assertIn("weekly=cron; @every only s/m/h", content)
        self.assertIn("exceeds 12 active jobs", content)
        self.assertIn("one bounded alternative; no SQLite", content)
        self.assertIn("never repurpose primary", content)
        self.assertNotIn("Task scope changed? Adjust timing", content)

    def test_large_allowed_contacts_are_compacted_in_prompt(self):
        CommsAllowlistEntry.objects.bulk_create(
            [
                CommsAllowlistEntry(
                    agent=self.agent,
                    channel=CommsChannel.EMAIL,
                    address=f"person-{idx:02d}@example.com",
                    is_active=True,
                    allow_inbound=True,
                    allow_outbound=True,
                )
                for idx in range(prompt_context.CONTACT_PROMPT_INLINE_LIMIT + 5)
            ]
        )
        collector = _PromptSectionCollector()
        config_authority = prompt_context._ConfigAuthorityResolver(self.agent)
        contact_records = prompt_context.build_contacts_snapshot_records(
            self.agent,
            display_name_for_user=prompt_context._build_user_display_name,
            user_can_configure=config_authority.user_can_configure,
        )

        prompt_context._build_contacts_block(
            self.agent,
            collector,
            _NoopSpan(),
            config_authority,
            contact_records,
        )

        allowed_contacts = collector.sections["allowed_contacts"]
        self.assertIn("__contacts", allowed_contacts)
        self.assertIn("active contacts are available", allowed_contacts)
        self.assertIn("Sample active contacts", allowed_contacts)
        self.assertIn("person-29@example.com", allowed_contacts)
        self.assertNotIn("person-00@example.com", allowed_contacts)
        self.assertIn("status='allowed' AND allow_outbound=1", allowed_contacts)

    def test_auto_approval_prompt_sends_email_directly_but_keeps_sms_approval(self):
        self.agent.contact_approval_mode = PersistentAgent.ContactApprovalMode.AUTO_APPROVE_EMAIL
        self.agent.save(update_fields=["contact_approval_mode"])
        collector = _PromptSectionCollector()
        config_authority = prompt_context._ConfigAuthorityResolver(self.agent)
        contact_records = prompt_context.build_contacts_snapshot_records(
            self.agent,
            display_name_for_user=prompt_context._build_user_display_name,
            user_can_configure=config_authority.user_can_configure,
        )

        prompt_context._build_contacts_block(
            self.agent,
            collector,
            _NoopSpan(),
            config_authority,
            contact_records,
        )

        allowed_contacts = collector.sections["allowed_contacts"]
        self.assertIn("email a new address directly with send_email", allowed_contacts)
        self.assertIn("SMS contacts still require request_contact_permission", allowed_contacts)
        self.assertNotIn("To reach someone new, use request_contact_permission", allowed_contacts)

    def test_allowed_contact_channels_do_not_imply_sending_channels(self):
        PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.WEB,
            address=f"web://agent/{self.agent.id}",
        )
        CommsAllowlistEntry.objects.create(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="ops@example.test",
            is_active=True,
            allow_inbound=True,
            allow_outbound=True,
        )
        CommsAllowlistEntry.objects.create(
            agent=self.agent,
            channel=CommsChannel.SMS,
            address="+15555550123",
            is_active=True,
            allow_inbound=True,
            allow_outbound=False,
        )
        collector = _PromptSectionCollector()
        config_authority = prompt_context._ConfigAuthorityResolver(self.agent)
        contact_records = prompt_context.build_contacts_snapshot_records(
            self.agent,
            display_name_for_user=prompt_context._build_user_display_name,
            user_can_configure=config_authority.user_can_configure,
        )

        prompt_context._build_contacts_block(
            self.agent,
            collector,
            _NoopSpan(),
            config_authority,
            contact_records,
        )

        allowed_channels = collector.sections["allowed_channels"]
        self.assertIn("You can communicate via: web.", allowed_channels)
        self.assertNotIn("email", allowed_channels)
        self.assertNotIn("sms", allowed_channels)

    def test_verified_owner_phone_does_not_advertise_sms_without_agent_endpoint(self):
        PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="agent@example.test",
        )
        UserPhoneNumber.objects.create(
            user=self.user,
            phone_number="+15555550123",
            is_verified=True,
        )
        collector = _PromptSectionCollector()
        config_authority = prompt_context._ConfigAuthorityResolver(self.agent)
        contact_records = prompt_context.build_contacts_snapshot_records(
            self.agent,
            display_name_for_user=prompt_context._build_user_display_name,
            user_can_configure=config_authority.user_can_configure,
        )

        prompt_context._build_contacts_block(
            self.agent,
            collector,
            _NoopSpan(),
            config_authority,
            contact_records,
        )

        allowed_channels = collector.sections["allowed_channels"]
        self.assertIn("You can communicate via: email.", allowed_channels)
        self.assertNotIn("sms", allowed_channels)

    def test_sms_endpoint_is_advertised_only_when_sms_is_enabled(self):
        PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="agent@example.test",
        )
        PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.SMS,
            address="+15555550124",
        )

        config_authority = prompt_context._ConfigAuthorityResolver(self.agent)
        contact_records = prompt_context.build_contacts_snapshot_records(
            self.agent,
            display_name_for_user=prompt_context._build_user_display_name,
            user_can_configure=config_authority.user_can_configure,
        )
        collector = _PromptSectionCollector()
        prompt_context._build_contacts_block(
            self.agent,
            collector,
            _NoopSpan(),
            config_authority,
            contact_records,
        )

        self.assertIn("- sms: +15555550124", collector.sections["agent_endpoints"])
        self.assertIn("You can communicate via: email, sms.", collector.sections["allowed_channels"])

        self.agent.sms_disabled = True
        collector = _PromptSectionCollector()
        prompt_context._build_contacts_block(
            self.agent,
            collector,
            _NoopSpan(),
            config_authority,
            contact_records,
        )

        self.assertNotIn("sms", collector.sections["agent_endpoints"])
        self.assertIn("You can communicate via: email.", collector.sections["allowed_channels"])
        self.assertNotIn("sms", collector.sections["allowed_channels"])
