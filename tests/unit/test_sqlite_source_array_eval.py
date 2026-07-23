import json
from types import SimpleNamespace

from django.test import SimpleTestCase, tag

import api.evals.loader  # noqa: F401 - registers scenarios and suites
from api.agent.tools.sqlite_query_quality import summarize_sqlite_tool_result_sql
from api.evals.registry import ScenarioRegistry
from api.evals.scenarios.sqlite_tool_results import (
    SQLITE_INCREMENTAL_DOMAIN_MODEL,
    SQLITE_SOURCE_ARRAY_FIRST_WRITE,
    SQLITE_TOOL_RESULT_SCENARIO_SLUGS,
    SQLITE_TOOL_RESULT_SUITE_SLUG,
    SqliteIncrementalDomainModelScenario,
    SqliteSourceArrayFirstWriteScenario,
    _source_array_first_write_failures,
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

    def test_prompt_does_not_teach_the_sql_solution(self):
        prompt = SqliteSourceArrayFirstWriteScenario.prompt.casefold()

        for leaked_term in ("sqlite", "__tool_results", "json_each", "insert", "select", "table"):
            self.assertNotIn(leaked_term, prompt)

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
