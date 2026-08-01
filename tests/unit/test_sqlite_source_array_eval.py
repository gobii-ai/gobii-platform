import json
from types import SimpleNamespace

from django.test import SimpleTestCase, tag

import api.evals.loader  # noqa: F401 - registers scenarios and suites
from api.agent.tools.sqlite_query_quality import summarize_sqlite_tool_result_sql
from api.evals.registry import ScenarioRegistry
from api.evals.scenarios.sqlite_tool_results import (
    SQLITE_ENRICHMENT_REFRESH_UNDER_PRESSURE,
    SQLITE_FRESH_PEER_FACT_OVER_EMPTY_MODEL,
    SQLITE_INCREMENTAL_DOMAIN_MODEL,
    SQLITE_PEER_OUTCOME_RECONCILES_CANONICAL_MODEL,
    SQLITE_SIBLING_RESULT_SET_FIRST_WRITE,
    SQLITE_SOURCE_CARDINALITY_AND_IDENTITY,
    SQLITE_SOURCE_ARRAY_FIRST_WRITE,
    SQLITE_STRUCTURED_PEER_EVENT_PERSISTENCE,
    SQLITE_TOOL_RESULT_SCENARIO_SLUGS,
    SQLITE_TOOL_RESULT_SUITE_SLUG,
    SQLITE_UNSTRUCTURED_BINDINGS_FIRST_WRITE,
    SqliteEnrichmentRefreshUnderPressureScenario,
    SqliteFreshPeerFactOverEmptyModelScenario,
    SqliteIncrementalDomainModelScenario,
    SqliteIntermediateWorkingTableScenario,
    SqlitePeerOutcomeReconcilesCanonicalModelScenario,
    SqliteSiblingResultSetFirstWriteScenario,
    SqliteSourceCardinalityAndIdentityScenario,
    SqliteSourceArrayFirstWriteScenario,
    SqliteStructuredPeerEventPersistenceScenario,
    SqliteUnstructuredBindingsFirstWriteScenario,
    _bound_json_payload_placeholder,
    _derives_bound_structured_message_fields,
    _derives_structured_message_fields,
    _insert_values_derive_bound_payload_fields,
    _mutation_target_table,
    _repeated_source_import_tables,
    _release_source_url_column,
    _schema_grounded_read_failures,
    _sqlite_attempt_failures,
    _source_array_first_write_failures,
    _uses_bound_source_values,
    _uses_queryable_source_model,
)
from api.evals.suites import SuiteRegistry


def _sqlite_call(sql, *, result=None, status="complete"):
    return SimpleNamespace(
        tool_name="sqlite_batch",
        tool_params={"sql": sql},
        status=status,
        result=result or json.dumps({
            "status": "ok",
            "results": [
                {"message": "Query 0 executed."},
                {"message": "Query 1 affected 4 rows."},
                {"result": [{"release_id": "rel-search-18"}]},
            ],
        }),
    )


