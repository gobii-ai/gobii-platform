from decimal import Decimal
from unittest.mock import patch

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
    def test_sqlite_guidance_tracks_bounded_set_coverage(self):
        guidance = prompt_context._get_sqlite_guidance()

        self.assertIn("Named tables are the world model", guidance)
        self.assertIn("digest is partial", guidance)
        self.assertIn("Use queried rows, not memory, for decisions", guidance)
        self.assertIn("Explicit fresh source: fetch once", guidance)
        self.assertIn("Otherwise read the model first", guidance)
        self.assertIn("Current complete rows are truth; don't refetch them", guidance)
        self.assertIn("Tool output doesn't update it", guidance)
        self.assertIn("reconcile each useful source batch", guidance)
        self.assertIn("checklist, intermediate state, and resume point", guidance)
        self.assertIn("sharing a stable ID or its children", guidance)
        self.assertIn("reconcile entities/relations", guidance)
        self.assertIn("before acting/reporting", guidance)
        self.assertIn("evolve schema, then query", guidance)
        self.assertIn("Upserts refresh every mutable/provenance field", guidance)
        self.assertIn("Only unrelated one-offs bypass it", guidance)
        self.assertIn("Use stable keys", guidance)
        self.assertIn("inspect identity after wrong row counts", guidance)
        self.assertIn("query gaps before reporting", guidance)
        self.assertIn("Only sourced blockers are unresolved", guidance)
        self.assertIn("use SQLite for exact set logic/counts/ranking", guidance)
        self.assertIn("Bind messy agent-authored values with :name + bindings", guidance)
        self.assertIn("No sibling-by-sibling result/table/blob loops", guidance)
        self.assertIn("Inspect unknown structure once", guidance)
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

        self.assertIn("form a working set and remain transient", warning)
        self.assertIn("next action must be sqlite_batch", warning)
        self.assertIn("durable named entity/relationship tables", warning)
        self.assertIn("PRIMARY KEY/UNIQUE and provenance (not TEMP/CTAS)", warning)
        self.assertIn("reconcile this source batch", warning)
        self.assertIn("query coverage gaps", warning)
        self.assertIn("Import same-shaped siblings in one set query", warning)
        self.assertIn("separate statements only for different entity shapes", warning)
        self.assertIn("Do not answer or act from transient results", warning)

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
        self.assertIn("already inspected this source batch", post_inspection)
        self.assertIn("Do not query raw __tool_results again", post_inspection)
        self.assertIn("bind one JSON array", post_inspection)
        self.assertIn("json_each(:rows)", post_inspection)
        self.assertIn("Import same-shaped siblings with one set query", post_inspection)

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
        self.assertIn("Recurring work is highest priority", content)
        self.assertIn("Emotions are autonomous, not only owner-requested", content)
        self.assertNotIn("Without a schedule, you die", content)

    def test_runtime_schedule_note_keeps_temporary_scope_from_changing_cadence(self):
        self.agent.schedule = "0 9 * * *"
        self.agent.save(update_fields=["schedule", "updated_at"])
        with patch("api.agent.core.prompt_context.ensure_steps_compacted"), patch(
            "api.agent.core.prompt_context.ensure_comms_compacted"
        ):
            context, _, _ = prompt_context.build_prompt_context(self.agent, is_first_run=False)

        content = "\n".join(message["content"] for message in context)
        self.assertIn("temporary task scope never changes it", content)
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
