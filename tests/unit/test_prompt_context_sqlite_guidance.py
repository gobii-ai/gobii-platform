from django.test import SimpleTestCase, tag

from api.agent.core import prompt_context


@tag("batch_promptree")
class PromptContextSqliteGuidanceTests(SimpleTestCase):
    def test_tool_results_schema_mentions_result_text(self):
        examples = prompt_context._get_sqlite_examples()
        section = examples.split("# __tool_results (special table)", 1)[1].split(
            "# JSON: path from hint", 1
        )[0]
        self.assertIn("result_text", section)
        self.assertIn("analysis_json", section)
        self.assertIn("json_extract(result_json,'$.result')", section)
        self.assertIn("do not invent columns", section)

    def test_examples_require_sqlite_tool_for_database_queries(self):
        examples = prompt_context._get_sqlite_examples()
        self.assertIn("User asks to query SQLite/database/tables", examples)
        self.assertIn("schema proves shape, not result data", examples)

    def test_csv_parsing_requires_inspection_and_result_text(self):
        examples = prompt_context._get_sqlite_examples()
        csv_section = examples.split("## CSV Parsing", 1)[1].split(
            "## Data Cleaning Functions", 1
        )[0]
        self.assertIn("inspect before parsing", csv_section)
        self.assertIn("result_text", csv_section)
        self.assertIn("path_from_hint", csv_section)

    def test_examples_include_messages_table_schema(self):
        examples = prompt_context._get_sqlite_examples()
        self.assertIn("# __messages (special table)", examples)
        self.assertIn("attachment_paths_json", examples)
        self.assertIn("rejected_attachments_json", examples)
        self.assertIn("latest_status", examples)

    def test_examples_include_files_table_schema(self):
        examples = prompt_context._get_sqlite_examples()
        self.assertIn("# __files (special table; metadata only)", examples)
        self.assertIn("recent_files", examples)
        self.assertIn("metadata only", examples)

    def test_examples_discourage_browser_task_completion_polling(self):
        examples = prompt_context._get_sqlite_examples()
        self.assertIn("completed browser task wakes you and adds a `spawn_web_task_result` row", examples)
        self.assertIn("Don't poll __tool_results/__files waiting for browser task completion before that wake-up", examples)

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

    def test_examples_show_larger_grep_context_window(self):
        examples = prompt_context._get_sqlite_examples()
        self.assertIn("grep_context_all(\n        json_extract(result_json,'$.excerpt'), '<pattern>', 120, 12)", examples)
        self.assertIn("try wider context (200 chars)", examples)

    def test_examples_prefer_shaped_multi_result_queries(self):
        examples = prompt_context._get_sqlite_examples()
        self.assertIn("multiple_results", examples)
        self.assertIn("working_table", examples)
        self.assertIn("avoid → one result_text fetch per source", examples)

    def test_sqlite_retry_warning_flags_blob_fetch_loops(self):
        warning = prompt_context._build_sqlite_retry_warning(
            [
                ({"sql": "SELECT result_text FROM __tool_results WHERE result_id='a1'"}, "{}"),
                ({"sql": "SELECT result_text FROM __tool_results WHERE result_id='b2'"}, "{}"),
            ]
        )

        self.assertIn("SQLite efficiency warning", warning)
        self.assertIn("one shaped query", warning)

    def test_examples_prefer_patch_and_retry_for_named_missing_parameters(self):
        examples = prompt_context._get_sqlite_examples()
        self.assertIn(
            "If an API/tool error explicitly names a missing parameter, patch that parameter and retry before broad search unless the error is ambiguous",
            examples,
        )

    def test_planning_first_run_welcome_ends_planning_before_execution_tools(self):
        guidance = prompt_context._get_planning_first_run_welcome_instruction(
            welcome_target=prompt_context._FirstRunWelcomeTarget(
                channel="web",
                address="web://user/1/agent/test",
                send_tool_name="send_chat_message",
            )
        )

        self.assertIn("call the welcome send tool and end_planning in the same response", guidance)
        self.assertIn("Do not call http_request", guidance)