@tag("batch_eval_fingerprint")
class SqliteSourceArrayEvalTests(SimpleTestCase):
    clean_sql = """
        CREATE TABLE release_events (
            release_id TEXT PRIMARY KEY,
            service TEXT NOT NULL,
            starts_at TEXT NOT NULL,
            owner TEXT NOT NULL,
            status TEXT NOT NULL,
            source_url TEXT NOT NULL,
            observed_at TEXT NOT NULL
        );
        INSERT INTO release_events (
            release_id, service, starts_at, owner, status, source_url, observed_at
        )
        SELECT
            json_extract(event.value, '$.release_id'),
            json_extract(event.value, '$.service'),
            json_extract(event.value, '$.starts_at'),
            json_extract(event.value, '$.owner'),
            json_extract(event.value, '$.status'),
            json_extract(event.value, '$.source_url'),
            json_extract(event.value, '$.observed_at')
        FROM __tool_results
        JOIN json_each(result_json, '$.content.events') AS event
        WHERE tool_name = 'http_request';
        SELECT release_id, service, starts_at, owner, status
        FROM release_events
        ORDER BY starts_at;
    """

    def test_release_model_accepts_semantic_provenance_column_names(self):
        self.assertEqual(
            _release_source_url_column({"release_id", "event_source_url"}),
            "event_source_url",
        )
        self.assertEqual(
            _release_source_url_column({"release_id", "feed_source_url"}),
            "feed_source_url",
        )
        self.assertIsNone(_release_source_url_column({"release_id", "service"}))

    def test_schema_grounding_accepts_direct_live_schema_read(self):
        call = _sqlite_call(
            "SELECT worker_ref, COUNT(*) FROM z_handoff_ledger "
            "WHERE resolution_code <> 'resolved' GROUP BY worker_ref;"
        )

        self.assertEqual(_schema_grounded_read_failures([call]), [])

    def test_schema_grounding_rejects_redundant_inspection_after_read(self):
        call = _sqlite_call(
            "SELECT * FROM z_handoff_ledger; "
            "PRAGMA table_info(z_handoff_ledger);",
        )

        self.assertIn(
            "live prompt schema was ignored in favor of a redundant schema-inspection round trip",
            _schema_grounded_read_failures([call]),
        )

    def test_schema_grounding_rejects_redundant_inspection_before_read(self):
        call = _sqlite_call(
            "PRAGMA table_info(z_handoff_ledger); "
            "SELECT * FROM z_handoff_ledger;",
        )

        self.assertIn(
            "live prompt schema was ignored in favor of a redundant schema-inspection round trip",
            _schema_grounded_read_failures([call]),
        )

    def test_peer_outcome_fixture_uses_role_aligned_seller(self):
        scenario = SqlitePeerOutcomeReconcilesCanonicalModelScenario()

        self.assertIn("seller", scenario.peer_name_prefix.casefold())
        self.assertIn("outreach", scenario.peer_charter.casefold())
        self.assertEqual(scenario.outcome_state, "bounced")
        self.assertNotIn(scenario.outcome_state, {"sent", "prepared"})

    def test_source_array_case_is_registered_in_sqlite_suite(self):
        suite = SuiteRegistry.get(SQLITE_TOOL_RESULT_SUITE_SLUG)
        scenario = ScenarioRegistry.get(SQLITE_SOURCE_ARRAY_FIRST_WRITE)

        self.assertIsNotNone(scenario)
        self.assertIn(SQLITE_SOURCE_ARRAY_FIRST_WRITE, SQLITE_TOOL_RESULT_SCENARIO_SLUGS)
        self.assertIn(SQLITE_SOURCE_ARRAY_FIRST_WRITE, suite.scenario_slugs)
        self.assertEqual(
            [task.name for task in scenario.tasks],
            [
                "inject_prompt",
                "verify_first_source_write",
                "verify_persisted_release_model",
                "verify_release_answer",
            ],
        )

    def test_truth_integrity_cases_are_registered_without_teaching_sql(self):
        suite = SuiteRegistry.get(SQLITE_TOOL_RESULT_SUITE_SLUG)
        cases = (
            (
                SQLITE_SOURCE_CARDINALITY_AND_IDENTITY,
                SqliteSourceCardinalityAndIdentityScenario,
            ),
            (
                SQLITE_FRESH_PEER_FACT_OVER_EMPTY_MODEL,
                SqliteFreshPeerFactOverEmptyModelScenario,
            ),
        )

        for slug, scenario_class in cases:
            with self.subTest(slug=slug):
                self.assertIsNotNone(ScenarioRegistry.get(slug))
                self.assertIn(slug, SQLITE_TOOL_RESULT_SCENARIO_SLUGS)
                self.assertIn(slug, suite.scenario_slugs)
                prompt = scenario_class.prompt.casefold()
                for leaked_term in ("sqlite", "__tool_results", "json_each", "insert", "select", "table"):
                    self.assertNotIn(leaked_term, prompt)

    def test_structured_peer_event_case_is_registered(self):
        suite = SuiteRegistry.get(SQLITE_TOOL_RESULT_SUITE_SLUG)
        scenario = ScenarioRegistry.get(SQLITE_STRUCTURED_PEER_EVENT_PERSISTENCE)

        self.assertIsInstance(scenario, SqliteStructuredPeerEventPersistenceScenario)
        self.assertIn(SQLITE_STRUCTURED_PEER_EVENT_PERSISTENCE, SQLITE_TOOL_RESULT_SCENARIO_SLUGS)
        self.assertIn(SQLITE_STRUCTURED_PEER_EVENT_PERSISTENCE, suite.scenario_slugs)
        self.assertEqual(
            [task.name for task in scenario.tasks],
            [
                "inject_event_request",
                "verify_structured_event_modeled",
                "verify_persisted_outcome_reported",
            ],
        )

    def test_peer_outcome_reconciliation_case_is_registered_without_teaching_sql(self):
        suite = SuiteRegistry.get(SQLITE_TOOL_RESULT_SUITE_SLUG)
        scenario = ScenarioRegistry.get(SQLITE_PEER_OUTCOME_RECONCILES_CANONICAL_MODEL)

        self.assertIsInstance(scenario, SqlitePeerOutcomeReconcilesCanonicalModelScenario)
        self.assertIn(SQLITE_PEER_OUTCOME_RECONCILES_CANONICAL_MODEL, SQLITE_TOOL_RESULT_SCENARIO_SLUGS)
        self.assertIn(SQLITE_PEER_OUTCOME_RECONCILES_CANONICAL_MODEL, suite.scenario_slugs)
        for leaked_term in ("sqlite", "__messages", "insert", "update", "select", "table"):
            self.assertNotIn(leaked_term, scenario.prompt.casefold())

    def test_peer_outcome_grounding_requires_every_copied_bound_value(self):
        sql = (
            "UPDATE outreach_threads SET state=:delivery_status, provider_message_id=:provider_id, sent_at=:sent_at "
            "WHERE recipient=:recipient"
        )
        call = _sqlite_call(sql)
        call.tool_params["bindings"] = {
            "recipient": "jordan@northstar.example.test",
            "delivery_status": "bounced",
            "provider_id": "provider-message-998",
            "sent_at": "2026-07-30T14:12:09Z",
        }

        self.assertTrue(
            _uses_bound_source_values(
                call,
                sql,
                {
                    "jordan@northstar.example.test",
                    "bounced",
                    "provider-message-998",
                    "2026-07-30T14:12:09Z",
                },
            )
        )

        call.tool_params["bindings"].pop("recipient")
        self.assertFalse(
            _uses_bound_source_values(
                call,
                sql,
                {
                    "jordan@northstar.example.test",
                    "bounced",
                    "provider-message-998",
                    "2026-07-30T14:12:09Z",
                },
            )
        )

    def test_source_read_does_not_bless_sibling_sql_literals_or_unused_bindings(self):
        sql = (
            "UPDATE outreach_threads SET state='sent', provider_message_id='provider-message-998', "
            "source_message_id=(SELECT message_id FROM __messages ORDER BY timestamp DESC LIMIT 1) "
            "WHERE recipient='jordan@northstar.example.test'"
        )
        call = _sqlite_call(sql)
        call.tool_params["bindings"] = {
            "status_padding": "sent",
            "recipient_padding": "jordan@northstar.example.test",
            "provider_padding": "provider-message-998",
        }

        self.assertFalse(
            _uses_bound_source_values(
                call,
                sql,
                {
                    "jordan@northstar.example.test",
                    "sent",
                    "provider-message-998",
                },
            )
        )

    def test_double_quoted_literal_does_not_count_as_bound_source_value(self):
        sql = (
            'UPDATE outreach_threads SET state="bounced", provider_message_id=:provider_id, '
            "sent_at=:sent_at WHERE recipient=:recipient"
        )
        call = _sqlite_call(sql)
        call.tool_params["bindings"] = {
            "recipient": "jordan@northstar.example.test",
            "delivery_status": "bounced",
            "provider_id": "provider-message-998",
            "sent_at": "2026-07-30T14:12:09Z",
        }

        self.assertFalse(
            _uses_bound_source_values(
                call,
                sql + " SELECT :delivery_status",
                {
                    "jordan@northstar.example.test",
                    "bounced",
                    "provider-message-998",
                    "2026-07-30T14:12:09Z",
                },
            )
        )

    def test_structured_peer_import_derives_every_field(self):
        fields = {"recipient", "delivery_status", "provider_message_id", "sent_at"}
        sql = (
            "UPDATE outreach_threads SET "
            "state=json_extract(structured_payload_json,'$.delivery_status'), "
            "provider_message_id=json_extract(structured_payload_json,'$.provider_message_id'), "
            "sent_at=json_extract(structured_payload_json,'$.sent_at') "
            "FROM __messages WHERE outreach_threads.recipient="
            "json_extract(structured_payload_json,'$.recipient')"
        )

        self.assertTrue(_derives_structured_message_fields(sql, fields))
        self.assertFalse(
            _derives_structured_message_fields(
                sql.replace(
                    "json_extract(structured_payload_json,'$.delivery_status')",
                    "'bounced'",
                ),
                fields,
            )
        )
        self.assertFalse(
            _derives_structured_message_fields(
                sql.replace(
                    "state=json_extract(structured_payload_json,'$.delivery_status')",
                    "state=CASE WHEN json_extract(structured_payload_json,'$.delivery_status')="
                    "'bounced' THEN 'bounced' ELSE 'sent' END",
                ),
                fields,
            )
        )

    def test_structured_peer_import_accepts_insert_select_upsert(self):
        fields = {"recipient", "delivery_status", "provider_message_id", "sent_at"}
        sql = (
            "INSERT INTO outreach_threads "
            "(thread_id, recipient, owner_name, state, provider_message_id, sent_at, source_message_id) "
            "SELECT 'manager:wave:prospect-77', "
            "json_extract(m.structured_payload_json, '$.recipient'), "
            "'Seller One', json_extract(m.structured_payload_json, '$.delivery_status'), "
            "json_extract(m.structured_payload_json, '$.provider_message_id'), "
            "json_extract(m.structured_payload_json, '$.sent_at'), m.message_id "
            "FROM (SELECT message_id, structured_payload_json FROM __messages "
            "WHERE is_outbound=0 AND structured_payload_json IS NOT NULL "
            "ORDER BY seq DESC LIMIT 1) m "
            "ON CONFLICT(recipient) DO UPDATE SET "
            "state=excluded.state, provider_message_id=excluded.provider_message_id, "
            "sent_at=excluded.sent_at, source_message_id=excluded.source_message_id"
        )

        self.assertTrue(_derives_structured_message_fields(sql, fields))
        self.assertFalse(
            _derives_structured_message_fields(
                sql.replace(
                    "json_extract(m.structured_payload_json, '$.delivery_status')",
                    "'bounced'",
                ),
                fields,
            )
        )

    def test_structured_peer_import_accepts_direct_message_scalar_subqueries(self):
        fields = {"recipient", "delivery_status", "provider_message_id", "sent_at"}
        latest_payload = (
            "(SELECT structured_payload_json FROM __messages "
            "WHERE is_outbound=0 AND structured_payload_json IS NOT NULL "
            "ORDER BY seq DESC LIMIT 1)"
        )
        sql = (
            "UPDATE outreach_threads SET "
            f"state=json_extract({latest_payload},'$.delivery_status'), "
            f"provider_message_id=json_extract({latest_payload},'$.provider_message_id'), "
            f"sent_at=json_extract({latest_payload},'$.sent_at') "
            f"WHERE recipient=json_extract({latest_payload},'$.recipient')"
        )

        self.assertTrue(_derives_structured_message_fields(sql, fields))
        self.assertFalse(
            _derives_structured_message_fields(
                sql.replace(
                    f"state=json_extract({latest_payload},'$.delivery_status')",
                    "state='bounced'",
                ),
                fields,
            )
        )

        cte_sql = (
            "WITH inbound AS (SELECT message_id, structured_payload_json FROM __messages "
            "WHERE is_outbound=0 ORDER BY seq DESC LIMIT 1) "
            "UPDATE outreach_threads SET "
            "state=json_extract((SELECT structured_payload_json FROM inbound),'$.delivery_status'), "
            "provider_message_id=json_extract((SELECT structured_payload_json FROM inbound),'$.provider_message_id'), "
            "sent_at=json_extract((SELECT structured_payload_json FROM inbound),'$.sent_at') "
            "WHERE recipient=json_extract((SELECT structured_payload_json FROM inbound),'$.recipient')"
        )
        self.assertTrue(_derives_structured_message_fields(cte_sql, fields))
        self.assertFalse(
            _derives_structured_message_fields(
                cte_sql.replace("FROM __messages", "FROM unrelated_payloads"),
                fields,
            )
        )

    def test_bound_structured_peer_import_derives_every_field(self):
        payload = {
            "recipient": "jordan@northstar.example.test",
            "delivery_status": "bounced",
            "provider_message_id": "provider-message-998",
            "sent_at": "2026-07-30T14:12:09Z",
        }
        sql = (
            "WITH outcome AS (SELECT "
            "json_extract(:payload,'$.recipient') AS recipient, "
            "json_extract(:payload,'$.delivery_status') AS delivery_status, "
            "json_extract(:payload,'$.provider_message_id') AS provider_message_id, "
            "json_extract(:payload,'$.sent_at') AS sent_at) "
            "UPDATE outreach_threads SET "
            "state=outcome.delivery_status, "
            "provider_message_id=outcome.provider_message_id, sent_at=outcome.sent_at "
            "FROM outcome\nWHERE outreach_threads.recipient=outcome.recipient"
        )
        call = _sqlite_call(sql)
        call.tool_params["bindings"] = {"payload": json.dumps(payload)}

        self.assertTrue(_derives_bound_structured_message_fields(call, sql, payload))
        self.assertFalse(
            _derives_bound_structured_message_fields(
                call,
                sql.replace(
                    "state=outcome.delivery_status",
                    "state=CASE WHEN outcome.delivery_status='bounced' THEN 'bounced' "
                    "ELSE outcome.delivery_status END",
                ),
                payload,
            )
        )

    def test_bound_operational_event_payload_is_grounded_without_scalar_copying(self):
        payload = {
            "event_id": "evt-2048",
            "event_type": "accepted_setup",
            "thread_key": "thread-2048",
            "occurred_at": "2026-07-28T15:42:00Z",
        }
        sql = (
            "INSERT INTO operational_events "
            "(event_id, event_type, thread_key, occurred_at, source_message_id) "
            "VALUES (json_extract(:source_payload,'$.event_id'), "
            "json_extract(:source_payload,'$.event_type'), "
            "json_extract(:source_payload,'$.thread_key'), "
            "json_extract(:source_payload,'$.occurred_at'), :source_message_id)"
        )
        call = _sqlite_call(sql)
        call.tool_params["bindings"] = {
            "source_payload": payload,
            "source_message_id": "message-2048",
        }

        self.assertEqual(
            _bound_json_payload_placeholder(call, sql, payload),
            ":source_payload",
        )
        self.assertIsNone(
            _bound_json_payload_placeholder(
                call,
                sql.replace(
                    "json_extract(:source_payload,'$.occurred_at')",
                    "'2026-07-28T15:42:00Z'",
                ),
                payload,
            )
        )
        scalar_copy_sql = sql.replace(
            "json_extract(:source_payload,'$.event_type')",
            ":event_type",
        )
        call.tool_params["bindings"]["event_type"] = "accepted_setup"
        self.assertIsNone(
            _bound_json_payload_placeholder(call, scalar_copy_sql, payload)
        )
        unused_extracts = (
            "WITH inspected AS (SELECT "
            "json_extract(:source_payload,'$.event_id'), "
            "json_extract(:source_payload,'$.event_type'), "
            "json_extract(:source_payload,'$.thread_key'), "
            "json_extract(:source_payload,'$.occurred_at')) "
            "INSERT INTO operational_events "
            "(event_id, event_type, thread_key, occurred_at, source_message_id) "
            "VALUES (:event_id, :event_type, :thread_key, :occurred_at, :source_message_id)"
        )
        call.tool_params["bindings"].update(payload)
        self.assertIsNone(
            _bound_json_payload_placeholder(call, unused_extracts, payload)
        )
        literal_copy = unused_extracts.replace(":event_id", "'evt-2048'")
        self.assertIsNone(
            _bound_json_payload_placeholder(call, literal_copy, payload)
        )
        self.assertTrue(
            _insert_values_derive_bound_payload_fields(
                sql,
                table_name="operational_events",
                placeholder=":source_payload",
                expected_fields=set(payload),
            )
        )
        self.assertTrue(
            _insert_values_derive_bound_payload_fields(
                sql.replace(
                    "INSERT INTO operational_events",
                    'INSERT OR IGNORE INTO "operational_events"',
                ),
                table_name="operational_events",
                placeholder=":source_payload",
                expected_fields=set(payload),
            )
        )
        select_sql = sql.replace(
            "VALUES (json_extract(:source_payload,'$.event_id'), "
            "json_extract(:source_payload,'$.event_type'), "
            "json_extract(:source_payload,'$.thread_key'), "
            "json_extract(:source_payload,'$.occurred_at'), :source_message_id)",
            "SELECT json_extract(:source_payload,'$.event_id'), "
            "json_extract(:source_payload,'$.event_type'), "
            "json_extract(:source_payload,'$.thread_key'), "
            "json_extract(:source_payload,'$.occurred_at'), :source_message_id "
            "WHERE 1=1",
        )
        self.assertTrue(
            _insert_values_derive_bound_payload_fields(
                select_sql,
                table_name="operational_events",
                placeholder=":source_payload",
                expected_fields=set(payload),
            )
        )
        self.assertFalse(
            _insert_values_derive_bound_payload_fields(
                sql.replace(
                    "json_extract(:source_payload,'$.event_type')",
                    "lower(json_extract(:source_payload,'$.event_type'))",
                ),
                table_name="operational_events",
                placeholder=":source_payload",
                expected_fields=set(payload),
            )
        )

    def test_mutation_target_ignores_update_words_in_leading_comments(self):
        self.assertEqual(
            _mutation_target_table(
                "-- update jordan's outcome\n"
                "UPDATE outreach_threads SET state='bounced' WHERE recipient='jordan@example.test'"
            ),
            "outreach_threads",
        )

    def test_prompt_does_not_teach_the_sql_solution(self):
        prompt = SqliteSourceArrayFirstWriteScenario.prompt.casefold()

        for leaked_term in ("sqlite", "__tool_results", "json_each", "insert", "select", "table"):
            self.assertNotIn(leaked_term, prompt)

    def test_catalog_case_rejects_every_single_result_import_filter(self):
        self.assertEqual(SqliteIntermediateWorkingTableScenario.max_single_result_filters, 0)

    def test_sqlite_attempt_scorer_rejects_success_with_query_advisory(self):
        call = _sqlite_call(
            self.clean_sql,
            result=json.dumps({
                "status": "ok",
                "results": [{"result": [{"release_id": "rel-search-18"}]}],
                "advisories": [{
                    "code": "tool_result_row_loop",
                    "message": "Use one shaped query over every sibling.",
                }],
            }),
        )

        self.assertEqual(
            _sqlite_attempt_failures([call]),
            ["SQLite attempt 1 returned a query advisory"],
        )

    def test_catalog_case_rejects_repeated_same_table_imports_without_result_ids(self):
        repeated = _repeated_source_import_tables([
            """
            INSERT INTO plans SELECT json_extract(item.value, '$.name')
            FROM __tool_results, json_each(result_json, '$.content.plans') item
            WHERE result_json LIKE '%AxonFlow%';
            INSERT INTO plans SELECT json_extract(item.value, '$.name')
            FROM __tool_results, json_each(result_json, '$.content.plans') item
            WHERE result_json LIKE '%CareMesh%';
            """
        ])
        aggregate = _repeated_source_import_tables([self.clean_sql])

        self.assertEqual(repeated, ("plans",))
        self.assertEqual(aggregate, ())

    def test_incremental_domain_model_case_is_registered_without_teaching_sql(self):
        suite = SuiteRegistry.get(SQLITE_TOOL_RESULT_SUITE_SLUG)
        scenario = ScenarioRegistry.get(SQLITE_INCREMENTAL_DOMAIN_MODEL)

        self.assertIsNotNone(scenario)
        self.assertIn(SQLITE_INCREMENTAL_DOMAIN_MODEL, SQLITE_TOOL_RESULT_SCENARIO_SLUGS)
        self.assertIn(SQLITE_INCREMENTAL_DOMAIN_MODEL, suite.scenario_slugs)
        self.assertEqual(
            [task.name for task in scenario.tasks],
            [
                "inject_prompt",
                "verify_incremental_domain_model",
                "verify_operating_answer",
            ],
        )
        prompt = SqliteIncrementalDomainModelScenario.prompt.casefold()
        for leaked_term in ("sqlite", "__tool_results", "json_each", "insert", "select", "table"):
            self.assertNotIn(leaked_term, prompt)

    def test_pressure_refresh_case_is_registered_without_teaching_sql(self):
        suite = SuiteRegistry.get(SQLITE_TOOL_RESULT_SUITE_SLUG)
        scenario = ScenarioRegistry.get(SQLITE_ENRICHMENT_REFRESH_UNDER_PRESSURE)

        self.assertIsNotNone(scenario)
        self.assertIn(SQLITE_ENRICHMENT_REFRESH_UNDER_PRESSURE, SQLITE_TOOL_RESULT_SCENARIO_SLUGS)
        self.assertIn(SQLITE_ENRICHMENT_REFRESH_UNDER_PRESSURE, suite.scenario_slugs)
        self.assertEqual(
            [task.name for task in scenario.tasks],
            [
                "inject_prompt",
                "verify_pressure_refresh",
                "verify_missing_contact_answer",
            ],
        )
        prompt = SqliteEnrichmentRefreshUnderPressureScenario.prompt.casefold()
        for leaked_term in ("sqlite", "__tool_results", "json_each", "insert", "select", "table"):
            self.assertNotIn(leaked_term, prompt)

    def test_sibling_result_set_case_is_registered_without_teaching_sql(self):
        suite = SuiteRegistry.get(SQLITE_TOOL_RESULT_SUITE_SLUG)
        scenario = ScenarioRegistry.get(SQLITE_SIBLING_RESULT_SET_FIRST_WRITE)

        self.assertIsNotNone(scenario)
        self.assertIn(SQLITE_SIBLING_RESULT_SET_FIRST_WRITE, SQLITE_TOOL_RESULT_SCENARIO_SLUGS)
        self.assertIn(SQLITE_SIBLING_RESULT_SET_FIRST_WRITE, suite.scenario_slugs)
        self.assertEqual(
            [task.name for task in scenario.tasks],
            [
                "inject_prompt",
                "verify_first_shaped_model_write",
                "verify_segment_answer",
            ],
        )
        prompt = SqliteSiblingResultSetFirstWriteScenario.prompt.casefold()
        for leaked_term in ("sqlite", "__tool_results", "json_each", "insert", "select", "table"):
            self.assertNotIn(leaked_term, prompt)

    def test_unstructured_binding_case_is_registered_without_teaching_sql(self):
        suite = SuiteRegistry.get(SQLITE_TOOL_RESULT_SUITE_SLUG)
        scenario = ScenarioRegistry.get(SQLITE_UNSTRUCTURED_BINDINGS_FIRST_WRITE)

        self.assertIsNotNone(scenario)
        self.assertIn(SQLITE_UNSTRUCTURED_BINDINGS_FIRST_WRITE, SQLITE_TOOL_RESULT_SCENARIO_SLUGS)
        self.assertIn(SQLITE_UNSTRUCTURED_BINDINGS_FIRST_WRITE, suite.scenario_slugs)
        self.assertEqual(
            [task.name for task in scenario.tasks],
            [
                "inject_prompt",
                "verify_bound_model_write",
                "verify_evidence_answer",
            ],
        )
        prompt = SqliteUnstructuredBindingsFirstWriteScenario.prompt.casefold()
        for leaked_term in ("sqlite", "__tool_results", "json_each", "insert", "select", "table", "bindings"):
            self.assertNotIn(leaked_term, prompt)

    def test_existing_item_report_accepts_a_queried_source_model(self):
        summary = summarize_sqlite_tool_result_sql([
            "CREATE TABLE vehicles(vin TEXT PRIMARY KEY, price INTEGER);"
            "INSERT INTO vehicles SELECT json_extract(value,'$.vin'),json_extract(value,'$.price') "
            "FROM __tool_results,json_each(result_json,'$.content.vehicles') WHERE result_id='feed-a';"
            "INSERT INTO vehicles SELECT json_extract(value,'$.vin'),json_extract(value,'$.price') "
            "FROM __tool_results,json_each(result_json,'$.content.vehicles') WHERE result_id='feed-b';"
            "SELECT vin,price FROM vehicles ORDER BY price;"
        ])

        self.assertTrue(_uses_queryable_source_model(summary))
        unkeyed = summarize_sqlite_tool_result_sql([
            "CREATE TABLE vehicles(vin TEXT, price INTEGER);"
            "INSERT INTO vehicles SELECT json_extract(value,'$.vin'),json_extract(value,'$.price') "
            "FROM __tool_results,json_each(result_json,'$.content.vehicles') WHERE result_id='feed-a';"
            "SELECT vin,price FROM vehicles ORDER BY price;"
        ])
        self.assertFalse(_uses_queryable_source_model(unkeyed))

    def test_first_write_scorer_accepts_direct_source_array_import(self):
        failures = _source_array_first_write_failures(
            [_sqlite_call(self.clean_sql)],
            "release_events",
        )

        self.assertEqual(failures, [])

    def test_first_write_scorer_rejects_literal_rows_and_recovery_loops(self):
        literal_sql = """
            CREATE TABLE release_events (release_id TEXT PRIMARY KEY, service TEXT);
            INSERT INTO release_events VALUES ('rel-search-18', 'Search index');
            SELECT * FROM release_events;
        """
        literal_failures = _source_array_first_write_failures(
            [_sqlite_call(literal_sql)],
            "release_events",
        )
        recovery_failures = _source_array_first_write_failures(
            [
                _sqlite_call(
                    "INSERT INTO release_events VALUES ('rel-search-18', 'Search index')",
                    status="error",
                    result=json.dumps({"status": "error", "message": "Query not executed: copied rows"}),
                ),
                _sqlite_call(self.clean_sql),
            ],
            "release_events",
        )

        self.assertIn(
            "first release write did not derive array rows directly from __tool_results",
            literal_failures,
        )
        self.assertIn("expected one first-shot SQLite batch, found 2 attempts", recovery_failures)
